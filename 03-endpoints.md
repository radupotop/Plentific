# API Design

The API is REST-oriented around the main resources while keeping typed transaction resources for business clarity. Each typed transaction resource creates one immutable `stock_ledger` entry underneath.

All mutating endpoints require authorization, permission checks, and an `Idempotency-Key` header. Successful mutating endpoints return the UUID of the created or updated resource so clients have a stable handle for retries, polling, audit, and UI state.

## Catalogue and Containers

```text
GET   /skus
POST  /skus
GET   /skus/{sku_id}
PATCH /skus/{sku_id}

GET   /stock-containers
POST  /stock-containers
GET   /stock-containers/{container_id}
PATCH /stock-containers/{container_id}
```

Rules:

- SKU quantities use the SKU canonical unit and must respect `min_increment`.
- Normal container APIs expose real containers by default.
- Virtual containers are hidden by default and are only visible through audit/admin access, for example with `include_virtual=true`.
- Virtual containers cannot be assigned to operatives, used for reservations, or selected in normal transfer/usage UI.

## Physical Stock Balances

```text
GET /stock-balances?sku_id=&container_id=
GET /stock-containers/{container_id}/balances
GET /skus/{sku_id}/balances
```

Rules:

- Balances are read-only API resources.
- Clients never `POST` or `PATCH` balances directly.
- Balance endpoints return physical stock only: real containers, `on_hand`, `reserved`, and `available`.
- Virtual ledger postings are excluded from operational balance responses.

## Ledger and Transaction Resources

```text
GET /stock-ledger-entries?movement_type=&sku_id=&container_id=&from=&to=
GET /stock-ledger-entries/{ledger_id}

POST /stock-usage-records
POST /stock-receipts
POST /stock-transfers
POST /stock-adjustments
```

Rules:

- `stock_ledger` is immutable and remains the canonical audit record.
- Typed transaction resources are command-like resource creations that create a posted ledger entry.
- Each successful typed `POST` returns the typed resource UUID and the underlying `ledger_id`.
- All transaction resources create balanced double-entry ledger lines.
- Audit access to `stock-ledger-entries` may include virtual containers; operational APIs do not.

Example successful transaction response:

```json
{
  "id": "4f4a2c32-5f53-4d56-9dc6-0df510ddf178",
  "ledger_id": "8a758c35-b3cf-4d94-9d6e-059c4b7ad0b7",
  "status": "POSTED"
}
```

### Record Usage

`POST /stock-usage-records`

```json
{
  "work_order_ref": "WO-12345",
  "container_id": "van-1",
  "operative_ref": "user-100",
  "lines": [
    { "sku_id": "screw-001", "quantity": "3", "unit": "each" },
    { "sku_id": "cable-001", "quantity": "2.4", "unit": "metre" }
  ]
}
```

Rules:

- Field Operative can consume only from an assigned van unless a manager overrides.
- `container_id` must be a real container.
- Quantities must be positive and valid for the SKU increment.
- Internally, usage writes an `OUT` line from the van and an `IN` balancing line to `WORK_ORDER_CONSUMED`, tagged with the work order reference.
- If stock would fall below tolerance, return `409 INSUFFICIENT_STOCK`.

### Receive Stock

`POST /stock-receipts`

```json
{
  "destination_container_id": "warehouse-1",
  "purchase_order_ref": "PO-9001",
  "lines": [
    { "sku_id": "screw-001", "quantity": "100", "unit": "each", "unit_cost": "0.04" }
  ]
}
```

Rules:

- Store Manager permission required.
- Destination container must be active and real.
- Quantities must be positive.
- Internally, receipt writes an `IN` destination line and an `OUT` balancing line from `SUPPLIER_SOURCE`.
- Optional valuation data creates cost layers if enabled.

### Transfer Stock

`POST /stock-transfers`

```json
{
  "source_container_id": "warehouse-1",
  "destination_container_id": "van-1",
  "reason_code": "VAN_REPLENISHMENT",
  "lines": [
    { "sku_id": "screw-001", "quantity": "25", "unit": "each" }
  ]
}
```

Rules:

- Operations Manager or Store Manager permission required.
- Source and destination must be different real containers.
- Source and destination balance rows are locked in deterministic order to reduce deadlocks.
- One ledger entry contains both `OUT` source lines and `IN` destination lines. Since both sides are real containers, the transaction balances without a virtual container.

### Adjust Stock

`POST /stock-adjustments`

```json
{
  "container_id": "van-1",
  "reason_code": "DAMAGED",
  "notes": "Cable roll damaged during loading.",
  "lines": [
    { "sku_id": "cable-001", "quantity": "1.5", "direction": "OUT", "unit": "metre" }
  ]
}
```

Rules:

- Manager permission required.
- Reason is mandatory.
- `container_id` must be a real container.
- `OUT` adjustments respect tolerance unless an elevated override is explicitly allowed.
- `OUT` adjustments balance against `ADJUSTMENT_LOSS`; `IN` adjustments balance against `ADJUSTMENT_GAIN`.

## Stock Takes

```text
POST  /stock-takes
GET   /stock-takes/{stock_take_id}
PATCH /stock-takes/{stock_take_id}
PUT   /stock-takes/{stock_take_id}/lines/{sku_id}
```

Rules:

- `POST /stock-takes` creates a stock take for one real container.
- `POST /stock-takes` returns the created `stock_take_id`.
- `PUT /stock-takes/{stock_take_id}/lines/{sku_id}` adds or replaces the counted quantity for one SKU.
- Posting is a lifecycle transition via `PATCH /stock-takes/{stock_take_id}` with `status: "POSTED"`, not an action endpoint.
- Posting creates a `STOCKTAKE` ledger entry for discrepancies.
- Positive discrepancies create `IN` lines balanced against `ADJUSTMENT_GAIN`; negative discrepancies create `OUT` lines balanced against `ADJUSTMENT_LOSS`.
- If balances changed since the count was prepared, return `409 STOCK_TAKE_STALE`.

## Import Jobs

```text
POST  /import-jobs
GET   /import-jobs/{import_job_id}
PATCH /import-jobs/{import_job_id}
```

Rules:

- Import jobs support day-0 catalogue, container, stock quantity, and optional cost layer import.
- `POST /import-jobs` returns the created `import_job_id`.
- The import lifecycle is represented by status transitions such as `VALIDATED`, `APPROVED`, `APPLYING`, and `APPLIED`.
- Approval and apply are `PATCH` status transitions, not `/approve` or `/apply` action endpoints.
- Applying an import creates `INITIAL_LOAD` ledger entries balanced against `INITIAL_LOAD_SOURCE`.
- Imports never directly edit `stock_balance`.

## Reorder Resources

```text
GET   /reorder-policies
POST  /reorder-policies
PATCH /reorder-policies/{reorder_policy_id}

GET   /reorder-requests
POST  /reorder-requests
PATCH /reorder-requests/{reorder_request_id}
```

Rules:

- Reorder policies define minimum and target quantities.
- Reorder requests do not change stock.
- Only receipts change physical stock.

## Error Semantics

| Status | Meaning |
| --- | --- |
| `400` | Invalid body, unit, quantity, increment, lifecycle transition, or missing reason. |
| `401/403` | Authentication or permission failure. |
| `404` | Unknown SKU, container, stock take, import job, or external reference when strict validation is enabled. |
| `409` | Business conflict: insufficient stock, stale stock take, invalid lifecycle transition, or idempotency conflict. |
| `422` | Import parsed successfully but contains validation errors. |
| `202` | Async import validation or apply job accepted. |
