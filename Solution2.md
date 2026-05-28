# Solution Design document

Stock & Materials Management module

## Entities

* Roles (Field Op, Op Manager, Store Manager, General Manager)

* SKU
    * schema: id (UUID), code, name, unit, unit_type, min_increment, neg_tolerance, is_active
* StockContainer (Van, Warehouse, Workshop)
    * schema: id (UUID), code, name, container_type, is_active, external_ref
* StockTxn (txn envelope, immutable) <-> WorkOrder
    * StockTxnLine (txn, immutable)
* StockBalance (projection of txn totals)
* 


## External Entities

* WorkOrder (a stub entity, has external ID)
* Location (a stub entity, has external ID)
