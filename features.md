# Vuna Transport Features and Development Roadmap

This document records the current Vuna Transport functionality, the architectural decisions already made, and the remaining work planned for the React Native Driver App and the wider transport platform.

Vuna Transport is an ERPNext/Frappe transportation management application. ERPNext remains authoritative for stock, accounting, Sales Orders, Delivery Notes, Customers, Addresses, Warehouses, Vehicles, Drivers, Employees, Assets, and HRMS transactions where applicable.

## Current operational flow

```text
Sales Order
    ↓
Delivery Order
    ↓
Delivery Load
    ↓
Vehicle Allocation
    ↓
Delivery Note(s)
    ↓
Delivery Trip
    ↓
Proof of Delivery
    ↓
Delivery Exception / Return Request where required
```

## Implemented functionality

### Application structure

- Vuna Transport is implemented as a Frappe/ERPNext application.
- HRMS is a required dependency for employee and driver-related functionality.
- Fleet Management and Dispatch Operations have separate workspaces and sidebars.
- Fleet and dispatch services are organized into domain-oriented service folders.
- Standard ERPNext/Frappe DocTypes are reused wherever they are the system of record.
- Standard custom-field fixtures are exported using Frappe's built-in `export_customizations` function.

### Transport configuration

- Transport Settings stores default weight, volume, and distance UOMs.
- Default UOMs are configured as:
  - Weight: Kg
  - Volume: Cubic Meter
  - Distance: Kilometer
- Transport Settings controls capacity override behaviour and capacity buffer rules.
- Delivery Method represents customer/business fulfilment, not the source of transport capacity.
- Vehicle Allocation represents whether transport capacity comes from company-owned, leased, or supplier vehicles.
- Delivery Note generation can be configured relative to Vehicle Allocation according to the selected workflow.
- Transport Compatibility Group is separate from Delivery Group and controls physical load compatibility.

### Sales Order to Delivery Order

- Submitted and eligible Sales Orders generate Delivery Orders.
- Service items are excluded from Delivery Order generation.
- One Sales Order may generate multiple Delivery Orders.
- Delivery Order grouping supports base dimensions such as:
  - Sales Order
  - Required delivery date
  - Delivery address
  - Source warehouse
  - Delivery method
  - Delivery Group
- Delivery Group is an Item-linked master used to split demand into Delivery Orders.
- Items without a Delivery Group may remain together when other grouping dimensions match.
- Delivery Group is snapshotted onto Delivery Orders and Delivery Order Items.
- Delivery Order generation is idempotent and avoids duplicate Sales Order Item demand.
- Delivery Order creation can be configured for automatic submission.
- Sales Order status and eligibility are checked before Delivery Order generation and downstream execution.
- Delivery Order quantities support requested, allocated, unallocated, and partial allocation states.
- Delivery Order Item quantities are tracked at item-line level.
- UOM conversion is respected when calculating transport quantities and downstream allocations.
- Delivery Orders can be regenerated after a Delivery Order is deleted where the source Sales Order Item remains eligible and unfulfilled.

### Geography and addresses

Customer delivery geography is stored primarily on ERPNext Address and copied to Delivery Order snapshots.

Current hierarchy:

```text
Territory
    ↓
County
    ↓
Constituency / Sub-county
    ↓
Town
```

Implemented geography functionality:

- Standard ERPNext Territory is used for the top-level geography.
- County is a Vuna master linked to a non-group Territory.
- Constituency is a Vuna master linked to County.
- Town can link to Constituency and derives/validates its County.
- Existing county-only Town records remain supported for backward compatibility.
- Address contains Vuna Link fields for Territory, County, Constituency, and Town.
- Address values synchronize to relevant ERPNext display fields where appropriate.
- Address geography queries are filtered by their parent geography.
- Delivery Orders snapshot Territory, County, Constituency, and Town.
- Dispatch Planning and load suggestions can filter by Delivery Order geography snapshots.
- Address location supports latitude, longitude, Geolocation, and device-location pinning.
- Source Warehouse and customer destination locations can be snapshotted for planning.

### Delivery Groups and transport compatibility

- Delivery Group is a simple Item delivery-demand grouping master.
- Transport Compatibility Group is a separate Item-linked master.
- Delivery Group answers:

  ```text
  Which Item demand belongs in the same Delivery Order?
  ```

- Transport Compatibility Group answers:

  ```text
  Which Items or Delivery Orders may share a Delivery Load?
  ```

- Different Delivery Groups may share a Delivery Load when their transport compatibility is valid.
- Incompatible Transport Compatibility Groups are rejected server-side.
- Delivery Load errors identify the incompatible groups without exposing unnecessary item-level detail.

### Delivery Load and dispatch planning

- Delivery Load is the operational planning core.
- Loads may contain multiple Delivery Orders.
- A Delivery Order may be split across multiple Delivery Loads and Delivery Notes.
- Delivery Load items support item-level allocations and partial quantities.
- Delivery Load item rows show Delivery Order, item, Delivery Group, Transport Compatibility Group, quantity, and transport measurements.
- A Delivery Load can be manually composed using item-level quantities.
- Selecting a Delivery Order without entering item quantities allocates the eligible remaining quantities by default where supported.
- Load totals update when quantities change.
- Delivery Load validation checks:
  - Over-allocation
  - Cancelled or closed source demand
  - Capacity
  - Transport Compatibility Group
  - Vehicle Category requirements
  - Delivery Method compatibility
  - Delivery Note lineage
- Source warehouse and dispatch details can be adjusted within the supported execution flow.
- Picking Lists are optional.
- Direct Delivery Note generation is supported where configured.
- Delivery Notes are linked to exact Delivery Load allocation rows.
- Duplicate Delivery Note generation is prevented.
- Delivery Note quantities cannot exceed the associated load allocation.
- Cancelled Delivery Loads are excluded from active allocation and availability calculations.

### Dispatch Planning Workbench

- Native Frappe Desk page with a Reactivity-oriented Vue implementation currently used for the planning UI.
- Route is available under the Frappe v16 Desk route `/desk/dispatch-planning`.
- Planning filters include:
  - Company
  - Delivery date range
  - Source Warehouse
  - Delivery Address
  - Territory
  - County
  - Constituency / Sub-county
  - Town
  - Priority
  - Customer
  - Allocation state
- Users can add configured or user-defined Delivery Order filters.
- Planning data is paginated and retrieved server-side.
- Delivery Order item details are loaded in batches or lazily.
- Manual load planning supports:
  - Delivery Order selection
  - Item-level selection
  - Partial quantities
  - Vehicle Category selection
  - Automatic capacity recalculation
  - Manual load evaluation
  - Server-side load creation
- Vehicle Category can be selected before Delivery Orders to guide planning and filtering.
- The Manual Load Plan displays:
  - Weight occupied and available
  - Volume occupied and available
  - Capacity percentages
  - Capacity progress bars
  - Recommended Vehicle Category where none is selected
- Capacity indicators use:
  - Blue for low utilization
  - Green for healthy utilization below capacity
  - Red for capacity exceeded
- Fleet Availability and Driver Availability are separate scrollable sections.
- Load Suggestions can be requested for the current filters and disappear when the planning context changes.
- Suggestions include multiple ranked alternatives, reasons, warnings, and capacity utilization.
- Suggestions can be reviewed and accepted explicitly.
- Suggestions never allocate demand or reserve vehicles by themselves.

### Load Suggestion Engine

- Deterministic, explainable load suggestions are available.
- Candidate Delivery Orders are partitioned and bounded to avoid combinatorial explosion.
- Hard constraints and soft scoring are separated.
- Capacity is evaluated across configured dimensions, primarily weight and volume.
- Vehicle Categories are evaluated before physical vehicle assignment.
- The smallest practical compatible Vehicle Category is preferred.
- Geographic scoring uses local geography and coordinates where available.
- Missing location data lowers confidence instead of being treated as valid geographic evidence.
- Suggestions return positive reasons, warnings, and exclusions where practical.
- Suggestion acceptance revalidates all demand and quantities server-side.
- Stale or concurrently allocated Delivery Orders are rejected safely.

### Fleet management

ERPNext Vehicle remains the physical vehicle master.

Implemented or supported fleet concepts:

- Vehicle Category
- Vehicle Manufacturer
- Vehicle Model
- Vehicle Owner using Company or Supplier semantics
- Vehicle → ERPNext Asset link
- Vehicle operational status display
- Vehicle dispatch eligibility
- Vehicle Documents
- Vehicle Document Types
- Vehicle Availability Blocks
- Vehicle Incidents
- Vehicle Operational Roles, now referred to as Vehicle Classifications where applicable
- Vehicle Allocation history and reallocation
- Vehicle operational enable/disable controls
- Vehicle costing structure assignment
- Vehicle capacity, UOM, and minimum payload validation

Vehicle availability is derived centrally from:

```text
Vehicle master state
Vehicle transport enablement
Vehicle Category
Linked Asset state
Vehicle Documents
Vehicle Availability Blocks
Vehicle Allocation overlap
Delivery Trip state
Vehicle Incidents
```

The canonical result includes:

- Operational state
- Dispatch eligibility
- Primary reason
- All blocking reasons
- Current allocation
- Current trip
- Next available time

### Driver availability

- Standard ERPNext/HRMS Driver is reused.
- Driver status must be Active.
- Driver licence validity is checked against the planning window where supplied.
- Driver allocation overlaps are checked.
- Conflicting Delivery Trips are checked.
- Driver availability is exposed to Dispatch Planning.
- Driver contact information is stored through standard Driver/Employee records rather than duplicate allocation text fields.

### Vehicle Allocation

- One Vehicle Allocation can contain multiple Delivery Loads.
- One Vehicle Allocation normally results in one Delivery Trip.
- Vehicle Allocation validates combined load weight and volume against the selected vehicle.
- Vehicle Category and capability compatibility are checked server-side.
- Vehicle and Driver availability are revalidated transactionally on submission.
- Reallocation preserves allocation history.
- Vehicle incidents can link to replacement allocations.
- Cancelling an allocation detaches active links from Delivery Loads to avoid circular cancellation dependencies.
- Vehicle and Driver link queries show available compatible resources.
- Vehicle, supplier, category, owner, and costing details can be populated from the selected Vehicle and related records.

### Vehicle Costing

Current costing is estimation-oriented. ERPNext remains the accounting authority.

Implemented concepts include:

- Cost Component
- Cost Structure
- Cost Structure Assignment
- Vehicle-linked Transport Costing Model where applicable
- Fixed and variable transport rates
- Distance-based estimates
- Origin and destination geography
- Company, Supplier, Vehicle Category, and Vehicle-specific applicability
- Effective dating
- Rate/component snapshots at allocation where applicable
- Fuel efficiency and fuel UOM inputs

Costing estimates do not replace ERPNext accounting entries, invoices, supplier bills, or expense claims.

### Delivery Trip and execution

- Standard ERPNext Delivery Trip is reused.
- Delivery Trips can contain multiple Delivery Notes through Delivery Stops.
- Delivery Stops carry the delivery/load lineage needed for execution.
- Delivery Trip creation can be initiated from Vehicle Allocation.
- Vehicle Allocation supports multiple loads while maintaining trip traceability.
- Delivery Note generation and Delivery Trip generation are guarded against duplicates.
- Reallocation history remains traceable from the trip and allocation records.
- Gate Pass functionality is available for outbound dispatch control.

### Proof of Delivery

- POD is custom and linked to Delivery Trip and Delivery Stop.
- POD records item-level accepted and rejected quantities.
- Rejected quantity can be calculated from planned/delivered and accepted quantities.
- Rejection Reason is a controlled Link value.
- Rejection remarks are recorded separately.
- POD validation requires rejection information when rejected quantity is greater than zero.
- POD supports customer acknowledgement/signature and supporting attachments where configured.
- Accepted-in-full PODs do not create Delivery Exceptions.
- Partial, rejected, and failed deliveries create or update Delivery Exceptions idempotently.

### Delivery Exceptions and returns

Delivery Exception represents what happened during delivery and what unresolved outcome must be managed.

Return Request represents the approved physical return case.

ERPNext stock and accounting documents remain authoritative.

Implemented or planned relationships:

```text
POD
    ↓
Delivery Exception
    ↓
Return Request
    ↓
ERPNext Return Delivery Note / Inspection / Credit Note
```

The return model supports:

- Item-level affected quantities
- Custody
- Responsible party
- Physical resolution
- Commercial resolution
- Return warehouse per item where required
- Return quantity controls
- Inspection and quality outcomes
- Replacement or re-delivery references
- Activity/history tracking
- Open exception completion blocking
- Protection against incorrectly re-releasing goods left with the customer

### Workspace and reporting

Fleet workspace areas include relevant:

- Vehicles
- Vehicle Categories
- Vehicle Models
- Vehicle Documents
- Vehicle Incidents
- Vehicle Allocations
- Vehicle Logs
- ERPNext Assets
- Fleet reports

Dispatch workspace areas include relevant:

- Delivery Orders
- Delivery Loads
- Vehicle Allocations
- Delivery Trips
- Proof of Delivery
- Delivery Exceptions
- Return Requests
- Dispatch reports
- Cost Components
- Cost Structures
- Cost Structure Assignments

Reports include or are planned for:

- Fleet Availability
- Vehicle Compliance
- Expiring Vehicle Documents
- Vehicle Allocation History
- Vehicles In Trip
- Open Vehicle Incidents
- Dispatch Readiness
- Delivery Trips Pending POD
- Delivery Exceptions
- Return Requests

## React Native Driver App

The driver application will be built with React Native, not Flutter.

The app will be a driver execution interface over server-side Vuna Transport and ERPNext APIs. It will not become a second source of truth.

### Driver App MVP

The first usable app should include:

- Secure driver login
- Driver-specific dashboard
- Assigned Delivery Trips
- Assigned Vehicle and Loads
- Delivery Stop list
- Delivery Note and item details
- Stop arrival/action state
- Item-level delivered quantities
- Accepted and rejected quantities
- Rejection reason and remarks
- Customer signature
- Delivery photos/attachments
- POD submission
- Delivery Exception creation when necessary
- Offline draft storage and retry synchronization

### Driver App phase 2

- Return Request initiation from a Delivery Exception
- Custody and physical-resolution capture
- Return status visibility
- Driver expense request entry
- Receipt photos
- Trip and Delivery Load linkage
- HRMS Expense Claim submission integration

### Driver App phase 3

- Location tracking consent
- Active-trip location pings
- Background location updates
- Offline location queue
- Server-side Driver Location Log
- Dispatcher visibility of last known location
- Tracking start/stop based on trip state

Location tracking should initially be limited to active assigned trips. It should not include live optimization, geofencing, telemetry, or route adherence until the basic execution workflow is proven.

### React Native technical direction

Recommended application structure:

```text
driver-app/
├── src/
│   ├── api/
│   ├── auth/
│   ├── navigation/
│   ├── features/
│   │   ├── trips/
│   │   ├── stops/
│   │   ├── proof-of-delivery/
│   │   ├── exceptions/
│   │   ├── expenses/
│   │   └── location/
│   ├── offline/
│   ├── storage/
│   └── shared/
├── android/
└── package.json
```

The app should use:

- A typed API client
- Secure token/session storage
- Local SQLite or equivalent offline storage
- A synchronization queue
- Background task support for location updates
- Camera and file upload support
- Signature capture
- Explicit server-confirmed transaction states

### Planned mobile API boundary

The backend should expose dedicated, permission-checked driver APIs rather than unrestricted generic document CRUD.

Potential services:

```text
services/mobile/
├── driver_auth_service.py
├── driver_trip_service.py
├── driver_delivery_service.py
├── driver_pod_service.py
├── driver_exception_service.py
├── driver_expense_service.py
└── driver_location_service.py
```

Potential API operations:

```text
get_driver_dashboard
get_assigned_delivery_trips
get_trip_details
get_stop_details
start_delivery_trip
arrive_at_stop
submit_delivery_result
create_delivery_exception
submit_expense_claim
record_driver_location
complete_delivery_trip
```

Every operation must:

- Require authentication
- Resolve the Driver from the logged-in user
- Check trip/stop assignment
- Check company access
- Check document permissions
- Recalculate quantities server-side
- Be safe to retry
- Reject cancelled, completed, or reassigned documents
- Record audit information

## Pending work

### Platform hardening

- Complete remaining Delivery Exception and Return Request workflow refinements.
- Add item-level return inspection and resolution controls.
- Finalize replacement-demand creation for only eligible replacement quantities.
- Expand dispatch and fleet reports after operational state stabilizes.
- Review all API endpoints for mobile-safe permission handling.
- Add end-to-end regression scenarios across Sales Order, Delivery Order, Load, Allocation, Trip, POD, Exception, and Return Request.

### Driver identity and access

- Confirm the Driver → Employee → User relationship used by the customer.
- Decide whether each driver receives a Frappe user account or whether an external identity layer is required.
- Define driver role permissions and mobile-specific permissions.
- Add device/session revocation support.

### React Native backend

- Inspect existing ERPNext/HRMS Expense Claim workflow and API requirements.
- Design and implement dedicated driver APIs.
- Add server-side request schemas and idempotency keys.
- Define mobile payload versions and backward compatibility rules.
- Add API rate limits and upload limits.
- Add mobile-specific audit logging.

### React Native app

- Create the React Native project shell at `vuna_transport/vuna_transport_mobile` using Expo SDK 57, TypeScript, and Expo Router.
- Implement authentication and session persistence.
- Implement trip and stop screens.
- Implement item-level POD.
- Implement signature/photo capture.
- Implement offline drafts and synchronization.
- Implement Delivery Exception capture.
- Implement Expense Claim request flow.
- Implement location tracking.
- Add Android device testing and release signing.

### Mobile test coverage

Test at minimum:

- Driver can only see assigned trips.
- Reassigned trips disappear or show a clear state.
- Cancelled trips cannot be executed.
- Duplicate POD submission is idempotent.
- Partial delivery quantities are validated.
- Rejection reasons are required when applicable.
- POD creates the correct Delivery Exception.
- Offline drafts synchronize once connectivity returns.
- Failed synchronization can be retried safely.
- Expense claims follow HRMS permissions and workflows.
- Location tracking stops after trip completion.
- Unauthorized users cannot access another driver’s locations or documents.

## Agreed exclusions for the initial Driver App

Do not implement initially:

- Full route optimization
- Automatic vehicle allocation
- Vehicle replacement decisions from the driver app
- Credit note approval
- Unrestricted stock operations
- Direct accounting entries
- GPS telemetry integrations
- Geofencing
- Live traffic
- Driver fatigue/work-hour management
- Fuel management
- Carrier settlement
- Automatic commercial resolution

## Implementation sequence

```text
1. Confirm Driver → Employee → User identity model
2. Inspect HRMS Expense Claim and current POD/Exception services
3. Define mobile API contracts
4. Implement authenticated driver dashboard APIs
5. Create React Native project shell
6. Implement trips, stops, and item details
7. Implement POD submission
8. Implement Delivery Exception capture
9. Add offline synchronization
10. Add Expense Claim requests
11. Add controlled location tracking
12. Add mobile security, device, and audit hardening
13. Perform end-to-end acceptance testing
```

## Architecture principle

```text
React Native Driver App
= driver-facing execution interface

Vuna Transport
= transport planning, dispatch validation, POD, exceptions, and returns

ERPNext / HRMS
= stock, accounting, employees, vehicles, drivers, assets, and Expense Claims
```
