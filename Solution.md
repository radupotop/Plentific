# Stock and Materials Management Design

## 1. Questions, Assumptions, and Scope

### Questions I would ask

- Do operatives need to record usage offline, or can stock commits require connectivity?
- Can stock ever go negative operationally, for example because a van count is stale?
- Does procurement already exist elsewhere, or should this module own purchase orders?
- Are WorkOrder, Location, vehicle, and user identifiers globally unique across services?
- Is FIFO/LIFO cost valuation needed in MVP, or only later?
- Do stock adjustments, transfers, and stock takes require approval workflows?

### Assumptions

- The MVP focuses on accurate quantity tracking, auditability, and fast stock lookup.
- WorkOrders and Locations are owned by existing services. This module stores external references and may keep local read-model stubs for display/validation.
- Stock is held in containers: vans, warehouses, workshops, and future container types such as lockers.
- Main user roles are Field Operative, Operations Manager, Store Manager, and General Manager/Admin.
- PostgreSQL is the source of truth. RabbitMQ/Celery/SNS are delivery mechanisms, not the authoritative inventory history.
- Stock cannot go negative by default. A SKU-level or system-level tolerance can allow controlled exceptions.

### Main risks

- Offline mobile usage can create conflicts when multiple devices consume the same van stock before syncing.
- Initial customer data may be incomplete or inaccurate, so day-0 import needs validation and reconciliation.
- Reporting over the raw ledger can become expensive unless current balances and snapshots are used.
- Synchronous validation against WorkOrder/Location services can reduce availability if those services are down.

## 2. Proposed Architecture

I would introduce a dedicated Django/DRF service called `stock-management`.

It owns:

- Material catalogue/SKUs and quantity rules.
- Stock containers: vans, warehouses, workshops, later lockers/bins, plus virtual accounting containers.
- Immutable stock ledger entries.
- Current stock balances.
- Stock takes, adjustments, transfers, receipts, usage, and day-0 imports.
- Reorder policies and lightweight reorder requests.
- Optional cost layers for FIFO/LIFO valuation.

It does not own:

- WorkOrder lifecycle.
- Location/property lifecycle.
- User/operative identity.
- Vehicle assignment source of truth.
- Full procurement lifecycle, if that already exists elsewhere.

External concepts are represented as references:

- `work_order_ref` for usage.
- `location_ref` for storage sites.
- `vehicle_ref` for van containers.
- `operative_ref` / `user_ref` for audit and access control.

The service may maintain local stubs such as `known_work_order`, `known_location`, or `vehicle_assignment` from SNS events. These are read models, not ownership boundaries.

## 3. Core Design

Inventory mutations are stored in an append-only `StockLedger` using double-entry inventory accounting.

Every stock-changing action creates ledger records:

- `RECEIPT`: goods received into a container.
- `USAGE`: materials consumed on a work order.
- `TRANSFER`: stock moved between containers.
- `ADJUSTMENT`: manual correction with a reason.
- `STOCKTAKE`: discrepancy correction from an audit.
- `INITIAL_LOAD`: day-0 starting stock.
- `RETURN`: future return from a job.

Current physical stock is stored separately in `stock_balance` for fast reads. This is a projection of the ledger for real containers only, not a separate source of truth.

Important invariant:

> `stock_balance` for real containers must always be derivable from `stock_ledger_line`, and every posted ledger transaction must balance to zero per SKU/unit across real and virtual containers.

### Real vs virtual stock

Real stock means physical inventory that can be counted, transferred, reserved, or used by operatives. It exists only in real containers such as vans, warehouses, workshops, lockers, and bins.

Virtual stock is not available inventory. It is an accounting representation used only inside the ledger to make each transaction balanced and explain where stock came from or went. 

Virtual containers should never be assignable to operatives, selected as transfer destinations in normal UI, or included in operational on-hand totals.

Virtual containers include:

- `SUPPLIER_SOURCE`: balances receipts into stock.
- `WORK_ORDER_CONSUMED`: sink for materials consumed on work orders.
- `ADJUSTMENT_GAIN`: source for found stock or positive corrections.
- `ADJUSTMENT_LOSS`: sink for damaged, lost, or written-off stock.
- `INITIAL_LOAD_SOURCE`: balances day-0 starting stock.
- `RETURNED_FROM_WORK_ORDER`: source for future returns.

Operational stock views include only real containers. Audit and reporting views may include both real and virtual containers.

## 4. Data Model

The detailed ERD, field-level descriptions, constraints, indexes, and ledger/balance trade-offs are kept in `data-model.md`.

The core model is:

- `sku`: material catalogue item and unit/increment rules.
- `stock_container`: real or virtual stock location.
- `stock_balance`: read-optimized physical stock projection for real containers only.
- `stock_ledger` and `stock_ledger_line`: immutable double-entry source of truth.
- Supporting workflow tables for idempotency, stock takes, import jobs, reorders, outbox events, and optional cost layers.

The important trade-off is hybrid storage: the ledger is the source of truth, balances provide fast current reads, and snapshots/read replicas support historical reporting.

## 5. API Design

The detailed endpoint catalogue is kept in `endpoints.md`. 

The API is REST-oriented around main resources while keeping typed transaction resources for business clarity.

High-level resource groups:

- Catalogue and containers: `/skus`, `/stock-containers`.
- Physical stock views: `/stock-balances`, `/stock-containers/{id}/balances`, `/skus/{id}/balances`.
- Ledger and typed transactions: `/stock-ledger-entries`, `/stock-usage-records`, `/stock-receipts`, `/stock-transfers`, `/stock-adjustments`.
- Operational workflows: `/stock-takes`, `/import-jobs`, `/reorder-policies`, `/reorder-requests`.

Important API rules:

- All mutating endpoints require authorization, permission checks, and an `Idempotency-Key` header.
- Mutating endpoints return a UUID handle: either the created resource ID for synchronous writes or `command_id` for queued writes.
- `stock_balance` is read-only through the API; clients never directly create or update balances.
- Typed transaction resources create immutable balanced `stock_ledger` entries.
- Operational APIs expose only real containers and physical stock. Audit/admin ledger APIs may expose virtual containers.
- Lifecycle changes such as posting a stock take or approving/applying an import are represented as `PATCH` status transitions, not action endpoints.
- Business conflicts return `409`, for example insufficient stock, stale stock take, invalid lifecycle transition, or idempotency conflict.

## 6. Day-0 Population

Day-0 population is mandatory because customers already have stock in vans and sites.

Recommended process:

1. Upload catalogue, containers, quantities, and optional cost layers by CSV/API.
2. Validate asynchronously and produce a dry-run report.
3. Show row-level errors and warnings.
4. Require manager/admin approval.
5. Apply the import as `INITIAL_LOAD` ledger entries.
6. Produce a reconciliation report with totals by SKU and container.
7. Prevent accidental duplicate initial loads unless explicitly marked as correction imports.

Validation examples:

- SKU codes are unique.
- Units and tracking methods valid.
- Containers exist or are declared in the import.
- Quantities satisfy SKU increments.
- Initial quantities are non-negative unless this is a signed-off corrective import.
- Cost layers sum to the imported quantity when valuation is enabled.

## 7. Migration and Rollout

### Schema migration

- Build this as a new service/schema without changing existing WorkOrder or Location tables.
- Use additive migrations first: create tables, indexes, and read models.
- Create large indexes concurrently where needed.
- Avoid destructive changes during rollout.
- Keep movement tables designed for future partitioning by `posted_at`.

### Rollout plan

1. Deploy schema and feature flags.
2. Enable catalogue/container admin for internal users.
3. Run day-0 import dry runs for a pilot customer/site.
4. Enable receipts and current stock views.
5. Enable transfers and manager adjustments.
6. Enable work order usage for a small operative group, initially requiring online commits.
7. Add stock takes and reorder policies once stock data quality is stable.

### Backfills

- Create container records from existing vehicles and storage locations.
- Subscribe to WorkOrder, Location, vehicle, and operative assignment events.
- Import initial stock through the day-0 workflow.
- Reconcile imported totals with customer sign-off totals.

## 8. Non-Functional Considerations

### Concurrency

Use PostgreSQL row-level locks on affected `stock_balance` rows.

- Lock rows in deterministic order.
- Keep transactions short.
- Validate availability while locks are held.
- Use optimistic `version` fields for stale-read detection, not as the sole correctness mechanism.

This is appropriate for hundreds of concurrent users and thousands of movements per day.

### Idempotency

Every mutating request uses an `Idempotency-Key`.

- Same key + same request returns the original response.
- Same key + different request returns `409 IDEMPOTENCY_KEY_CONFLICT`.
- Idempotency rows are committed with the stock mutation.

### Reliability

- Use the outbox pattern for SNS/RabbitMQ publishing.
- Queue consumers must be idempotent because delivery is at-least-once.
- External service outages should not corrupt stock. Depending on operational policy, reject with retryable `503` or accept external refs and reconcile later.
- Posted ledger entries are immutable; corrections are new entries.

### Scalability

- Current stock APIs read from `stock_balance`.
- Common indexes support lookup by container and by SKU.
- Historical reporting uses snapshots, read replicas, or analytical exports.
- Reorder checks can run asynchronously after balance updates.
- Large imports run in Celery with progress tracking.

### Observability

Track:

- Movement count by type.
- Stock mutation latency.
- Balance lock wait time.
- Insufficient stock conflicts.
- Negative balances beyond tolerance.
- Outbox publish lag.
- Import validation errors.
- Ledger/balance reconciliation mismatches.

Alert on:

- Balance below allowed tolerance.
- Ledger and balance mismatch.
- Outbox backlog beyond SLA.
- Import apply failures.
- High conflict/error rates after rollout.

## 9. MVP to v2

### MVP

- Roles and permissions for Field Operative, Operations Manager, Store Manager, and Admin.
- SKU catalogue with units, tracking methods, and increments.
- Stock containers for vans, warehouses, and workshops.
- Permanent append-only stock ledger.
- Transactional stock balances.
- Work order usage.
- Receipts.
- Transfers.
- Adjustments.
- Current stock queries.
- Day-0 import with validation and approval.
- Basic stock take and discrepancy posting.
- Outbox events and core monitoring.

### v2

- Reservations and allocations.
- Returns from work orders.
- Reorder policies and reorder requests.
- Richer offline mobile sync and conflict resolution.
- FIFO/LIFO valuation and cost reports.
- Bin-level warehouse structure.
- Approval workflows for high-value or unusual movements.
- Reporting snapshots and analytical exports.

### Deferred but enabled

- Full procurement can live in a separate procurement service while receipts remain in stock management.
- New container types are additive.
- Cost valuation can be added without changing the quantity ledger.
- Historical reporting can move to a warehouse without changing write semantics.
