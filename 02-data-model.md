# Data Model

## ERD

The Mermaid diagram source is kept in `02-erd.mmd`. 
It intentionally shows only the core stock model; supporting workflow/integration tables are listed later.

## `sku`

Material catalogue item.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `code` | varchar | no | Business SKU code. |
| `name` | varchar | no | Display name. |
| `unit` | varchar | no | Canonical unit: `each`, `box`, `metre`, etc. |
| `tracking_method` | enum | no | `CONTAINER`, `UNIT`, or `CONTINUOUS`. |
| `min_increment` | numeric | no | Smallest valid movement, e.g. `1`, `0.1`, `0.01`. |
| `negative_tolerance` | numeric | no | Allowed negative threshold; default `0`. |
| `is_active` | boolean | no | Inactive SKUs cannot be used for normal new movements. |

Key constraints and indexes:

- Unique `code`.
- `min_increment > 0`.
- `negative_tolerance >= 0`.
- Index `is_active`.

Invariants:

- Persist quantities in the SKU canonical unit.
- Movement quantities must be multiples of `min_increment`.

## `stock_container`

Any real or virtual place where stock is posted. Real containers represent physical stock locations.

Virtual containers represent accounting sources and sinks and do not hold operational stock.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `container_type` | enum | no | `VAN`, `WAREHOUSE`, `WORKSHOP`, later `LOCKER`, `BIN`, plus virtual types such as `SUPPLIER_SOURCE` and `WORK_ORDER_CONSUMED`. |
| `is_virtual` | boolean | no | Whether this is an accounting container rather than a physical stock location. |
| `code` | varchar | no | Business identifier. |
| `name` | varchar | no | Display name. |
| `status` | enum | no | `ACTIVE`, `INACTIVE`, `QUARANTINED`. |
| `location_ref` | varchar | yes | External Location service ID for site-based storage. |
| `vehicle_ref` | varchar | yes | External vehicle ID for van containers. |

Key constraints and indexes:

- Unique `code`.
- Index `(is_virtual, container_type, status)`.
- Partial index on `vehicle_ref` where present.
- Partial index on `location_ref` where present.

Invariants:

- Van containers normally have a `vehicle_ref`.
- Warehouse/workshop containers normally have a `location_ref`.
- Inactive real containers cannot receive normal operational movements.
- Virtual containers are not shown in normal "stock on hand" operational views and cannot be used for reservations, van assignment, or manual transfers.

## `stock_balance`

Fast current physical stock projection for real containers only.

There are no `stock_balance` rows for virtual containers.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `container_id` | UUID | no | Stock container. |
| `sku_id` | UUID | no | Material item. |
| `on_hand` | numeric | no | Current physical quantity. |
| `reserved` | numeric | no | Reserved quantity, default `0`; may be unused in MVP. |
| `version` | bigint | no | Incremented on update. |
| `updated_at` | timestamptz | no | Projection update time. |

Key constraints and indexes:

- Unique `(container_id, sku_id)`.
- Foreign key `container_id -> stock_container.id`.
- Foreign key `sku_id -> sku.id`.
- Index `container_id` for container stock.
- Index `sku_id` for global stock by SKU.
- `reserved >= 0`.
- `container_id` must reference a real, non-virtual container.

Invariants:

- Updated only by the stock mutation transaction.
- `available = on_hand - reserved`.
- Rebuildable from ledger lines where `container.is_virtual = false`.
- Represents operational on-hand stock only; virtual ledger quantities are excluded.

## `stock_ledger`

Immutable movement header.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `movement_type` | enum | no | `USAGE`, `RECEIPT`, `TRANSFER`, `ADJUSTMENT`, `STOCKTAKE`, `INITIAL_LOAD`, `RETURN`. |
| `source_ref` | varchar | yes | Human-readable source container/account for the movement, e.g. `warehouse-1` or `SUPPLIER_SOURCE`. |
| `destination_ref` | varchar | yes | Human-readable destination container/account for the movement, e.g. `van-1` or `WORK_ORDER_CONSUMED`. |
| `external_ref_type` | varchar | yes | `WORK_ORDER`, `PURCHASE_ORDER`, `IMPORT_JOB`, etc. |
| `external_ref` | varchar | yes | External ID. |
| `reason_code` | varchar | yes | Required for adjustments and discrepancy corrections. |
| `idempotency_key` | varchar | yes | API retry key. |
| `created_by_ref` | varchar | no | User/operative/manager reference. |
| `posted_at` | timestamptz | no | Business posting time. |

Key constraints and indexes:

- Unique `idempotency_key` where present.
- Index `(movement_type, posted_at)`.
- Index `(external_ref_type, external_ref)`.
- Future partitioning by `posted_at` if volume requires it.

Invariants:

- Posted ledger records are immutable.
- Corrections are represented by new adjustment movements.
- Every ledger entry has at least two lines.
- Posted ledger entries must balance to zero per SKU/unit across their lines.
- `source_ref` and `destination_ref` are denormalized readability fields; `stock_ledger_line` remains authoritative for balance calculation.

## `stock_ledger_line`

Line-level quantity posting.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `ledger_id` | UUID | no | Parent movement. |
| `line_no` | integer | no | Stable order within movement. |
| `sku_id` | UUID | no | Material item. |
| `container_id` | UUID | no | Container affected. |
| `quantity` | numeric | no | Absolute quantity posted. Always positive. |
| `direction` | enum | no | `IN` increases the container/account balance; `OUT` decreases it. |
| `unit_cost` | numeric | yes | Optional valuation data. |
| `posted_at` | timestamptz | no | Copied from the ledger header for efficient historical queries. |
| `metadata` | jsonb | yes | Batch, import row, supplier, notes. |

Key constraints and indexes:

- Unique `(ledger_id, line_no)`.
- Foreign key `ledger_id -> stock_ledger.id`.
- Foreign key `sku_id -> sku.id`.
- Foreign key `container_id -> stock_container.id`.
- `quantity > 0`.
- `direction in ('IN', 'OUT')`.
- Index `(container_id, sku_id, posted_at)`.
- Index `(sku_id, posted_at)`.

Invariants:

- Usage creates an `OUT` line from the real container and an `IN` line to `WORK_ORDER_CONSUMED`.
- Receipt creates an `IN` line to the destination container and an `OUT` line from `SUPPLIER_SOURCE`.
- Transfer creates one `OUT` source line and one `IN` destination line under the same ledger entry.
- For double-entry consistency, every movement also has a balancing line.
- Balance impact is derived as `+quantity` for `IN` and `-quantity` for `OUT`.
- Receipts balance against `SUPPLIER_SOURCE`, usage balances against `WORK_ORDER_CONSUMED`, adjustments balance against `ADJUSTMENT_GAIN` or `ADJUSTMENT_LOSS`, and initial loads balance against `INITIAL_LOAD_SOURCE`.

## `stock_take`

Physical stock audit session for one real container.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `container_id` | UUID | no | Container being counted. |
| `status` | enum | no | `DRAFT`, `COUNTED`, `POSTED`, or `CANCELLED`. |
| `started_by_ref` | varchar | no | External user reference for the person who started the count. |
| `posted_ledger_id` | UUID | yes | Ledger entry created when discrepancies are posted. |
| `started_at` | timestamptz | no | Count start time. |
| `posted_at` | timestamptz | yes | Posting time, present only after status becomes `POSTED`. |

Key constraints and indexes:

- Foreign key `container_id -> stock_container.id`.
- Foreign key `posted_ledger_id -> stock_ledger.id`.
- Index `(container_id, status)`.
- At most one active stock take per container where status is `DRAFT` or `COUNTED`.

Invariants:

- `container_id` must reference a real, non-virtual container.
- Posting a stock take creates a `STOCKTAKE` ledger entry for non-zero discrepancies.
- If stock changed since the count snapshot, posting should return a stale-count conflict rather than silently applying old expected quantities.

## `stock_take_line`

Counted quantity for one SKU within a stock take.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `stock_take_id` | UUID | no | Parent stock take. |
| `sku_id` | UUID | no | Counted SKU. |
| `expected_quantity` | numeric | no | Projected balance when the count line was prepared. |
| `counted_quantity` | numeric | no | Physical counted quantity. |
| `discrepancy_quantity` | numeric | no | `counted_quantity - expected_quantity`. |
| `reason_code` | varchar | yes | Required for material discrepancies. |
| `notes` | text | yes | Optional count notes. |

Key constraints and indexes:

- Foreign key `stock_take_id -> stock_take.id`.
- Foreign key `sku_id -> sku.id`.
- Unique `(stock_take_id, sku_id)`.

Invariants:

- Counted quantities must satisfy the SKU's `min_increment`.
- Posting converts non-zero discrepancies into balanced ledger lines against `ADJUSTMENT_GAIN` or `ADJUSTMENT_LOSS`.

## Supporting Tables

| Table | Purpose |
| --- | --- |
| `idempotency_record` | Stores request hash, status, and response for safe mobile/API retries. |
| `outbox_event` | Stores committed integration events to publish asynchronously. |
| `import_job` | Day-0 catalogue and stock import lifecycle. |
| `import_row_error` | Row-level validation errors and warnings. |
| `reorder_policy` | Min/target quantity rules. |
| `reorder_request` | Lightweight request to replenish stock. |
| `cost_layer` | Optional FIFO/LIFO valuation layer. |

Supporting table relationships:

- `idempotency_record.ledger_id -> stock_ledger.id` when a request posts a movement.
- `outbox_event.aggregate_id -> stock_ledger.id` when publishing stock movement events.
- `import_row_error.import_job_id -> import_job.id`.
- `import_job.applied_ledger_id -> stock_ledger.id` when an initial load is applied as a ledger entry.
- `reorder_policy.sku_id -> sku.id`.
- `reorder_policy.container_id -> stock_container.id` when the policy is container-specific.
- `reorder_request.policy_id -> reorder_policy.id`.
- `reorder_request.sku_id -> sku.id`.
- `reorder_request.container_id -> stock_container.id` when replenishing a specific container.
- `cost_layer.sku_id -> sku.id`.
- `cost_layer.container_id -> stock_container.id`.
- `cost_layer.source_ledger_line_id -> stock_ledger_line.id`.

## Ledger vs Balances vs Snapshots

Ledger-only is best for auditability, especially with double-entry balancing, but too slow for frequent mobile and dashboard reads because every lookup aggregates history.

Persisted balances are fast and easy to lock for concurrent writes, but they are a projection of physical stock only. They must only be updated transactionally with ledger inserts for real containers.

Snapshots/materialized views are useful for historical reports and `stock at time T`. They should not replace the write model. A practical approach is daily snapshots plus ledger deltas after the snapshot.

Recommended model:

- Ledger is the balanced source of truth for real and virtual postings.
- Balance is the current physical stock projection for real containers.
- Snapshots/read replicas support reporting.
