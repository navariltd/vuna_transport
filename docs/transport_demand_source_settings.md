# Transport Demand Source Settings

Transport Settings now uses a `Transport Demand Sources` child table rather than one default source DocType.

## Supported sources

| Source | Flow | Scope |
| --- | --- | --- |
| Sales Order | Sales Order → Delivery Order → Transport Demand | Customer deliveries |
| Material Request | Material Request → Transport Demand | Material Transfer only |
| Return Request | Approved depot Return Request → Transport Demand | Depot pickup; direct assignment remains available |
| Purchase Order | Opted-in submitted Purchase Order → Transport Demand | Supplier-demand foundation; load/task execution is the next Phase 7 slice |

Arbitrary DocTypes cannot be selected. Purchase Order is opt-in at both levels:
add an enabled source-policy row in Transport Settings, then enable **Company
Pickup** and choose a linked **Supplier Pickup Address** on the Purchase Order.
No source-policy row is added or enabled automatically by this implementation.
Target warehouses and required dates come from PO item rows (standard header
defaults are respected). Quantities remain in PO transaction UOM; `received_qty`
is subtracted in that UOM before converting to Stock UOM.

Supplier demand currently stops at the reviewable/submittable demand document.
It cannot yet enter warehouse-transfer loads or generate supplier driver tasks.
The separate warehouse-side **Purchase Receipt (Vehicle)** action receives owned
supplier goods into the allocated vehicle's Transit Warehouse. Authorized users
review actual quantities, batch/serial identity and QI before submitting. From
that submitted Purchase Receipt, **Stock Entry (Material Transfer)** creates the
normal transfer to a receiving warehouse. Neither action submits stock automatically.
See
`transport_orchestration_phase_7_progress.md`.

## Per-source controls

- **Enabled:** permits new generation for that source. Existing demands and load history are retained.
- **Creation Mode:** Manual exposes the source creation action; Automatic also generates from submitted source events.
- **Required Workflow State:** optional exact `workflow_state` required before generation. Submission is always required, even with this setting empty.
- **Auto Submit Delivery Orders:** customer-delivery intermediary only. If disabled, an authorized user must submit the Delivery Order before Transport Demand can be created.
- **Auto Submit Transport Demand:** submits newly generated operational demand. It never submits stock or accounting documents, and does not retroactively submit existing drafts.

Automatic generation is an internal, policy-checked hook operation. Manual RPC generation retains the caller's create/read/submit permissions and company checks. Draft Demand submission remains an authorized action when automatic submission is disabled.

## Migration

- Existing auto-create and auto-submit Delivery Order choices are copied to the Sales Order row.
- Sales Order demand auto-submission defaults off.
- Material Request starts enabled, in Manual mode, with demand auto-submission off.
- The old single-source and automation fields remain hidden temporarily for legacy compatibility; populated table policies are authoritative.
- Re-running migration does not overwrite configured rows.

## Meru Dairy verification

Installed on `meru-dairy.localhost` on 2026-11-03. Migrated customer configuration is Automatic generation, automatic Delivery Order submission, and draft Transport Demand.

Validation: 259 application unit regressions, 33 focused tests, and the rollback-only demand/load database smoke test pass. Existing demand retry behaviour and cancellation guards remain intact.
