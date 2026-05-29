# Global Ordered Queue for Stock Mutations

## Summary

This explores using a queue-like command path for all stock-changing operations: usage, receipts/topups, transfers, adjustments, stock takes, returns, and day-0 initial loads.

The goal is to make stock mutations process in a deterministic global order. The queue serializes command processing, but PostgreSQL remains the durable source of truth for commands, ledger entries, balances, and idempotency.

## Core Design

Add a durable `stock_command` concept before the ledger:

1. API receives a stock-changing request.
2. API validates basic request shape, permissions, idempotency key, and referenced resources where possible.
3. API writes a `PENDING` command with a monotonically increasing `sequence_number`.
4. API returns `202 Accepted` with `command_id`.
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

return `202 Accepted` instead of immediately returning a posted movement.

Example response:

```json
{
  "command_id": "cmd-123",
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
  "command_id": "cmd-123",
  "status": "POSTED",
  "sequence_number": 1042,
  "ledger_id": "ledger-789"
}
```

Example rejected command:

```json
{
  "command_id": "cmd-124",
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
- Provides a clear pending/posted/rejected lifecycle for mobile clients.
- Durable command history helps debug retries, timeouts, and delayed processing.
- Worker can safely retry infrastructure failures without duplicating ledger entries.

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
