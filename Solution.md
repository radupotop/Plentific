# Stock and Materials Management Design

## 1. Questions and Assumptions

Questions to ask:

- Do operatives need to record usage offline, or can stock commits require connectivity?
- Can stock ever go negative operationally, for example because a van count is stale?
- Does procurement already exist elsewhere, or should this module own purchase orders?
- Are WorkOrder, Location, vehicle, and user identifiers globally unique across services?
- Is FIFO/LIFO cost valuation needed in MVP, or only later?
- Do stock adjustments, transfers, and stock takes require approval workflows?

Assumptions:

- The MVP focuses on quantity accuracy, auditability, and fast stock lookup.
- WorkOrders and Locations remain owned by existing services.
- This module stores external references and may keep local read-model stubs from SNS events.
- PostgreSQL is the source of truth; RabbitMQ/Celery/SNS are delivery mechanisms.
- Stock cannot go negative by default. A SKU-level or system-level tolerance can allow controlled exceptions.

Main risks:

- Offline mobile usage can create conflicts when devices sync later.
- Day-0 stock data may be incomplete or inaccurate.
- Historical reporting over raw ledger tables can degrade OLTP performance.
- Synchronous validation against external services can reduce stock-service availability.

## 2. Architecture

Introduce a dedicated Django/DRF service called `stock-management`.

It owns:

- SKUs, unit rules, and increment validation.
- Real stock containers such as vans, warehouses, workshops, lockers, and bins.
- Virtual accounting containers used only by the ledger.
- Immutable stock ledger entries and current stock balances.
- Stock takes, adjustments, transfers, receipts, work-order usage, day-0 imports, and reorder requests.
- Optional cost layers for FIFO/LIFO valuation.

It does not own WorkOrder, Location, user identity, vehicle assignment, or full procurement lifecycle. Those remain external services referenced by IDs such as `work_order_ref`, `location_ref`, `vehicle_ref`, and `user_ref`.

## 3. Core Model

Inventory changes are represented as an append-only double-entry ledger.

Every stock-changing operation creates a balanced `stock_ledger` entry:

- Receipts balance real stock against `SUPPLIER_SOURCE`.
- Work-order usage balances a van against `WORK_ORDER_CONSUMED`.
- Transfers balance one real container against another real container.
- Adjustments balance real stock against `ADJUSTMENT_GAIN` or `ADJUSTMENT_LOSS`.
- Day-0 imports balance real stock against `INITIAL_LOAD_SOURCE`.

Real stock is physical inventory that can be counted, transferred, reserved, or used by operatives. Virtual stock is not available inventory; it only explains where stock came from or went in the ledger.

Important invariants:

- `stock_balance` exists only for real containers.
- `stock_balance` must be derivable from `stock_ledger_line`.
- Every posted ledger transaction must balance to zero per SKU/unit across real and virtual containers.
- Posted ledger entries are immutable; corrections are new movements.

Full ERD, fields, constraints, indexes, and ledger/balance trade-offs are in `data-model.md`.

## 4. API Design

The API is REST-oriented around main resources while keeping typed transaction resources for business clarity. Full endpoint details are in `endpoints.md`.

High-level resource groups:

- Catalogue and containers: `/skus`, `/stock-containers`.
- Physical stock views: `/stock-balances`, `/stock-containers/{id}/balances`, `/skus/{id}/balances`.
- Ledger and typed transactions: `/stock-ledger-entries`, `/stock-usage-records`, `/stock-receipts`, `/stock-transfers`, `/stock-adjustments`.
- Workflows: `/stock-takes`, `/import-jobs`, `/reorder-policies`, `/reorder-requests`.

API rules:

- All mutating endpoints require authorization, permission checks, and an `Idempotency-Key`.
- Mutating endpoints return a UUID handle.
- `stock_balance` is read-only through the API.
- Operational APIs expose only real containers and physical stock.
- Audit/admin ledger APIs may expose virtual containers.
- Lifecycle changes use `PATCH` status transitions, not action endpoints.
- Business conflicts return `409`.

An optional global ordered queue variant is described in `queue.md`. 
It is optional because the MVP does not require globally ordered async processing: PostgreSQL transactions and row locks are sufficient for the stated scale of hundreds of concurrent users and thousands of movements/day. 
The queue variant is useful if the product wants deterministic global command ordering, but it changes API semantics to `202 Accepted`, introduces pending/rejected command states, requires worker recovery, and makes stock writes eventually consistent.

## 5. Day-0 Population

Day-0 population is mandatory because customers already have stock in vans and sites.

Recommended process:

1. Upload catalogue, containers, quantities, and optional cost layers by CSV/API.
2. Validate asynchronously and produce a dry-run report.
3. Show row-level errors and warnings.
4. Require manager/admin approval.
5. Apply the import as `INITIAL_LOAD` ledger entries.
6. Produce a reconciliation report by SKU and container.
7. Prevent duplicate initial loads unless explicitly marked as correction imports.

Initial quantities must enter through the same ledger path as every other mutation. They should not be inserted directly into `stock_balance`.

## 6. Migration and Rollout

Use additive schema migrations in the new `stock-management` service. Do not change existing WorkOrder or Location tables.

Rollout plan:

1. Deploy schema, service, and feature flags.
2. Enable catalogue and container admin for internal users.
3. Run day-0 import dry runs for a pilot customer/site.
4. Enable receipts and current stock views.
5. Enable transfers and manager adjustments.
6. Enable work-order usage for a small operative group, initially requiring online commits.
7. Add stock takes and reorder policies once stock data quality is stable.

Backfills:

- Create container records from existing vehicles and storage locations.
- Subscribe to WorkOrder, Location, vehicle, and operative-assignment events.
- Import starting stock through the day-0 workflow.
- Reconcile imported totals with customer sign-off totals.

## 7. Non-Functional Considerations

Concurrency:

- Default model uses PostgreSQL row-level locks on affected `stock_balance` rows.
- Lock rows in deterministic order.
- Keep transactions short.
- Use optimistic `version` fields only for stale-read detection, not decrement correctness.

Reliability:

- Use idempotency records for safe mobile/API retries.
- Use the outbox pattern for SNS/RabbitMQ publishing.
- Queue consumers must be idempotent because delivery is at-least-once.
- Reconciliation jobs compare ledger-derived totals against `stock_balance`.

Scalability:

- Current stock reads use `stock_balance`.
- Historical reporting uses snapshots, read replicas, or analytical exports.
- Movement tables can be partitioned by `posted_at` later.
- Reorder checks and imports can run asynchronously.

Observability:

- Track movement counts, mutation latency, lock wait time, insufficient-stock conflicts, negative balances, outbox lag, import errors, and ledger/balance mismatches.
- Alert on balances below tolerance, reconciliation mismatch, outbox backlog, import apply failures, and elevated conflict rates.

## 8. MVP to v2

MVP:

- SKU catalogue with unit and increment rules.
- Real and virtual stock containers.
- Double-entry stock ledger.
- Transactional stock balances.
- Work-order usage, receipts, transfers, adjustments, current stock queries.
- Day-0 import with validation and approval.
- Basic stock take and discrepancy posting.
- Outbox events and core monitoring.

v2:

- Reservations and allocations.
- Returns from work orders.
- Reorder policies and reorder requests.
- Richer offline mobile sync and conflict resolution.
- FIFO/LIFO valuation and cost reports.
- Bin-level warehouse structure.
- Approval workflows for high-value or unusual movements.
- Reporting snapshots and analytical exports.
