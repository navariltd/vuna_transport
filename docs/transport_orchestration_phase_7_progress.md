# Phase 7 — Customer returns and supplier collections

## Current status: supplier-demand foundation implemented (7A)

This is **not completion of Phase 7**. Existing approved customer Return Request
assignment, customer pickup evidence and customer sales-return processing remain
unchanged. Warehouse-side supplier vehicle receipts/transfers are now implemented;
supplier load/task/mobile orchestration is still pending.

## Implemented in this slice

- Purchase Order **Company Pickup** is off by default. **Supplier Pickup Address**
  must be active and linked to that supplier. These settings can be configured
  on an existing submitted PO, but active demand freezes pickup identity,
  receiving endpoints and required-date grouping until demands are released.
- Purchase Order is allowlisted in Transport Settings. Both its source-policy row
  and the PO opt-in must permit generation. Manual/automatic creation, workflow
  state and demand auto-submission reuse the existing source-policy service.
  Nothing enables Purchase Orders globally or changes current source policies.
- Only open submitted POs with active stock cargo are eligible. Non-stock and
  drop-shipped rows are excluded. Subcontracting and unit-price orders are
  deliberately unsupported in this slice.
- Supplier demands carry the supplier, external pickup Address and receiving
  company warehouse. No fake source Warehouse or customer is created. Destination
  warehouses must be active, non-group same-company storage, not vehicle transit.
- Each receiving warehouse/required-date pair becomes one stable movement leg.
  Source locking and unique source keys reuse demand-generation idempotency.
- Requested UOM and conversion factor are retained. ERPNext PO Item
  `received_qty` is in transaction UOM: outstanding = `qty - received_qty`, then
  Stock Qty = outstanding × conversion factor. Confirmed from ERPNext
  `buying/doctype/purchase_order/purchase_order.py::make_purchase_receipt`.
- Demand snapshots are rebuilt from the authoritative PO when validated.
  Existing create/read/submit/company checks apply. Active demand blocks PO
  cancellation. Address/opt-in/endpoint updates acquire the same source lock.
- Demand generation itself introduces no stock, Purchase Receipt, costing or GL posting.
  Supplier demand cannot be loaded through the existing warehouse-transfer API;
  that would incorrectly treat supplier stock as company-owned outbound stock.

## Test on meru-dairy.localhost

The new PO fields and Transport Demand metadata were synced selectively; a full
site migration was not run and source settings were not changed.

1. In Transport Settings add **Purchase Order**, Enabled, Manual. Leave automatic
   demand submission off if demands should be reviewed first.
2. On a submitted, outstanding stock-item Purchase Order enable **Company Pickup**.
3. Choose **Supplier Pickup Address**; ensure it is linked to the supplier.
4. Ensure item rows identify receiving warehouses and required dates. Save.
5. Use **Create → Transport Demand**. Check supplier/address, receiving warehouse,
   original UOM, remaining quantities and separate date/destination legs.
6. Repeat the action: it should return the same existing demand, not duplicate it.
7. Review/submit the demand if required. Supplier load and mobile pickup tasks are
   not implemented yet; do not route supplier demand through outbound transfer loads.

## Confirmed ownership and warehouse document path

Goods become company-owned when collected from the supplier. Therefore:

`Purchase Order → Purchase Receipt into vehicle Transit Warehouse → normal Material Transfer → receiving warehouse`

- From the opted-in submitted PO use **Create → Purchase Receipt (Vehicle)**.
  Select an active submitted same-company allocation with vehicle and driver.
  Exactly one active Transit Warehouse must be linked to that vehicle. Review
  actual collected quantities and standard ERPNext batch/serial and QI details.
- This creates a normal draft PO-mapped Purchase Receipt, with PO item/UOM/rate
  lineage. Only submission by authorized purchasing/warehouse users posts stock
  and accounting; driver APIs receive no new purchasing/stock permissions.
- Once submitted, use the PR's **Create → Stock Entry (Material Transfer)**.
  Select an active same-company receiving warehouse. This produces a standard
  draft transfer from purchased vehicle stock, **not** Add-to-Transit/End Transit.
  The originally planned PO receiving warehouse is retained as a row-level hint.
- Factories return existing drafts on retry. Other vehicle-receipt drafts reserve PO
  quantities; draft/posted linked transfers reserve receipt quantities. Source
  locking, source-row/UOM validation and exact lot identity prevent overlapping
  collection/transfer quantities within this path. Standard ERPNext stock
  availability and buying checks still apply, including other stock workflows.
- Transfers read posted receipt bundles first and construct distinct standard
  outward bundles; legacy batch/serial fields remain fallback. Original incoming
  bundles are never reused as outgoing vouchers. Per-row partial transfer
  quantities/serial subsets are validated against untransferred receipt cargo.
- Cancel/delete downstream transfers before cancelling a vehicle PR. Standard
  ERPNext cancellation reverses stock and purchasing quantities. No separate
  Vuna inventory ledger or valuation logic is added.
- Supplier returns are not orchestrated in this slice. A linked draft/posted
  Purchase Receipt return or stock-updating Purchase Invoice return blocks this
  receipt's transfer helper until reconciled; returned cargo must not be reused.
- Site configuration matters: Items must permit the **Transit** Warehouse Type
  under `nl_vms` compatibility rules, and incoming/outgoing QI is not bypassed.
  The integration fixtures add valid test-only compatibility/QI and roll back.

Supplier task/POD integration must consume this stock path rather than receiving
the same purchased goods a second time at the depot. Posting PR at collection is
not proof of arrival; the receiving transfer remains a separate warehouse action.

Verification of this document path includes supplier receipt/transfer authority
and lineage tests, Desk action tests, and real rollback ledger cycles:

- `supplier_pickup_demand_smoke.run(verify_vehicle_stock=True)`;
- `supplier_pickup_demand_smoke.run(verify_vehicle_stock=True, batched=True)`;
- `supplier_pickup_demand_smoke.run(verify_vehicle_stock=True, batched=True, partial_transfer=True)`.

These check stock into vehicle transit, standard arrival transfer, source PO
received quantities, bundle identity, duplicate draft reuse, remaining quantities,
and cancellation. Test-only compatibility/QIs are rolled back; no production
policy or approval is weakened. Physical mobile supplier task testing is not yet
applicable, because that integration remains pending.

Verification: supplier normalization/policy tests, existing demand/load/customer
and depot pickup regressions, Desk source-action tests, Ruff/format/ESLint checks,
and rollback-only `supplier_pickup_demand_smoke.run`. The smoke uses a disposable
submitted-state PO, preserves requested UOM, verifies retries/cancellation guards,
and asserts no Stock Ledger, GL, Purchase Receipt or Stock Entry changes.

## Next slice (7B)

- Supplier-aware Transport Loads and allocation cargo preview: preserve an external
  pickup Address instead of requiring a company source Warehouse.
- Supplier pickup tasks and shared POD collection UI, including mixed delivery/
  pickup stop ordering and capacity/compatibility checks.
- Stable collection evidence, actual quantities and batch/serial handling without
  inventing company stock or granting drivers purchasing/inspection authority.
- Connect supplier tasks/proof to the implemented PR-to-vehicle path. Company
  ownership starts at collection; warehouse arrival is recorded by the later
  Material Transfer, never by a second Purchase Receipt.
- Partial pickup/receipt, duplicate prevention, cancellation, permissions and
  offline/device acceptance tests before marking Phase 7 complete.

Transport cost estimation, supplier invoice/payment workflows, supplier returns,
subcontracting and automatic route optimization remain outside this slice.
