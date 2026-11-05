# Phase 6 — depot-held rejection return pickup execution

## Implemented first slice

Submitted depot Return Requests generated from transfer rejection can generate opted-in Transport Demand. Each leg uses the Return Request's approved receiving warehouse and each item's original depot custody warehouse. Vehicle/Transit-held rows are excluded: already-loaded goods are not collected a second time.

The requested UOM, conversion factor and source item are preserved. Posted collections reduce live outstanding quantity; planned-but-uncollected quantities are released for replanning after a partial/failed pickup. Return pickups and outbound transfer rows stay in separate loads, but their loads can share an allocation/trip. Multiple returns at the same pickup/receiving locations can share physical stops while retaining separate tasks/PODs.

Return loads do not require pre-trip Add-to-Transit stock. Trips include a real **Return Pickup** task followed by **Transfer Receipt**. Reordering receiving before its own pickup is blocked. Normal outbound depot trips remain destination-only.

The existing Proof of Delivery DocType handles pickup (`Return Pickup`, direction `Pickup`). A started, assigned trip is required. Pickup records quantity collected, the depot release person's name, actual serial subset and a mandatory explanation for a short/failed collection. Only collected quantities create and submit a standard Add-to-Transit Stock Entry into the assigned vehicle's uniquely configured transit warehouse. The driver cannot choose stock destinations or arbitrary return items.

Destination receiving uses the existing POD/automatic End Transit implementation, against the actual pickup stock, not the original planned quantity. Return Request received balances derive from submitted receiving Stock Entries, not from pickup evidence alone. Receiving cancellation reopens those balances. Pickup POD cancellation requires its stock transaction to be cancelled/deleted first; source Return Request cancellation requires its transport demands to be released first.

Desk exposes Create → Transport Demand on submitted depot Return Requests, and Create → Proof of Pickup on pickup tasks. The main standalone mobile repository uses the existing trip-task route with quantity-collected/released-by wording; customer proof screens and the MDCU fork are unchanged. No APK is built.

## Configuration and test sequence

1. In Transport Settings → demand sources, add/enable **Return Request**. Manual is the recommended first test. Existing production settings are not silently enabled by this implementation.
2. Use a submitted **Depot** Return Request with stock physically held in a depot's holding warehouse. Review **Return Warehouse** as the actual receiving returns/inspection warehouse, not a presumed automatic return to saleable stock. This site's `nl_vms` auto-write-off rejected warehouses are not allowed as return receipt destinations.
3. Create and submit demand, select quantities into a return-only Transport Load, submit the load, allocate vehicle/driver, then create/submit the Delivery Trip. Pickup and receiving warehouses need linked Addresses.
4. Start the trip. Open the pickup task and submit pickup POD for actual collection. Confirm that only collected stock enters vehicle transit.
5. Open the receiving task and submit receiving POD. Confirm automatic End Transit and Return Request received balance. Partial uncollected quantities should remain available for a later load.

Standard ERPNext stock availability, whole-number UOM, valuation, batches/serials, quality requirements and ledger posting remain enabled. Stock-posting errors abort POD submission. Tests create genuine fixture QIs where the site's item policy requires them; production does not auto-approve or bypass QI. An operational pre-inspection/draft-stock preparation path for mandatory-QI items still needs work before rollout; the driver must not receive inspection approval powers.

## Verification

- Existing site-aware app unit regressions and focused planning/source-policy/POD tests.
- New adapter tests: depot-only sources, requested UOM, live remaining quantity, no duplicate collection of onboard stock, endpoints and submission prerequisites.
- New pickup validation tests: source identity, quantity limits, other collected quantities, exact serial subsets, required trip start, short-pickup explanation, failed-pickup no-stock and immutable source link.
- Rollback-only two-batch site cycle: original depot rejection → depot Return Request → return demand/load/trip → partial pickup → Add to Transit → receiving POD → End Transit → partial return progress → remaining-quantity replanning → cancellation reopening. Temporary settings, warehouses, QIs, stock and GL changes are rolled back; real driver assignments are not submitted/notified.
- Full collection/receipt reaches Awaiting Inspection and repeated demand creation returns the existing demand. Zero collection posts no Stock Entry and creates no fictitious destination receipt. Shared pickup/receiving visits and required pickup-before-receipt order are also tested.

## Original gaps identified after the first slice (see completion update below)

- Adapt Return Inspection to receiving Stock Entries. The existing custom inspection form still requires a customer Sales Return; this slice does not fabricate one for depot returns. Standard ERPNext QI remains authoritative meanwhile.
- Receiving-only recovery of goods already held in vehicle transit. Manual depot cases now have the unplanned collection path described below; pre-collection manual depot return planning is still separate work.
- Mandatory-QI operational preparation, serialized-item document-level cycles, concurrent collection stress tests and physical-device verification.
- Offline pickup-proof queue and richer signature/photo/GPS evidence requirements.

This is a testable pickup/receiving execution slice, not a claim that every Phase 6 acceptance scenario or inspection workflow is finished.

## Unplanned collection follow-up

This is now an optional exception: **Transport Settings → Features → Allow Unplanned
Return Pickups**, default off. The standard route remains an approved/submitted
Return Request with an assigned pickup task. Disabling the exception blocks new
reports and their draft submission, but does not strand previously collected goods:
history, dispatch review, stock receipt and cancellation remain available.

Active trips now support **Stop → Unplanned collections** in the standalone mobile app,
and **expand Delivery Stop row → Record Unplanned Collection** in Desk. Every report
references that existing stop. The party is derived; users supply the customer's
submitted Delivery Note or depot's incoming Stock Entry number before seeing its
item rows, original UOM and batch/serial choices. Sources must match the stop and
company. This reuses POD as submitted custody evidence,
without fabricating demand or a new stop. Dispatch links an existing
Return Request, or creates a draft depot case after approving its source stock
warehouse and receiving holding warehouse. Customers still require a legitimate
original sale for their normal Return Request/Sales Return flow.

Reviewed depot collections that have arrived can generate a standard draft
Material Transfer, submitted by warehouse staff under normal permissions. It moves
stock directly from its recorded depot warehouse to the receiving holding warehouse;
there is no fake historical Add-to-Transit posting. ERPNext bundles, QI, valuation
and accounting remain enabled. Stock quantities derive from the actual collection,
and cancel/retry handling preserves the evidence and reopens receipt/review as needed.
Reviewed collections are removed from future pickup demand. A report's warehouse
receipt must currently include all its collected rows together.

New checks: collection validation/authorization/float-precision tests; existing-case
matching and unplanned-quantity planning exclusion; rollback-only ordinary and
two-batch stock receipt/cancellation cycles; mobile TypeScript and Expo lint.
See architecture plan section 23 for the precise scope and user steps.

## Direct approved pickup assignment (2026-11-05)

Submitted customer and depot requests can now use **Actions → Assign Pickup to
Delivery Trip** to attach remaining approved quantities to an existing unfinished
stop. No demand/load/allocation is created, and unplanned pickups may stay off.
Dispatch permissions, same-company trip/driver, matching customer/depot, existing
workflow approval, duplicate bookings, and known batch/serial identities are checked.
The existing driver task/POD route is reused. Ordinary delivery stops require both
their delivery proof and pickup tasks before completion; original delivery context
on the Return Request is not overwritten by the pickup trip.

Customer pickup is evidence only, leaving inventory/credit to the existing RR return
documents. Depot pickup posts actual goods into vehicle transit; warehouse staff
use standard End Transit into the RR Return Warehouse. Partial collection and
receipt/cancellation balances are tracked. **Task → Actions → Remove Pickup
Assignment** releases work after any proof/stock has been cancelled in order.
Rollback integration checks cover both customer and depot paths; no new workflow,
APK or commits are included. Full Stock Entry-based Return Inspection and manual
pre-collection depot RR creation remain separate work.

## Phase 6 completion implementation — 2026-11-05

The previously missing operational paths are now implemented:

- Manual **Depot** Return Request against an older submitted incoming Stock Entry.
  Select the source warehouse and use **Actions → Get Received Items**. Original
  transaction UOM/factor and bundle-first batch/serial identity are retained.
  Approval locks the original receipt and rejects quantities/serials reserved by
  other active requests. ERPNext still checks real stock availability at posting.
- Existing planned or direct approved pickup tasks collect these requests. Only
  actual collection posts Add-to-Transit; standard End Transit confirms receiving.
- Receiving Stock Entry **Create → Return Inspection**. Inspection is against one
  request and one posted receipt, never a fabricated customer Sales Return.
  Multiple partial receipts have separate inspections. Draft inspection may be
  incomplete; submission requires exact per-source-row/per-batch reconciliation.
- Detailed accepted/rejected groups retain reasons and exact serial subsets.
  Result target warehouses are required on submission; header warehouse defaults
  remain optional. Ordinary rejected-holding warehouses retain inventory; the
  site's automatic-write-off warehouses are explicitly blocked.
- Submitted inspection creates a standard draft **Stock Entry (Material Transfer)**
  with exact batch/serial groups and destinations. Validation prevents moving
  another lot or swapping a rejected/accepted destination. Reasons remain in the
  inspection rather than becoming accounting logic.
- Goods already in vehicle transit use RR **Create → End Transit Stock Entry**;
  no pickup/demand/second loading movement is fabricated. This is a warehouse
  draft, not an automatic claim that physical receiving happened.
- Warehouse staff can save a draft pickup/receiving POD and use **Create → Stock
  Entry for Quality Inspection** to prepare its standard stock/QI documents.
  Stock cannot be submitted against an unsubmitted proof. Submitting the POD
  reuses and validates the exact prepared draft; changed quantities require the
  draft/QI to be reconciled. No QI auto-approval or validation bypass was added.
- Standalone mobile pickup/transfer PODs use the existing offline outbox with
  stable request IDs and exact saved evidence. Available task templates are cached
  when the stop is opened online. A destination that has not yet been prepared
  from posted pickup stock cannot be invented offline. Sync Center reports pending
  handovers; only successful server synchronization posts stock. Unplanned
  collections remain an online-only opt-in exception.
- RR receipt, accepted/rejected and resolution progress is derived from submitted
  ERPNext receipts, inspections and redistribution. Cancel receiving/redistribution
  in dependency order; cancelling redistribution or inspection reopens progress.

### Verification and testing handoff

- New manual-source/approval and inspection boundary tests, including original
  voucher locking, existing reservations, wrong company/item/batch, nonfinite and
  excessive quantities, partial draft QC, mandatory per-group QI and serial splits.
- Rollback-only real two-batch cycle: manual RR → planned pickup → Add-to-Transit
  → End Transit → split inspection → redistribution → cancellation reopening.
  The draft-stock/QI preparation route is exercised without mocking pickup or
  return-receipt submission. Genuine fixture QIs satisfy site policy.
- Rollback-only vehicle-held recovery/inspection/redistribution/cancellation cycle.
- Rollback-only serialized manual RR → direct assignment → partial mobile pickup
  → standard End Transit → receiving and pickup cancellation.
- Existing pickup/direct-assignment/demand/transfer/task regressions and Desk
  cancellation UI checks. Mobile typecheck, lint and offline queue tests cover
  stable IDs, connectivity failure and refresh failure after successful posting.

Physical-device acceptance, network-loss testing on a device, and a realistic
multi-dispatcher load/concurrency exercise still require deployment/UAT. Richer
pickup signature/photo/GPS policy has not been added. No APK, commits or pushes
were included in the implementation checks above.

### Commit handoff

- Backend implementation and regression coverage: `936e5ba` on
  `feature/transport-orchestration-v2`.
- Standalone mobile integration and offline handover tests: `a71d771` on `main`.
- Pickup assignment now filters trip choices to active trips with an unfinished
  stop matching the request's customer or depot (including that depot's configured
  returns holding warehouse). The stop list uses the same matching rule, and the
  final assignment still validates it server-side. The matching-stop lookup is
  verified in the rollback depot pickup cycle.
- Changes are committed locally; no push or APK build has been performed.
