# Transport orchestration: Phase 2 implementation status

## Implemented

- Rename Delivery Load to Transport Load and Delivery Load Order to Transport Load Item.
- Update runtime DocType references, Desk routes, reports, dashboards, links, and tests.
- Pre-model-sync rename patch preserves existing records instead of deleting/recreating them.
- Use TL naming by default, retaining historical DL identifiers.
- Add demand/item references, destination warehouse, and Stock UOM to load items.
- Allocate warehouse-transfer demand in Stock UOM; retain transaction-UOM allocation for legacy customer delivery rows.
- Lock demand parents during allocation and check existing draft/submitted commitments.
- Recheck source eligibility and outstanding quantities on load save/submission.
- Reject duplicate rows, over-allocation, non-finite quantities, company/origin mismatches, and fractional whole-number UOMs.
- Provide Material Request Create → Transport Demand and Transport Demand Create → Transport Load actions.
- Provide multi-demand bundling through the Transport Demand list action.
- Block transfer loads from delivery-only stock and trip-generation helpers.
- Block cancellation of demand with active load allocations.
- Add a depot-transfer planning panel with source/date filters, editable Stock UOM quantities and load creation.
- Derive demand allocation status and per-item reserved, allocated and remaining balances on load save, submit, removal, cancellation and deletion.
- Guard source cancellation for Material Request, Delivery Order and the originating Sales Order.
- Backfill demand links for eligible unexecuted legacy customer loads; retain their existing execution path.

## Compatibility decisions

Existing `delivery_orders`, `delivery_loads`, `delivery_load`, and `vuna_delivery_load` field names remain temporarily in use. Mobile response keys and existing service import paths remain stable. The new DocType names are real changes, not translated labels.

Customer delivery loads continue using Delivery Order lineage. Backfilled demand links do not turn them into warehouse-transfer loads. Mixing customer delivery and transfer rows within one load remains blocked until generic trip execution exists. Dispatch Planning now has separate customer-delivery and depot-transfer sections; the latter does not use customer-specific filters or sales-load suggestions.

Historical post-model-sync patch targets now use the new DocType names so installation can proceed after the pre-sync rename. Historical source-field identifiers remain unchanged.

## Validation performed

- 26 isolated demand, allocation, lifecycle, and rename tests pass.
- Changed Python lint and formatting checks pass.
- Changed tracked JavaScript ESLint checks pass.
- Backed up and successfully migrated `meru-dairy.localhost` on 2026-11-03.
- Verified 43 Transport Load records, no legacy Delivery Load DocType, the Transport Load Item table link, and TL naming default.
- All 259 application unit tests pass on the migrated site, including 33 Transport Load regressions.
- A rollback-only database smoke test passed demand generation/retry, submission, draft allocation, and reservation release using an existing Material Transfer Request. Test records were rolled back.
- Extended rollback-only smoke checks pass load submission/cancellation, reserved-to-allocated balances, source cancellation protection, planning queries and reservation release. A second database connection was unable to bypass the demand row lock.
- Backfill rerun reported no duplicate/new links, 26 loads skipped because stock execution already exists, and six ineligible/incomplete source entries skipped. Historical quantities were not guessed or rewritten.
- A fresh-install migration and a full competing-allocation stress test have not yet been exercised.

## Still pending before release

1. Verify the fresh-install migration path.
2. Add full customer-delivery/POD document-level integration tests beyond the passing unit regressions.
3. Stress-test competing allocations and cancellation beyond the passing database-lock smoke check.
4. Review incomplete historical source records before attempting their optional backfill.
5. Migrate link field names and coordinate mobile API compatibility separately.
6. Implement cargo preview (Phase 3), followed by generic trip tasks and transfer execution.

No stock posting or mobile trip-execution behaviour is added in this phase.
