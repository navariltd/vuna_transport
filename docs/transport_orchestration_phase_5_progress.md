# Phase 5: warehouse-transfer execution

## Depot-only rejected-goods custody split

The Desk and mobile UI now use **Where Are the Rejected Goods?** with three choices: **All remain on vehicle**, **All received by depot**, and **Split between depot and vehicle**. Only Split shows a depot-quantity input and calculated vehicle balance; the two all-or-nothing choices derive both quantities automatically. All-depot also derives the depot serial subset from all rejected serials; Split requires the explicit subset. Server normalization is authoritative and rejects invalid split endpoints on submission. Older clients/drafts without the selector retain their quantity-based interpretation.

Transfer Receipt POD rows now record `depot_received_rejected_qty` in the requested UOM. It is part of, not additional to, `rejected_qty`. The balance is derived as `rejected_with_driver_qty`. Example: 20 rejected cartons, 15 received by depot, 5 retained on vehicle. Serial-controlled rows also require the exact rejected serial subset unloaded at the depot; batches remain grouped by their dispatched identity.

Configure **Returns / Quarantine Warehouse** (`vuna_returns_warehouse`) on each receiving Warehouse before recording depot-held rejection. It must be an active, distinct, same-company, non-transit leaf warehouse. Drivers never select arbitrary stock destinations. On sites with `nl_vms`, warehouses marked **Rejected Warehouse** are blocked here because that app automatically reconciles receipts to zero and expenses them. Use an ordinary holding/quarantine warehouse so stock remains physically and financially recorded pending inspection. No actual depot mappings are guessed or configured by this implementation.

POD submission automatically posts one End Transit Stock Entry: accepted rows to the intended receiving warehouse, depot-held rejected rows to its configured quarantine warehouse. Each row retains POD-item/outcome identity, requested UOM, source stock row, batches and serial subsets. Driver-held rejection and missing stock remain in transit. Full rejection with physical depot receipt still posts stock; an entirely driver-held rejection creates no empty receipt. Cancelling the receiving entry reopens stock-posting progress, including an all-rejected depot receipt.

The generated depot Return Request preserves the rejected quantities split by their current custody warehouse and serial subset. Mixed custody has no misleading single source warehouse; its child rows identify both locations. This is a snapshot of the confirmed POD handover, not implementation of subsequent pickup/diversion or a stock return to the factory. Customer delivery semantics are unchanged. Desk and the main standalone mobile transfer POD screen expose the split; existing APKs and the MDCU fork are not rebuilt or updated.

Verified: 259 app unit regressions, focused custody/proof/transfer tests, and rollback-only two-batch driver-held, split-custody and fully-rejected depot-receipt cycles. Actual quarantine Bin quantities and downstream cancellation are checked. Fixture mappings, QIs, warehouses and Stock/GL changes are rolled back. Mobile TypeScript and Expo lint pass; physical-device testing remains required.

## Automatic End Transit on POD submission (supersedes manual receiving action)

Submitting a Transfer Receipt POD now creates and submits its accepted-only End Transit Stock Entry in the same request transaction. It reuses the existing identity/UOM-preserving mapper and active-entry uniqueness lock. An existing submitted entry is reused; an existing draft is submitted. Zero accepted quantity creates no empty stock transaction. Rejected quantities still generate the depot Return Request and remain in transit.

The non-whitelisted submission helper is reached from the validated POD controller, including mobile POD submission. It uses narrowly scoped document permission overrides for the exact derived stock/bundle documents, not general driver stock permissions. ERPNext's stock, bundle, valuation, accounting and mandatory Quality Inspection validations remain enabled. Posting failure propagates and prevents successful POD submission; no background job, swallowed error or intermediate commit is introduced. Mandatory QI is not fabricated or bypassed; sites requiring it must provide valid inspection evidence before stock can post.

The manual Create → Stock Entry (End Transit) action is removed from POD; posted entries remain accessible through Connections. Already submitted PODs are not retroactively posted by this change.

Verified 259 app unit tests, 43 focused proof/transfer tests, and rollback-only two-batch partial-rejection auto-posting and downstream-cancellation cycles. Site smoke supplies real accepted fixture QIs before auto-posting; this is test setup only, not production auto-approval.

## Destination-only driver stops (2026-11-04)

Loading is completed through Vehicle Allocation before trip creation: submitted customer Delivery Notes and submitted Add-to-Transit Stock Entries remain prerequisites. Outbound depot transfers create only Transfer Receipt tasks at receiving destinations; they do not create loading stops or require loading PODs. One depot destination means one stop; a separate customer delivery plus a depot delivery means two stops.

The driver starts the trip and records the destination POD. An authorized user reviews/posts End Transit for accepted quantities; rejected quantities retain the Return Request flow. Departure still validates posted dispatch stock.

Existing submitted manifests are not rewritten. Mobile responses omit legacy loading-only stops and tasks, while retaining customer/receiving work at shared stops. Legacy loading tasks derive completion from their posted dispatch Stock Entry, not a second driver confirmation. The historical dispatch-proof descriptions below are superseded by this flow.

Cancellation revision: the Desk trip form applies exclusions during refresh, after ERPNext resets its standard list. Upstream allocations, loads, Delivery Notes and internal task records are excluded; PODs, Return Requests and downstream receiving Stock Entries remain discoverable through Frappe's normal cascade. Serial and Batch Bundles are excluded from direct traversal because ERPNext cancels them through their owning stock voucher. Submitted proofs must be cancelled before the trip, including through the standard cascade. Cancelling the route, even after departure, does not reverse allocation-bound loading stock; the transfer load stays Dispatched while that stock remains posted. Legacy trip-bound dispatch stock still requires explicit cleanup.

Verified both no-downstream cancellation/replacement and the actual Frappe downstream cascade (depot Return Request, receiving Stock Entry, POD, then trip) using rollback-only site fixtures. A JavaScript regression test reproduces ERPNext resetting the list on refresh and checks that upstream exclusions persist without excluding downstream documents.

Verified: 259 site-aware app unit tests and 52 focused transfer/planning tests pass. The rollback-only site cycle verifies destination-only planning, mobile departure without a loading POD, destination rejection → depot Return Request, partial End Transit posting and receipt cancellation; all fixture documents and ledger changes are rolled back.

## Unified POD (supersedes the experimental proof model)

- One Proof of Delivery / Proof of Delivery Item model handles customer delivery and stock-transfer handovers. The experimental Transport Execution Proof and its child DocType have been removed.
- POD stores handover type, direction and operation/task linkage. Customer POD remains linked to its Delivery Note; transfer POD is linked to its dispatch Stock Entry and exact stock/batch groups.
- Canonical quantities are existing `ordered_qty`, `delivered_qty` (accepted) and `rejected_qty`, with Stock UOM snapshots. Transfer POD additionally derives `missing_qty`; missing goods are not implicitly rejected goods.
- Explicit transfer rejection creates one draft depot Return Request from that POD. It retains rejecting depot, current transit custody, requested return warehouse, UOM, batch/serial identity and rejection reasons. No customer or credit document is fabricated.
- End Transit receives accepted quantities only. Rejected goods remain in transit for the Return Request; missing goods remain an unresolved variance. Depot return follow-up uses stock transfers, not customer Sales Returns.
- An existing assigned task's draft POD can be completed through the constrained mobile API; retries still validate request ID and payload hash.
- Direction includes Pickup and POD has the task link for future return-pickup execution. The actual pickup adapter, inbound demand planning and return-stock receipt workflow remain Phase 6; this change does not pretend to execute pickup tasks yet.
- Existing customer POD submission, rejection return creation, cancellation and mobile/offline behavior remain in place.
- The two existing development proof records were copied into POD, preserving document/item identifiers, quantities, timestamps, evidence links and stock references, verified before removing the old metadata. Historical shortages remain unaccounted rather than guessed rejection. Backup: `20261104_140934-meru-dairy_localhost-database.sql.gz`.
- Verified 259 app unit regressions, 69 focused tests and a rolled-back two-batch depot rejection → Return Request → accepted-only End Transit cycle. Mobile TypeScript and lint pass.

## Revised pre-trip flow (2026-11-04)

Material Request → submitted Transport Demand (source optional) → draft Transport Load (source optional; mandatory before submission) → Vehicle Allocation → submitted Add-to-Transit Stock Entry → Delivery Trip → handover/receiving proof → reviewed End Transit Stock Entry.

Drafts can be created directly from Transport Demand form/list actions and Dispatch Planning without a loading-warehouse prompt. Submitted-load warehouse changes are blocked once active dispatch Stock Entries exist.

The load's Recommended Vehicle Category is advisory, not an exact-category restriction on allocation or link searches. The allocated vehicle supplies the actual category, and capacity summaries use the selected allocation category. Capacity/minimum thresholds, refrigeration, body/equipment requirements, delivery-access limits and cargo-group compatibility remain enforced. Loads with different category recommendations can share an allocation if the actual vehicle satisfies their combined requirements.

Verified source-free draft save → blocked submission → warehouse selection → successful submission in a rollback test. The reported FH-recommended load `TL-04-11-2026-00004` passes the read-only Canter cargo-compatibility check. All 259 app unit regressions and 77 focused tests pass.

- Vehicle Allocation and Transport Load expose the next stock-preparation action. Each transfer load gets its own stock entry, which may contain multiple demands and destinations; an allocation can prepare multiple load stock entries before creating one trip.
- Vehicle → Warehouse.vehicle resolves exactly one active, same-company Transit Warehouse. Missing/ambiguous mappings require explicit configuration; no warehouse is created or guessed in real operation.
- Existing Stock Entry and item `vehicle` / `driver` fields are populated from the allocation when present on the site. Receiving entries retain those defaults.
- Allocation stock entries link directly to allocation/load and load rows, not to tasks that do not exist until trip creation. Per-stop proof selects only its demand's cargo from a shared dispatch entry.
- Both allocation actions and trip creation enforce posted transfer stock, alongside submitted customer Delivery Notes for mixed allocations.
- Driver-facing depot drop-offs are deliveries; loading is pre-trip warehouse work, not another driver stop. Transport Trip Task remains the internal execution/audit record; return/supplier pickup Tasks are separate inbound work for later adapters, not a relabelling of outbound deliveries.
- Fixed transfer-trip detail serialization when its source is a Frappe Document rather than a list-response dictionary.
- The standalone `apps/vuna_transport_mobile` repository now has depot-delivery navigation, online handover confirmation, batch-group quantities, serial selection and confirmation review. These changes are not in the MDCU fork or an already-built APK. Offline transfer-proof queueing remains pending.

## First slice: dispatch stock preparation

Implemented:

- Transport Trip Task → Create → Stock Entry (Add to Transit).
- Requires a submitted transfer trip, demand and load, plus Stock Entry create permission and transport-document access.
- Uses ERPNext's Material Request mapper, then selects only planned cargo and restores its requested UOM, conversion factor and canonical stock quantity.
- User selects an active same-company Transit warehouse. ERPNext remains responsible for source stock availability, batch/serial selection, valuation, Stock Ledger, GL, and stock submission.
- Links Stock Entry to its dispatch task and each stock row to the exact task item. Connections exposes the stock documents.
- Reuses an existing active draft/submitted dispatch entry. Trip/task locks prevent concurrent saves from creating duplicate active entries.
- Server checks exact cargo totals and MR lineage. Multiple stock rows may represent one cargo row, subject to normal ERPNext UOM and serial/batch validation.
- Short dispatch requires replanning rather than silently changing the submitted manifest in this first slice.
- Mobile trip departure requires submitted Add-to-Transit entries for all transfer-dispatch tasks. Customer-only trips remain unchanged.
- Posted dispatches update the load's dispatch state; cancellation before departure restores readiness. Dispatch cancellation after departure is blocked pending a proper reconciliation workflow.
- Trip cancellation requires cancellation/deletion of active dispatch entries first.
- Remaining-demand calculations distinguish posted commitments from unposted reservations so MR fulfilment is not deducted twice.

## Verification

- 46 isolated tests cover planning, requested UOM, task integrity, dispatch invariants, duplicate reuse, departure/cancellation guards and remaining-demand arithmetic.
- Rollback-only site smoke creates and saves a Carton-based dispatch draft, verifies reuse and departure blocking, and confirms no Stock Ledger posting.
- The smoke uses isolated submitted-state fixtures without submitting a trip or notifying drivers. It does not submit any Stock Entry.
- Metadata migrated on `meru-dairy.localhost`.
- All 259 existing app unit tests pass; Python lint/formatting and task JavaScript lint pass.

## Site configuration discovered during testing

`nl_vms` enforces Item / Item Group Allowed Warehouse Types. Items being transported must allow `Transit`; the sample item currently excludes it. Real policy has not been changed. The smoke adds Transit only inside its rolled-back fixture. Vuna does not bypass that validation.

A subsequent standalone customization sync also exposed an existing Delivery Note Item custom Link pointing to the removed Return Receipt Item DocType. That stale returns metadata is outside this dispatch change and needs a separate cleanup; the initial migration and dispatch smoke passed. Do not treat an all-app customization resync as verified clean.

## Remaining Phase 5 work

1. Offline transfer-proof queue integration and physical-device verification of the new online depot-delivery screens.
2. Explicit follow-up/reconciliation workflow for stock left in transit after a partial/failed attempt; it is not silently written off or received elsewhere.
3. Serialized-item document-level integration tests, concurrent receiving/retry stress tests, and full mixed customer/transfer mobile scenarios.
4. Configurable signature/photo/GPS evidence requirements. This slice records confirming person, server recording time/user, quantities and mandatory variance remarks, not signatures/photos.

## Second slice: transfer proof and receiving backend

- Extended existing Proof of Delivery and item DocTypes. Tasks expose Create → Proof of Delivery; proofs expose stock documents through Connections.
- Receipt proof requires a started trip and submitted Add-to-Transit stock. The former loading-proof requirement has been removed.
- Confirmed person is required on submission; partial/failed receipts require remarks. All dispatched batch groups remain on the evidence, including zero received quantities.
- Batch/serial identity comes from the posted bundle first, with legacy fields as fallback. Receipt quantities and chosen serials are validated against each dispatched identity group, not an assumed first batch.
- Requested UOM and conversion factors are restored server-side. Standard whole-number UOM rules apply; packaging splits must use a compatible UOM configuration.
- Create → Stock Entry (End Transit) prepares a standard ERPNext Material Transfer against the outgoing entry, using ERPNext's document-mapping pattern. Proof quantities are applied before creating fresh outward bundles in the transit warehouse; full dispatch bundles are never blindly copied for partial receipts.
- End Transit rows must exactly match the submitted evidence, source rows, warehouse, UOM and batch/serial identities. Stock creation/submission still requires normal ERPNext permissions. Receiving quality inspections remain standard ERPNext checks, and outgoing QI links are not reused as receiving QIs.
- A submitted receipt proof waits in Awaiting Stock Posting. Matching posted stock makes the task Completed / Completed with Variance. A failed attempt posts no receipt stock and keeps the stock in transit.
- Cancelling End Transit reopens task/trip execution rather than leaving stale completion. Active linked stock must be cancelled/deleted before proof cancellation.
- Driver API endpoints `prepare_mobile_transport_proof` and `submit_mobile_transport_proof` authorize against the mobile device and assigned trip. Submission accepts only constrained evidence fields, never arbitrary ERPNext documents or warehouses. Stable request IDs plus payload hashes make submitted retries idempotent; conflicting ID reuse is rejected.
- Stock posting alone does not complete a transfer task; execution proof is also required.

Additional verification:

- 64 focused tests pass, including vehicle-warehouse resolution, Document-based mobile serialization, identity limits, multiple batches, serial subsets, offline retry/conflict checks and assigned-driver authorization.
- A rollback-only document-level test passes dispatch posting → proof → partial receipt proof → End Transit posting → cancellation. Two batches are carried in one outgoing bundle and split into proof/receipt rows against the same original stock row. Requested Carton UOM is retained and ERPNext's transferred/outstanding quantities reverse correctly on cancellation.
- The full-cycle smoke creates disposable stock and accepted standard Quality Inspections, and rolls back all test documents and Stock/GL Ledger changes. It never submits a real driver assignment/trip or disables the site's stock/QI policies.

This is the receiving backend, not a completed mobile rollout or a complete variance-reconciliation module.
