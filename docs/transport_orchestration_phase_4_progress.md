# Phase 4: task-based Delivery Trip planning

## Implemented backend planning

- Warehouse-transfer allocations can generate Delivery Trips without customer Delivery Notes.
- Mixed allocations generate Delivery Notes for customer loads only; submitted customer notes are required before trip generation.
- New transfer/mixed trips contain standalone, read-only Transport Trip Tasks with requested quantity/UOM snapshots and internal Stock UOM quantities.
- Transfer loads expose Allocate Vehicle and subsequent allocation/trip actions through their execution summary.
- Supported tasks: Customer Delivery, Transfer Dispatch, Transfer Receipt.
- Warehouse stops use linked ERPNext Addresses; missing addresses block planning with an actionable message.
- Transfer tasks can share a stop with a customer delivery at the same address.
- A return to the origin is represented as another visit rather than a cyclic merged stop.
- Dispatch must precede receipt, and each task must be assigned exactly once.
- Draft stop order can be edited; submitted task/stop assignments and item snapshots are frozen.
- Existing customer-only trips retain their standard Delivery Note/POD execution path.
- One POD unit remains one Delivery Note. Multiple customer Delivery Notes at the same address retain separate ERPNext stop rows, while transfer tasks can attach to a matching stop.
- Trip Connections exposes tasks. Task editing is reserved for trusted planner/execution services, not driver or Desk form input.
- Assigned-trip API responses include task context. Transfer stops do not offer customer POD actions.
- Completion is derived from task outcomes; visited flags alone cannot complete a transfer.
- Customer task outcomes retain rejection/partial-delivery exceptions from submitted PODs.
- Cancellation cancels task plans and releases links safely; cancelled task history does not trap source-document cancellation.
- Deleting a draft trip removes its generated tasks.

## Deliberate boundary

This is the trip-planning layer, not transfer execution. It does not create, submit, or cancel Stock Entries. Transfer trip submission leaves transfer loads Ready for Dispatch, not physically dispatched in ERPNext stock.

Phase 5 must provide dispatch/receiving proof, authorized stock-document review, and the mobile task execution screens in the standalone mobile repository. Transfer tasks cannot currently be completed through an unrestricted status button.

Transfer Stock Entries and proof forms must retain the task's requested UOM and conversion factor, reconciling actual quantities in Stock UOM. These future execution forms are not implemented by the planning-only UOM update.

Supplier pickups, depot-return source adapters, automatic route optimization and actual onboard-cargo calculations remain outside this implementation slice.

## Verification

- Existing 259 application unit regressions pass on meru-dairy.localhost.
- Nine task-planning tests cover transfer-only planning, shared customer/transfer stops, repeat visits, precedence, task assignment integrity, source cancellation and completion guards.
- A rollback-only site smoke test creates and saves a transfer trip without Delivery Notes, verifies two generated tasks, checks repeat-save idempotency, and checks task cleanup when deleting the draft trip.
- The smoke test uses an isolated allocation fixture copied from a submitted allocation; it bypasses allocation scheduling only for that fixture. It never submits a trip or notifies drivers. All fixture records are rolled back.
- Full mixed-trip submission/POD integration and mobile task-card interaction still need end-to-end verification before release.
