# Data Model

## ERD

The Mermaid ERD source is kept in `02-erd.mmd`.

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

## `stock_ledger_line`

Line-level quantity delta.

| Field | Type | Null | Meaning |
| --- | --- | --- | --- |
| `id` | UUID | no | Primary key. |
| `ledger_id` | UUID | no | Parent movement. |
| `line_no` | integer | no | Stable order within movement. |
| `sku_id` | UUID | no | Material item. |
| `container_id` | UUID | no | Container affected. |
| `quantity_delta` | numeric | no | Positive inbound, negative outbound. |
| `unit_cost` | numeric | yes | Optional valuation data. |
| `posted_at` | timestamptz | no | Copied from the ledger header for efficient historical queries. |
| `metadata` | jsonb | yes | Batch, import row, supplier, notes. |

Key constraints and indexes:

- Unique `(ledger_id, line_no)`.
- `quantity_delta <> 0`.
- Index `(container_id, sku_id, posted_at)`.
- Index `(sku_id, posted_at)`.

Invariants:

- Usage creates negative lines.
- Receipt creates positive lines.
- Transfer creates one negative source line and one positive destination line under the same ledger entry.
- For double-entry consistency, every movement also has a balancing line.
- Receipts balance against `SUPPLIER_SOURCE`, usage balances against `WORK_ORDER_CONSUMED`, adjustments balance against `ADJUSTMENT_GAIN` or `ADJUSTMENT_LOSS`, and initial loads balance against `INITIAL_LOAD_SOURCE`.

## Supporting Tables

| Table | Purpose |
| --- | --- |
| `idempotency_record` | Stores request hash, status, and response for safe mobile/API retries. |
| `outbox_event` | Stores committed integration events to publish asynchronously. |
| `stock_take` | Stock audit session for one container. |
| `stock_take_line` | Counted quantity, expected quantity, and discrepancy by SKU. |
| `import_job` | Day-0 catalogue and stock import lifecycle. |
| `import_row_error` | Row-level validation errors and warnings. |
| `reorder_policy` | Min/target quantity rules. |
| `reorder_request` | Lightweight request to replenish stock. |
| `cost_layer` | Optional FIFO/LIFO valuation layer. |

## Ledger vs Balances vs Snapshots

Ledger-only is best for auditability, especially with double-entry balancing, but too slow for frequent mobile and dashboard reads because every lookup aggregates history.

Persisted balances are fast and easy to lock for concurrent writes, but they are a projection of physical stock only. They must only be updated transactionally with ledger inserts for real containers.

Snapshots/materialized views are useful for historical reports and `stock at time T`. They should not replace the write model. A practical approach is daily snapshots plus ledger deltas after the snapshot.

Recommended model:

- Ledger is the balanced source of truth for real and virtual postings.
- Balance is the current physical stock projection for real containers.
- Snapshots/read replicas support reporting.
