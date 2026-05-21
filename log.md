# Session Log

## Challenge Interpretation

The challenge is a backend architecture and systems design exercise, not an implementation task.

This conclusion comes from `Challenge.md`, which explicitly says the candidate deliverables are:

> Provide a written response (2-8 pages) plus diagrams.

It does not ask for a runnable Django app, migrations, implemented endpoints, or tests. The mandatory sections are architecture, ERD, field-level data model, API design, rollout plan, and non-functional considerations.

## Recommended Approach

Approach the challenge as the design of a new Stock & Materials Management module for an existing Django microservice platform.

The strongest answer should show the ability to design an auditable inventory system that is correct under concurrency, integrates cleanly with existing microservices, and can evolve beyond the MVP.

Use a dedicated `stock-management` Django/DRF service. It should own catalogue items, stock containers, immutable stock movements, current balances, stock takes, import jobs, reorder policies, and optional valuation layers.

Do not make WorkOrders or Locations part of this service's transactional ownership. Store external references such as `work_order_id`, `location_id`, and `operative_id`, and optionally maintain local read models populated from SNS events. This keeps service boundaries clean while still allowing validation and reporting.

## Core Design Choice

Use a hybrid inventory model:

- Immutable `stock_movement` and `stock_movement_line` ledger as the source of truth.
- Persisted `stock_balance` table for fast current lookups.
- Optional snapshots or materialized views for historical reporting and "stock at time T".
- Transactional updates where every stock-changing API writes ledger rows and updates balances atomically.

This directly addresses the brief's pressure points: concurrency, auditability, mobile UX, historical queries, and future reserved stock.

## Main Entities

The model should roughly include:

- `sku` / `material_item`: catalogue item, unit type, tracking method, minimum increment.
- `stock_container`: van, warehouse, workshop, later locker/bin/etc.
- `stock_balance`: current quantity per `container + sku`, later `on_hand`, `reserved`, `available`.
- `stock_movement`: header for receive, usage, transfer, adjustment, stocktake, initial load.
- `stock_movement_line`: individual SKU quantity deltas.
- `idempotency_record`: safe retries from mobile and APIs.
- `stock_take`: audit session for a container.
- `stock_take_line`: counted quantity versus expected quantity.
- `purchase_order` / `reorder_request`: probably shallow in MVP unless procurement lifecycle is in scope.
- `cost_layer`: stretch for FIFO/LIFO valuation.

The important invariant is that all quantity changes happen through movements. Nobody directly edits balances except inside the same transaction that records the movement.

## Concurrency Strategy

For stock-changing operations, use a PostgreSQL transaction and lock affected balance rows with `SELECT ... FOR UPDATE`.

For example, recording work order usage should:

1. Validate the work order reference, container assignment, SKU, quantity, and increment.
2. Resolve or create balance rows for the affected `container + sku`.
3. Lock those rows.
4. Check available quantity against negative stock tolerance.
5. Insert immutable movement records.
6. Update `stock_balance.on_hand`.
7. Commit.
8. Publish an outbox event asynchronously.

This is simple and defensible for hundreds of concurrent users and thousands of movements per day. It does not require exotic distributed locking.

## API Shape

Use REST because the prompt allows REST or RPC and DRF is already in the stack.

Core endpoints:

```text
POST /stock-usages
POST /stock-receipts
POST /stock-transfers
POST /stock-adjustments
POST /stock-takes
POST /imports/initial-stock
GET  /stock-levels?sku_id=&container_id=
GET  /containers/{id}/stock-levels
GET  /containers/{id}/stock-levels/history?at=
```

Every mutating endpoint should accept an `Idempotency-Key` header. For mobile retries, returning the original response for the same key is much better than hoping clients retry safely.

Conflict semantics should be explicit:

- `400` for invalid quantity, increment, unknown SKU, or bad unit.
- `404` for unknown container or external reference if synchronously validated.
- `409` for insufficient stock, stale stocktake version, duplicate transfer completion, or business-rule conflict.
- `202` for async imports.
- `422` for import validation failures once parsed.

## Day-0 Import

Day-0 population is mandatory and should be treated as a first-class flow.

Recommended flow:

1. Upload CSV/API payload containing catalogue, containers, balances, and optional cost layers.
2. Run async validation with a dry-run report.
3. Require manager approval.
4. Apply import as `INITIAL_LOAD` stock movements.
5. Produce reconciliation/audit report.
6. Block or flag duplicate initial loads per client/environment.

Day-0 quantities should not be inserted directly into `stock_balance`. They should enter through the same ledger path as every other stock mutation.

## Suggested Written Response Structure

The final deliverable should likely be a `Solution.md` or PDF containing:

1. Qualifying questions and assumptions: external service consistency, offline mobile, negative tolerance, valuation needs, procurement ownership.
2. Proposed architecture: new service, external references, SNS/outbox, Celery jobs, transaction boundaries.
3. Data model: ERD first, then field-level table descriptions.
4. Ledger versus balances trade-off: explain why hybrid is the pragmatic choice.
5. API design: request/response examples, validation, idempotency, errors.
6. Migration and rollout: feature flags, schema migrations, pilot client, monitoring.
7. Non-functionals: concurrency, indexes, partitioning, observability, reliability.
8. MVP versus v2: defer reservations, rich procurement, advanced valuation, but leave schema paths open.

## What To Deliver

Deliver a document, likely Markdown or PDF, containing:

1. Qualifying questions + assumptions.
2. Proposed architecture.
3. Data model package.
4. ERD diagram, Mermaid is acceptable.
5. Field-level table descriptions.
6. Ledger vs persisted balances vs snapshots trade-off.
7. API design.
8. Migration and rollout plan.
9. Non-functional considerations.
10. MVP to v2 phase plan, optional but useful.

No code is required unless including illustrative snippets such as example JSON requests or Mermaid diagrams.

The answer should optimize for defensible trade-offs. The interviewer is likely looking for whether the candidate understands inventory systems are ledger and audit problems first, CRUD problems second.

## Application-Level Ledger / WAL Clarification

An application-level append-only log is a good fit for stock movement, stock balance reconstruction, auditability, and replay. However, it should not be presented as a replacement for PostgreSQL transactions.

It is better to avoid calling it a "WAL" in the final answer because write-ahead logging has a specific database-internals meaning. Better terms are:

- `stock_movement_ledger`
- `inventory_ledger`
- `append-only movement log`
- `event-sourced stock ledger`

The recommended write path is:

```text
POST /stock-usages
  -> transaction starts
  -> lock affected stock_balance rows
  -> validate no negative stock beyond tolerance
  -> append stock_movement + stock_movement_line records
  -> update stock_balance projection
  -> append outbox_event
  -> commit
```

Example usage movement:

```json
{
  "type": "USAGE",
  "external_ref": "WO-123",
  "container_id": "VAN-1",
  "lines": [
    {
      "sku_id": "SCREW-001",
      "quantity_delta": "-3",
      "unit": "each"
    }
  ]
}
```

The corresponding `stock_balance` row is the current projection:

```text
container_id = VAN-1
sku_id       = SCREW-001
on_hand      = previous_on_hand - 3
```

The key invariant is:

> `stock_balance` must always be derivable from the movement ledger.

This gives auditability, replay, stock-at-time-T queries, reconciliation, and recovery from projection corruption. PostgreSQL transactions still protect correctness during each write.

Suggested wording for the final solution:

> I would model inventory mutations as an append-only stock movement ledger. Each receive, usage, transfer, adjustment, stocktake correction, and initial import creates immutable ledger rows. Current balances are maintained as a transactional projection for low-latency reads, but can be rebuilt from the ledger. This gives auditability and replayability without sacrificing PostgreSQL transaction guarantees for concurrent writes.

## Durable Storage vs Queue Events

Stock movement events should be stored permanently in PostgreSQL. They should not only be consumed from a queue.

For this challenge, `stock_movement` and `stock_movement_line` tables are the permanent append-only inventory ledger. They are part of the domain model and audit trail. RabbitMQ, Celery, and SNS are for asynchronous processing and integration, not for authoritative inventory history.

Recommended flow:

```text
API request
  -> DB transaction
      -> insert stock_movement rows permanently
      -> update stock_balance projection
      -> insert outbox_event row
  -> commit
  -> async worker publishes outbox_event to SNS/RabbitMQ
  -> consumers build read models / notify other services
```

Reasons to store movements durably in the database:

- Queues usually have retention limits and operational cleanup.
- Consumers can fail, retry, duplicate, or process out of order.
- Audit history is required for stock takes, adjustments, disputes, and reporting.
- `stock_balance` must be rebuildable if a projection is corrupted.
- "Stock level at time T" requires durable history.
- Transactional consistency is needed between the movement and the balance.

Important distinction:

```text
stock_movement = source of truth
stock_balance  = current-state projection
queue event    = integration/delivery mechanism
```

In the final design, movement rows should be immutable and retained long-term, with possible partitioning or archival later if volume grows.
