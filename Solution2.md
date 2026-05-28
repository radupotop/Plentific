# Solution Design document

## Entities

* Roles (Field Op, Op Manager, Store Manager, General Manager)

* SKU
* StockContainer (Van, Warehouse, Workshop)
* Materials (qty: item, box, meter, etc.)
* WorkOrder (a stub entity, has external ID)
    * OrderItems
* Location (a stub entity, has external ID)
* StockLedger (txn envelope, immutable)
    * StockLedgerLine (txn, immutable)
* StockBalance (projection of txn totals)
*
