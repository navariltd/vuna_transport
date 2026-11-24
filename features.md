# Vuna Transport feature overview

This overview describes the version-16 release line. Configure the source policies, Delivery Methods, permissions and stock prerequisites before using an operational workflow.

## Plan the work

- Create customer Transport Demand from eligible Sales Order rows. Delivery Method and the Item's transport requirement determine eligibility.
- Plan depot transfers from Material Requests, supplier collections from Purchase Orders, and pickups from approved Return Requests.
- Configure automatic or manual demand creation and source approval requirements in Transport Settings.
- Use Dispatch Planning's Orders, Depots and Suppliers tabs to select item quantities and build Transport Loads.
- Show warehouse stock in the requested transaction UOM and preserve transaction UOM through supported downstream movements and proofs.
- Reserve partial quantities without allocating the same outstanding demand twice.
- Validate company, origin, grouping, handling, compatibility, vehicle-access restrictions and capacity at the relevant planning stage.
- Flag customer demands with stale Sales Order grouping for review without blocking healthy demands on the planning page.

## Prepare vehicles and drivers

- Maintain category capacities and individual vehicle body configuration and refrigeration capability.
- Review vehicle availability using transport enablement, compliance, availability blocks, assets and repairs, allocations, trips and incidents.
- Check driver status, licence validity and overlapping allocations or trips.
- Allocate one vehicle to multiple Transport Loads and review the combined cargo.
- Select a vehicle first where appropriate and populate its category and related information.
- Enforce physical-vehicle refrigeration when any selected load requires it.
- Apply capacity override allowances only where configured and authorized.
- Record incidents and manage supported replacement allocations without losing the allocation history.

## Dispatch and execute

- Prepare customer Delivery Notes and depot in-transit Stock Entries from the allocated work before creating a Delivery Trip.
- Present customer deliveries, depot deliveries and pickups as identifiable stops and tasks.
- Assign approved return pickups and supported supplier collection demands to existing trips where appropriate.
- Record item-level actual quantities, rejection reasons, recipient details and supporting evidence using Proof of Delivery.
- Complete supported depot receipts through End Transit stock movements and reconcile ERPNext receipts made outside the mobile proof workflow.
- Receive company-owned supplier collections into the vehicle warehouse through Purchase Receipts when the configured driver workflow permits it.
- Control driver Purchase Receipt and End Transit posting through Transport Settings.

## Handle rejected goods and returns

- Create Return Requests for rejected goods and arrange approved customer or depot pickups.
- Record whether rejected goods are left at the receiving location, remain on the vehicle, or are split between those outcomes.
- Move depot returns through vehicle transit stock and receiving stock movements.
- Inspect received returns and link accepted and rejected outcomes to Quality Inspections created explicitly by users.
- Use Shipment Diversion for approved onward delivery to another customer or depot, with stock and sales-document traceability.
- Permit unplanned return collection only when enabled in Transport Settings.

## Plan routes and estimate costs

- Maintain zones, routes and location information for planning.
- Use permission-checked Google road-route previews and managed optimization with administrator-controlled limits.
- Review and apply planned stop order, including allocation journeys spanning multiple loads, and carry supported order into Delivery Trips.
- Retain applied road-distance estimates and recalculate configured transport cost estimates when applied trip routes change.
- Configure cost components, structures and effective assignments by relevant vehicle, category, supplier and location context.
- Keep transport estimates separate from ERPNext accounting entries and actual expense settlement.

## Support drivers on mobile

The separate Vuna Transport mobile app provides:

- Site onboarding, authenticated driver access and registered-device checks.
- Assigned trips, stops, deliveries and collection tasks.
- Shared proof forms, signatures, photographs and automatic GPS evidence capture.
- Offline trip information and a retryable proof synchronization queue.
- Material Request and Expense Claim entry and request history.
- Incident reporting and map-provider preferences from the connected site.
- Native Google maps and Android navigation in compatible builds, with customer-site credentials.
- Device approval, revocation and administrator reassignment controls.

Mobile capabilities depend on the installed app release, site configuration, device permissions and connectivity. Offline proof support does not imply every operation is available offline.

## Monitor operations

Use the Dispatch Operations and Fleet Management workspaces to review demand, unallocated loads, dispatch readiness, active trips, outstanding proofs, returns and fleet restrictions. Reports include fleet availability, vehicle compliance, expiring documents and allocation history.

## Scope boundaries

The release does not claim continuous GPS fleet tracking, telematics, automatic carrier settlement, universal offline operation or a bundled self-hosted road-routing service. Google services need appropriate credentials and can incur charges. Mobile apps are built and released independently of this backend.
