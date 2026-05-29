# Global Ordered Queue for Stock Mutations

## Summary

This explores using a queue-like command path for all stock-changing operations: usage, receipts/topups, transfers, adjustments, stock takes, returns, and day-0 initial loads.

The goal is to make stock mutations process in a deterministic global order. The queue serializes command processing, but PostgreSQL remains the durable source of truth for commands, ledger entries, balances, and idempotency.

## Core Design

Add a durable `stock_command` concept before the ledger:

1. API receives a stock-changing request.
2. API validates basic request shape, permissions, idempotency key, and referenced resources where possible.
3. API writes a `PENDING` command with a monotonically increasing `sequence_number`.
4. API returns `202 Accepted` with a UUID `command_id`.
5. A single active worker processes commands in `sequence_number` order.
6. Worker posts the double-entry `stock_ledger` entry and updates `stock_balance` inside one PostgreSQL transaction.
7. Worker marks the command `POSTED` or `REJECTED`.

RabbitMQ/Celery can be used as a wake-up and processing mechanism, but not as the authoritative ordered log.

The database command table defines order. RabbitMQ delivery order should not be trusted for correctness because retries, redeliveries, multiple consumers, and worker failures can complicate ordering.

## Write Path

```text
POST /stock-usage-records
  -> validate request shape and permissions
  -> check Idempotency-Key
  -> insert stock_command(status=PENDING, sequence_number=N)
  -> enqueue/wake worker
  -> return 202 Accepted with command_id

worker
  -> claim next PENDING stock_command by sequence_number
  -> open PostgreSQL transaction
  -> validate business rules against current stock_balance
  -> lock affected real-container stock_balance rows
  -> create balanced stock_ledger + stock_ledger_line rows
  -> update stock_balance projection
  -> mark stock_command POSTED with ledger_id
  -> insert outbox_event
  -> commit
```

If business validation fails, for example insufficient stock, the worker marks the command `REJECTED` with a structured reason and continues to the next command.

## Command Statuses

| Status | Meaning |
| --- | --- |
| `PENDING` | Accepted but not processed. |
| `PROCESSING` | Claimed by a worker. |
| `POSTED` | Ledger entry created and balance updated successfully. |
| `REJECTED` | Business validation failed, for example insufficient stock. |
| `FAILED_RETRYABLE` | Infrastructure failure; worker can retry. |

## API Implications

Mutating endpoints such as:

```text
POST /stock-usage-records
POST /stock-receipts
POST /stock-transfers
POST /stock-adjustments
POST /stock-takes
POST /import-jobs
```

return `202 Accepted` instead of immediately returning a posted movement. The response always includes a UUID `command_id`, which becomes the client-visible tracking handle for polling, retries, and UI state.

Example response:

```json
{
  "command_id": "4f4a2c32-5f53-4d56-9dc6-0df510ddf178",
  "status": "PENDING",
  "sequence_number": 1042
}
```

Add command status endpoints:

```text
GET /stock-commands/{command_id}
GET /stock-commands?status=&from=&to=
```

Example posted command:

```json
{
  "command_id": "4f4a2c32-5f53-4d56-9dc6-0df510ddf178",
  "status": "POSTED",
  "sequence_number": 1042,
  "ledger_id": "8a758c35-b3cf-4d94-9d6e-059c4b7ad0b7"
}
```

Example rejected command:

```json
{
  "command_id": "2e193588-f4cb-4713-911e-0d77b061e999",
  "status": "REJECTED",
  "sequence_number": 1043,
  "rejection": {
    "code": "INSUFFICIENT_STOCK",
    "message": "Requested quantity would reduce stock below tolerance.",
    "sku_id": "screw-001",
    "container_id": "van-1"
  }
}
```

Current stock reads still use `stock_balance`, but clients must understand that recently submitted commands may still be pending.

## Idempotency

Idempotency is handled at command creation.

- Same `Idempotency-Key` and same request returns the existing `command_id`.
- Same `Idempotency-Key` with a different request returns `409 IDEMPOTENCY_KEY_CONFLICT`.
- The idempotency row and `stock_command` row are committed together.
- Retries do not create duplicate commands.

## Ordering Guarantees

The selected model is global ordering:

```text
command 1042 must be processed before command 1043
command 1043 must be processed before command 1044
```

This gives simple semantics: if command B has a higher `sequence_number` than command A, B cannot post before A.

The database should enforce or support this with:

- Monotonic `sequence_number`, usually from a database sequence.
- Worker query that claims the lowest eligible `PENDING` command.
- Single active processor for stock commands, or leader-election/advisory-lock protection if multiple workers are running.
- Recovery process for commands stuck in `PROCESSING`.

## Interaction With Ledger and Balances

The command queue does not replace the stock ledger.

```text
stock_command = accepted intent to mutate stock
stock_ledger  = immutable posted accounting record
stock_balance = current physical stock projection
outbox_event  = integration event after commit
```

Only `POSTED` commands produce ledger entries. `REJECTED` commands remain useful audit records of attempted operations, but do not affect stock.

The worker still uses a PostgreSQL transaction when posting a command:

- Lock affected real-container `stock_balance` rows.
- Validate availability and tolerance.
- Insert balanced double-entry ledger lines.
- Update physical balances.
- Mark command as `POSTED`.
- Insert outbox event.

## Benefits

- Simple global ordering semantics.
- Avoids most concurrent stock-write conflicts because only one stock command posts at a time.
- Works well for unreliable mobile clients because submitted stock mutations become durable server-side commands.
- Provides a clear pending/posted/rejected lifecycle for mobile clients.
- Durable command history helps debug retries, timeouts, and delayed processing.
- Worker can safely retry infrastructure failures without duplicating ledger entries.

## Unreliable Mobile Clients

The queue approach is useful for unreliable mobile clients because the API can accept the user's intent durably and return a `command_id` immediately.

```json
{
  "command_id": "4f4a2c32-5f53-4d56-9dc6-0df510ddf178",
  "status": "PENDING"
}
```

The mobile app can then safely retry, poll, and reconcile later.

Benefits:

- The app does not need to keep a request open while stock is posted.
- If the connection drops after submit, retrying with the same `Idempotency-Key` returns the same `command_id`.
- The user can see pending operations locally.
- The server later marks the command `POSTED` or `REJECTED`.
- If stock is insufficient by the time the command is processed, the app receives a structured rejection instead of silent inconsistency.
- The model supports offline-ish behavior: the app can queue local drafts, then submit them when connectivity returns.

Important caveat:

The server-side queue helps after the request reaches the server. It does not solve the case where the mobile app is fully offline and cannot submit at all. For that, the mobile app still needs a local outbox.

```text
mobile local draft/outbox
  -> submit when online with Idempotency-Key
  -> server creates stock_command
  -> app polls command_id
  -> command becomes POSTED or REJECTED
```

This means the queue should be paired with mobile UI states for `PENDING`, `POSTED`, and `REJECTED` operations.

## Mobile Local Outbox

The mobile local outbox is the client-side companion to the server-side queue. It handles the case where the operative is fully offline or the app crashes before the request reaches the backend.

A local outbox item should contain:

- `local_operation_id`: client-side UUID for the local action.
- `idempotency_key`: generated once and reused for every retry.
- `operation_type`: for example `stock-usage-record`.
- `payload`: the request body to submit when online.
- `local_status`: `DRAFT`, `READY_TO_SYNC`, `SUBMITTING`, `SERVER_PENDING`, `POSTED`, `REJECTED`, or `FAILED_RETRYABLE`.
- `command_id`: server command UUID once accepted.
- `ledger_id`: posted ledger UUID once the command is `POSTED`.
- `rejection`: structured rejection details if the command is `REJECTED`.

### The flow:

- User records stock usage while offline.
- App persists local outbox item and idempotency key before any network attempt.
- When online, app submits with Idempotency-Key.
- Server returns command_id.
- App stores command_id and polls until POSTED or REJECTED.

```text
operative records stock usage offline
  -> app writes local outbox item with Idempotency-Key
  -> app submits when connectivity returns
  -> server creates stock_command and returns command_id
  -> app stores command_id and marks local item SERVER_PENDING
  -> app polls command status
  -> app marks local item POSTED or REJECTED
```

### Failure behavior:

- If the app crashes before submit, the local outbox item remains retryable.
- If the network drops after submit, retrying with the same `Idempotency-Key` returns the same `command_id`.
- If the server posts the command, the app stores `ledger_id` and marks the local item `POSTED`.
- If the server rejects the command, for example due to insufficient stock, the app surfaces a user-resolvable `REJECTED` state.
- If the same local operation is submitted twice, idempotency ensures only one server command is created.

## Tradeoffs

- Stock writes become eventually consistent from the client perspective.
- One global worker limits throughput.
- Worker outage pauses all stock mutations until recovered.
- Mobile UX must handle pending, posted, and rejected states.
- The queue cannot be the only source of truth; PostgreSQL still owns durable state.
- Global ordering is stricter than necessary for unrelated containers and may be overkill if per-container ordering would be enough.

## Failure Handling

- API timeout before response: client retries with the same `Idempotency-Key` and receives the existing `command_id`.
- Worker crash before transaction commit: command remains `PENDING` or can be recovered from stale `PROCESSING`.
- Worker crash after commit: command is already `POSTED`; retry must detect `ledger_id` and avoid reposting.
- Business failure: command becomes `REJECTED`; no ledger or balance mutation is created.
- Outbox publish failure: command remains `POSTED`; outbox retry publishes later.

## Test Scenarios

- Two usage commands for the same van/SKU are submitted concurrently; lower `sequence_number` posts first.
- A receipt/topup and a usage for the same SKU are submitted concurrently; final result follows sequence order.
- A command that would make stock negative is processed in order and becomes `REJECTED`; later commands continue.
- API retry with the same `Idempotency-Key` returns the same `command_id`.
- Same `Idempotency-Key` with a different request returns `409 IDEMPOTENCY_KEY_CONFLICT`.
- Worker crashes after marking `PROCESSING`; recovery safely retries or returns it to `PENDING`.
- Worker crashes after posting the ledger but before publishing outbox; retry does not create a duplicate ledger entry.
- Ledger/balance reconciliation proves balances are derivable from posted ledger entries only.

## Recommendation

This model is valid if the product accepts eventual consistency for stock writes and wants simple global ordering semantics.

For the challenge, present it as an optional variant or future evolution rather than the default design. The original synchronous transaction model with row locks is simpler and likely sufficient for hundreds of concurrent users and thousands of movements/day. A global command queue is easier to reason about but adds pending states, worker recovery logic, and throughput constraints.
