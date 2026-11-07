# Vuna Transport Orchestration V2

## Architecture and Implementation Plan

**Status:** Proposed for implementation

**Implementation update:** Phase 5 uses pre-trip allocation-bound stock, destination-only transfer tasks and the existing Proof of Delivery model. Depot POD submission automatically posts End Transit for physically received accepted/quarantined quantities; rejected goods retained on the vehicle remain in transit. See `transport_orchestration_phase_5_progress.md` for implemented scope, configuration and verification. Broader workflows below remain design proposals; offline transfer POD and Phase 6 pickup execution are not yet complete.

**Business reference:** `vuna_transport_operational_scenarios_for_codex.md`

**Initial implementation scope:** Main Warehouse to Depot and Depot to Main Warehouse
**Designed extensions:** Customer delivery, customer return pickup, supplier pickup, and mixed trips

---

## 1. Executive Decision

Vuna Transport will evolve from a customer-delivery workflow into a general transport orchestration layer over ERPNext.

The target model is:

```text
ERPNext or Vuna Source Document
        ↓
Transport Demand
        ↓
Transport Load
        ↓
Vehicle Allocation
        ↓
Delivery Trip
        ↓
Ordered Delivery Stops
        ↓
Transport Trip Tasks
        ↓
Proof of Delivery
        ↓
Draft ERPNext Completion Document
        ↓
Authorized Review and Submission
```

The concepts have distinct responsibilities:

- ERPNext and Vuna source documents explain **why** goods need to move.
- Transport Demand normalizes **what must move, from where, to where, and by when**.
- Transport Load groups compatible demand for planning and warehouse preparation.
- Vehicle Allocation assigns the physical vehicle and driver to one or more loads.
- Delivery Trip defines the actual ordered journey.
- Delivery Stops represent physical places visited.
- Transport Trip Tasks represent the work performed at each stop.
- Proof documents record what physically happened.
- ERPNext Stock Entries, Delivery Notes, Sales Returns, and Purchase Receipts remain authoritative for inventory and accounting.

Vuna must not reproduce Stock Ledger, valuation, purchasing, sales-return, or accounting logic.

---

## 2. Confirmed Business Decisions

### 2.1 Supported movement types

The architecture must support:

1. Customer delivery.
2. Main Warehouse to Depot transfer.
3. Depot to Main Warehouse return.
4. Customer return pickup.
5. Supplier pickup.
6. Mixed trips containing several of the above.

### 2.2 Planning ownership

The Transport Department owns:

- demand selection;
- load bundling;
- route and stop ordering;
- vehicle allocation;
- capacity review;
- dispatch coordination.

The system assists planners but does not automatically make every planning decision.

### 2.3 Depot model

- Depots remain standard ERPNext Warehouses.
- No parallel Depot master will be introduced.
- A depot used in routing must have a resolvable ERPNext Address and, where available, coordinates.

### 2.4 Depot returns

Depot returns use:

```text
Return Request
→ Stock Entry with Add to Transit
→ Physical Transport
→ End Transit Stock Entry
→ Receiving Returns Warehouse
→ Return Inspection / Quality Inspection
→ Transfer to saleable or rejected warehouse
```

### 2.5 Returns warehouses

- Each depot may configure a dedicated Returns or Quarantine Warehouse.
- This is a real ERPNext Warehouse and therefore participates in Stock Ledger and valuation.
- The word “virtual” describes its operational role only; it is not virtual from ERPNext's inventory perspective.
- Accepted stock may subsequently move to the depot's saleable warehouse.
- Rejected stock remains in or moves to a rejected/damaged warehouse.

### 2.6 Supplier pickups

- The company pays the transport cost.
- Purchase Order is the provisional source document.
- The design must allow that trigger to be changed later without replacing the transport model.
- Ownership transfers to the company at supplier collection. Submit the Purchase
  Receipt into the allocated vehicle's Transit Warehouse at collection; arrival
  is a normal Material Transfer into a company receiving warehouse. No company
  source stock, fake supplier warehouse, Add-to-Transit or End Transit is invented.

### 2.7 Capacity and compatibility

- Weight is the authoritative V1 capacity metric.
- Existing Item fields, especially Delivery Group and Transport Compatibility Group, must be reused.
- Volume, pallets, cases, and crates remain visible where already available but are not V1 blocking metrics.
- Compatibility is checked across every item assigned to the vehicle, not only within each individual load.

### 2.8 Driver authority

Drivers record physical events and evidence. They do not control inventory, valuation, accounting, inspection, or document submission.

### 2.9 Proof and posting

- Proof records actual execution.
- The server may generate linked draft ERPNext documents from proof.
- Authorized warehouse, procurement, sales, or stock users review and submit those drafts.
- Driver proof alone must not silently submit stock or accounting transactions.

### 2.10 Return inspection

- Inspection should be available at each receiving depot.
- Receipt and quality classification are separate responsibilities.
- Receipt is represented by the applicable ERPNext return or stock document.
- Quality Inspection personnel classify the returned stock.
- A depot may transfer accepted goods to saleable inventory.

### 2.11 Costing

Transport costing and Landed Cost Voucher integration are deferred. The data model must preserve links needed for future allocation, but this phase must not alter item valuation.

---

## 3. Current Implementation Assessment

### 3.1 Structures to keep and extend

The following existing structures remain valuable:

- `Delivery Order` for current sales-delivery demand.
- `Delivery Load`, renamed to `Transport Load`, for bundling and capacity planning.
- `Vehicle Allocation` for vehicle and driver assignment.
- ERPNext `Delivery Trip` for trip lifecycle and vehicle execution.
- ERPNext `Delivery Stop` for ordered physical stops.
- `Proof of Delivery` for customer-delivery execution.
- `Return Request`, `Return Inspection`, and ERPNext Quality Inspection for returns.
- `Transport Zone` as a hierarchical geography.
- `Transport Route` as an optional reusable route template.
- Item Delivery Group and Transport Compatibility Group.
- Existing vehicle-category capacity and access validation.

### 3.2 Current constraints that must change

The current implementation is customer-delivery-centric:

- `Delivery Order.customer` and `delivery_address` are mandatory.
- Delivery Load rows only reference Delivery Order Item.
- Delivery Load validation assumes compatible customer Delivery Orders.
- Delivery Load has one required source warehouse.
- Current load validation requires the same Transport Zone.
- Delivery Trip generation depends on submitted Delivery Notes.
- Delivery Stops are enriched only with Delivery Order, Delivery Load, Delivery Note, and POD links.
- Trip completion assumes one submitted POD per stop.
- Mobile trip serialization assumes delivery-only stops.
- Return Request requires a Customer and primarily models customer Sales Returns.

These constraints cannot safely accommodate depot transfers, supplier pickups, depot returns, or stops containing both delivery and pickup work.

### 3.3 Design principle

Do not force Material Requests, Purchase Orders, Stock Entries, or depot Return Requests into fake Delivery Orders or Delivery Notes.

Instead, normalize their transport-relevant data into Transport Demand while leaving the source documents authoritative.

---

## 4. Proposed Document Model

### 4.1 New: Transport Demand

`Transport Demand` is a submitted operational snapshot generated from an approved source document. It is not a stock transaction.

One Transport Demand represents one movement leg from one source location to one destination location.

#### Core fields

```text
company
movement_type
direction
status

source_document_type
source_document
source_document_status_snapshot

party_type
party

source_location_type
source_location
source_warehouse
source_address
source_location_snapshot
source_latitude
source_longitude

destination_location_type
destination_location
destination_warehouse
destination_address
destination_location_snapshot
destination_latitude
destination_longitude

required_date
window_from
window_to
priority

transport_zone
preferred_route
delivery_group
transport_compatibility_group

total_stock_qty
total_weight
weight_uom
total_volume
volume_uom

allocated_weight
executed_weight
```

#### Movement types

```text
Customer Delivery
Warehouse Transfer
Depot Return
Customer Return
Supplier Pickup
Other Transfer
```

#### Direction

Direction describes the task at the source and destination:

```text
Deliver
Collect
Transfer
```

#### Statuses

```text
Draft
Open
Partially Allocated
Allocated
In Transit
Partially Completed
Completed
Exception
Closed
Cancelled
```

Statuses are derived from active load allocations and trip tasks where practical. They must not be freely editable.

#### Invariants

- Company must match both warehouses where warehouses are used.
- Source and destination cannot represent the same physical location for a transfer.
- Source document and source row lineage are immutable after demand allocation.
- A source document must not generate duplicate active demand for the same movement leg and item quantities.
- Completed quantity cannot exceed planned quantity without an approved exception.
- Cancellation of the source document must block unexecuted demand and trigger review for allocated/in-transit demand.

### 4.2 New: Transport Demand Item

Each row records normalized transport quantity and source lineage.

```text
source_item_doctype
source_item
item_code
item_name

transaction_qty
transaction_uom
conversion_factor
stock_qty
stock_uom

allocated_stock_qty
executed_stock_qty
remaining_stock_qty

weight_per_unit
weight
weight_uom
volume
volume_uom

delivery_group
transport_compatibility_group

batch_no
serial_no
serial_and_batch_bundle
```

Stock UOM quantity is canonical for allocation, capacity, execution reconciliation, and manifest arithmetic. Transaction UOM remains visible for users.

Batch and serial identity should generally be captured when the ERPNext stock document determines it. Demand may retain planned identity where already known, but must not invent batches.

### 4.3 Delivery Order relationship

`Delivery Order` remains the current sales-delivery planning document during the transition.

On submission or synchronization:

```text
Delivery Order
→ one Transport Demand
```

The Transport Demand references Delivery Order as its source document. Existing sales-delivery behaviour remains available while load planning moves to the normalized demand layer.

After the generic model is stable, Delivery Order can either:

1. remain a sales-specific transport cockpit; or
2. be deprecated in favour of direct Transport Demand generation from Sales Order.

That later cleanup is not required for V1.

### 4.4 Renamed and generalized: Transport Load

Rename the existing `Delivery Load` DocType to `Transport Load`. This must be a real DocType and code migration, not only a translated UI label.

The resulting lifecycle becomes:

```text
Transport Demand
→ Transport Load
→ Vehicle Allocation
→ Delivery Trip
```

`Transport Load` is no longer limited to customer delivery documents. It groups compatible Transport Demand quantities for warehouse preparation, collection planning, capacity planning, and vehicle assignment.

Existing document names such as `DL-...` remain valid historical identifiers. New documents should use a `TL-...` naming series after migration.

### 4.5 Renamed and generalized: Transport Load Item

Rename and generalize the current `Delivery Load Order` child DocType as `Transport Load Item`.

```text
transport_demand
transport_demand_item
movement_type
item_code
item_name
source_location_summary
destination_location_summary
allocated_stock_qty
stock_uom
transaction_qty
transaction_uom
weight
weight_uom
delivery_group
transport_compatibility_group
planned_load_stop
planned_unload_stop
```

The current `Delivery Load Order` rows should be migrated into Transport Load Item rows through their generated Transport Demand.

During migration:

- existing submitted loads remain executable;
- new or amended loads use generic rows;
- services must reject a mixed legacy/new row state;
- the old child table is hidden and removed only after migration and compatibility tests pass.

### 4.6 Transport Load responsibility after generalization

A Transport Load is a compatible cargo bundle prepared or collected as part of one vehicle run.

It may contain multiple demands, but a load should normally share a primary loading context. A mixed trip can contain multiple loads through the existing Vehicle Allocation load table.

Examples:

```text
Load A: Main Warehouse outbound customer deliveries
Load B: Main Warehouse to Nkubu Depot transfer
Load C: Supplier X collection
Load D: Customer return collection
```

Vehicle Allocation combines A, B, C, and D. The Delivery Trip orders all resulting stops and tasks.

### 4.7 New: Transport Trip Task

`Transport Trip Task` is a standalone operational document linked to one Delivery Trip and one ERPNext Delivery Stop row.

It is standalone rather than a child row because it needs independent status, proof, permissions, exception handling, and links.

#### Core fields

```text
company
delivery_trip
delivery_stop_row
stop_sequence

task_type
status

transport_demand
transport_load

source_document_type
source_document

action_location_type
action_location
action_address

planned_stock_qty
actual_stock_qty
weight
weight_uom

proof_doctype
proof_document

exception_status
exception_reason
```

#### Task types

```text
Customer Delivery
Transfer Receipt
Return Pickup
Supplier Pickup
Depot Return Pickup
Warehouse Drop-off
```

#### Statuses

```text
Planned
Ready
Arrived
In Progress
Completed
Completed with Variance
Failed
Skipped
Cancelled
```

### 4.8 ERPNext Delivery Stop extensions

ERPNext Delivery Stop remains the physical stop row. It already supports an Address without requiring a Customer or Delivery Note.

Add Vuna fields for:

```text
vuna_location_type
vuna_location_doctype
vuna_location
vuna_location_label
vuna_transport_zone
vuna_stop_status
vuna_planned_arrival
vuna_actual_arrival
vuna_actual_departure
vuna_task_count
vuna_completed_task_count
```

Customer delivery fields and Delivery Note remain populated for standard customer stops.

Stops are merged only when tasks resolve to the same physical Address. Matching merely by customer, supplier, warehouse name, or text label is insufficient.

### 4.9 Extended: Proof of Delivery

This is the generic proof for pickup, transfer receipt, warehouse drop-off, and other non-customer-delivery tasks.

Use the existing POD and POD Item DocTypes for both customer deliveries and warehouse-transfer handovers. Direction and the authoritative document/task link distinguish the operation. Do not introduce a second persisted proof model.

#### Core fields

```text
company
proof_type
delivery_trip
delivery_stop_row
transport_trip_task
transport_demand
transport_load

event_datetime
latitude
longitude

confirmed_by
confirmation_role
phone
signature
photo
remarks

outcome
mobile_request_id
items
```

#### Proof types

```text
Proof of Pickup
Transfer Dispatch Confirmation
Transfer Receipt Confirmation
Warehouse Drop-off Confirmation
Failed Attempt
```

#### Outcomes

```text
Completed
Partially Completed
Rejected
Failed
```

### 4.10 Extended: Proof of Delivery Item

```text
transport_demand_item
source_item_doctype
source_item
item_code

planned_qty
actual_qty
variance_qty
uom

stock_qty
stock_uom
conversion_factor

serial_and_batch_bundle
batch_no
serial_no

variance_reason
remarks
```

Proof submission is idempotent through `mobile_request_id` and server-side duplicate checks.

### 4.11 New: Trip Manifest Item

Add a manifest child table to Delivery Trip. It is a submitted planning snapshot and execution summary, not a stock ledger.

```text
transport_trip_task
transport_demand
transport_demand_item
transport_load
item_code
stock_uom

planned_stock_qty
planned_weight

load_stop_sequence
unload_stop_sequence

loaded_stock_qty
delivered_stock_qty
collected_stock_qty
unloaded_stock_qty
current_onboard_stock_qty

batch_no
serial_no
serial_and_batch_bundle
```

Actual batch and serial details remain authoritative in ERPNext stock documents and submitted proof. Manifest identity is a linked execution snapshot.

---

## 5. Source Document Mappings

| Movement | Demand source | Pre-departure ERPNext document | Driver proof | Destination draft generated | Final authority |
|---|---|---|---|---|---|
| Customer delivery | Delivery Order / Sales Order | Submitted Delivery Note | Proof of Delivery | None | Delivery Note + POD |
| Main to depot transfer | Submitted Material Request (Material Transfer) | Submitted Stock Entry with Add to Transit | Transfer Receipt Confirmation | End Transit Stock Entry | Stock Entry |
| Depot to main return | Submitted Return Request | Submitted Stock Entry with Add to Transit | Proof of Pickup + Transfer Receipt | End Transit Stock Entry | Stock Entry |
| Customer return pickup | Submitted Return Request | None at pickup | Proof of Pickup | Draft Sales Return at receiving checkpoint | Sales Return |
| Supplier pickup | Submitted Purchase Order | Purchase Receipt into vehicle Transit Warehouse at collection | Proof of Pickup | Normal Material Transfer from vehicle stock on arrival | Purchase Receipt + Stock Entry |

### 5.1 Demand synchronization rules

Demand synchronization must be idempotent and adapter-based:

```text
delivery_order_adapter
material_request_adapter
return_request_adapter
purchase_order_adapter
```

Each adapter must implement the equivalent of:

```text
is_eligible(source_document)
get_movement_legs(source_document)
get_transport_items(source_document)
get_locations(source_document)
get_planning_requirements(source_document)
sync_transport_demand(source_document)
handle_source_cancellation(source_document)
```

This prevents transport services from containing document-specific condition chains throughout the codebase.

### 5.2 Material Request eligibility

V1 eligibility:

- submitted Material Request;
- Material Request Type = Material Transfer;
- source and target warehouses populated;
- outstanding quantity greater than zero;
- company transport required.

One Material Request may generate multiple Transport Demands if item rows contain different source or destination warehouses.

### 5.3 Purchase Order eligibility

Initial rule:

- submitted Purchase Order;
- company pickup explicitly enabled;
- supplier pickup Address selected;
- target company warehouse selected;
- outstanding receipt quantity greater than zero.

Do not generate transport demand for every Purchase Order automatically. Add an explicit transport/pickup indicator or action.

### 5.4 Return Request generalization

Return Request must evolve from customer-only to support:

```text
return_party_type: Customer | Warehouse
return_party
source_location_type
source_address
source_warehouse
destination_returns_warehouse
collection_required
transport_demand
pickup_proof
```

Rules:

- Customer is mandatory only for customer returns.
- Source Warehouse is mandatory for depot returns.
- Destination Returns Warehouse is mandatory before transport execution.
- Customer returns preserve Delivery Note or Sales Invoice lineage.
- Depot returns preserve originating warehouse and item/batch lineage.
- Depot Return generates a Stock Entry flow, not a Sales Return.
- Return Inspection begins only after stock is received into the configured Returns Warehouse.

---

## 6. Load Bundling Rules

### 6.1 Hard constraints

Demand cannot share a vehicle allocation when any of these conflict:

- company;
- vehicle ownership/availability rules;
- dedicated Transport Compatibility Group;
- required refrigeration or body type;
- special equipment requirements;
- maximum vehicle access restrictions;
- impossible required-date or time-window combinations;
- weight capacity at any route segment.

### 6.2 Planner warnings

Warn, but allow an authorized planner to review, when:

- transport zones differ but appear route-compatible;
- preferred routes differ;
- delivery groups differ without a hard compatibility conflict;
- coordinates are missing;
- route distance or duration is estimated with low confidence;
- returned/rejected cargo shares a vehicle with saleable cargo;
- a task introduces a large pickup before outbound cargo has been unloaded.

Compatibility override must require a reason and be auditable. Dedicated Transport Compatibility Group remains non-overridable unless a later setting explicitly permits it.

### 6.3 Compatibility-group behaviour

V1 reuses existing behaviour conservatively:

- the same populated Transport Compatibility Group is compatible;
- a group marked `dedicated_transport` cannot share the vehicle with another group;
- different populated groups are treated as incompatible unless an explicit compatibility rule is introduced later;
- missing compatibility data produces a warning for manual planner review.

Future enhancement: a compatibility matrix between groups. Do not add it until real combinations require it.

### 6.4 Dynamic peak-capacity calculation

Capacity must be evaluated after every stop:

```text
onboard_after_stop
= onboard_before_stop
- delivered_or_unloaded_weight
+ collected_or_loaded_weight
```

The allocation is valid only if:

```text
0 <= onboard_weight_at_every_segment <= effective_vehicle_weight_capacity
```

Initial outbound weight is not sufficient for mixed-trip validation.

### 6.5 Quantity reservation

- Draft Transport Loads reserve Transport Demand Item quantity.
- Submitted Transport Loads allocate it.
- Cancelled loads release it.
- A demand item cannot be over-reserved across concurrent loads.
- Rows must be locked during allocation updates.
- Availability is derived in Stock UOM.

---

## 7. Vehicle Allocation Cargo Preview

Vehicle Allocation must show a read-only preview before submission.

### 7.1 Summary

```text
Total loads
Total tasks
Initial outbound weight
Planned pickup weight
Peak onboard weight
Vehicle effective capacity
Capacity utilization percentage
Total deliveries
Total pickups
Compatibility groups
Warnings and blocking conflicts
```

### 7.2 Item detail

The preview must support grouping by:

- Item;
- Transport Load;
- movement type;
- source location;
- destination location;
- stop sequence;
- Transport Compatibility Group.

Each row should show:

```text
item
planned quantity and UOM
stock quantity and Stock UOM
weight
load at
unload at
source document
transport demand
transport load
```

### 7.3 Authority

The preview is derived from active Transport Load Item rows. Vehicle Allocation must not duplicate editable cargo quantities.

On submission, save an allocation capacity snapshot for audit while continuing to derive live links from the source documents.

---

## 8. Route and Zone Architecture

### 8.1 Transport Zone

Keep the existing hierarchical Transport Zone tree.

Use zones for:

- planning filters;
- route-corridor guidance;
- reporting;
- service ownership;
- future costing.

Do not require every demand in a mixed load or trip to have the exact same leaf zone. Parent/child zone relationships and route coverage must be considered.

### 8.2 Transport Route

Transport Route remains an optional reusable template, not a mandatory trip definition.

Enhance it with:

```text
route_type: Template | Recurring
origin_address
destination_address
service_zones
default_vehicle_category
default_departure_time
active_days (future)
```

Existing route stops continue to contain Address, sequence, coordinates, distance, and duration.

### 8.3 Dynamic daily planning

For V1:

1. Resolve source and destination Addresses from selected demand.
2. Group tasks by physical Address into candidate stops.
3. Seed order from a selected Transport Route when applicable.
4. Let the planner manually reorder stops.
5. Recalculate estimated distance, time, and segment capacity.
6. Copy the final plan into Delivery Trip.

Changing a Transport Route template after trip creation must not rewrite the trip.

### 8.4 Route optimization

Automatic optimization is deferred. Existing ERPNext route optimization may remain available only when all stops have valid coordinates and its behaviour is compatible with task constraints.

Task precedence must never be violated. For example, cargo cannot be unloaded before its load/pickup stop.

---

## 9. Trip, Stop, Task, and Manifest Generation

### 9.1 Trip creation

Trip creation from Vehicle Allocation should:

1. lock and reload the allocation and loads;
2. validate all active demand and source documents;
3. collect movement legs from generic Transport Load Items;
4. build candidate physical stops;
5. merge tasks sharing the same Address;
6. assign or confirm stop order;
7. create ERPNext Delivery Stop rows;
8. create Transport Trip Tasks;
9. snapshot Trip Manifest Items;
10. calculate peak capacity;
11. populate vehicle, driver, allocation, and load links.

### 9.2 Multiple tasks at one stop

Example:

```text
Customer B
├── Customer Delivery: DN-0002
└── Return Pickup: RR-0007
```

The trip contains one Delivery Stop and two Transport Trip Tasks. Each task has its own proof and outcome.

### 9.3 Stop completion

A stop is complete only when every non-cancelled task is:

```text
Completed
Completed with Variance
Failed
or Skipped with an authorized reason
```

Arrival and departure are stop-level events. Quantities and evidence are task-level events.

### 9.4 Trip completion

A trip may complete when:

- every required task has a terminal outcome;
- every required proof is submitted;
- all onboard cargo has a recorded destination or exception;
- unresolved cargo exceptions are explicitly acknowledged;
- the trip has no impossible negative manifest balance.

Trip completion does not require every generated ERPNext draft to be submitted. It must clearly report drafts awaiting back-office review.

---

## 10. Driver Security and Draft Document Generation

### 10.1 Driver capabilities

The driver may:

- read assigned submitted trips, stops, tasks, and planned items;
- record arrival and departure;
- record actual delivered or collected quantity;
- select permitted batch/serial information where required;
- record shortages and variance reasons;
- capture provider/recipient name, signature, photo, GPS, and timestamp;
- submit proof through task-specific mobile APIs.

### 10.2 Driver restrictions

The driver may not:

- create arbitrary Transport Demands or tasks;
- change source or destination warehouses;
- change company, party, item, valuation, rate, or accounts;
- add unrelated stock items to an ERPNext transaction;
- submit or cancel Stock Entries, Purchase Receipts, Sales Returns, or Delivery Notes;
- conduct or approve Quality Inspection;
- classify stock as saleable or rejected;
- change vehicle allocation or route planning.

### 10.3 Server-side proof validation

Every mobile submission must validate:

- authenticated user is linked to the currently assigned Driver;
- trip is submitted, started, and not completed/cancelled;
- task belongs to the trip and stop;
- task is assigned and executable;
- item rows match the task's demand items;
- quantity is non-negative and respects UOM precision;
- unexpected or excess quantity becomes an exception, not silent demand expansion;
- batch/serial data is valid for the item and source document;
- duplicate `mobile_request_id` returns the existing result;
- attachments are size/type constrained;
- GPS and timestamps are server-normalized and audited.

### 10.4 Draft generation policy

Draft ERPNext documents are created by a trusted server service from:

- the source document;
- Transport Demand lineage;
- configured warehouses;
- submitted execution proof;
- receiving confirmation;
- actual quantities and serial/batch data.

The driver's User must not require broad create/submit permission on the generated ERPNext DocType. The service performs only the narrow mapped operation and records:

```text
generated_from_transport_task
generated_from_proof
generated_by_service
requires_review
variance_present
```

### 10.5 Variances

```text
Planned 100, actual 100
→ generate normal draft

Planned 100, actual 90
→ generate draft for 90 with shortage exception

Planned 100, actual 110
→ record 100 plus excess exception
→ do not expand source document automatically

Unexpected item
→ capture evidence as exception
→ do not add it automatically to stock documents
```

---

## 11. Custody and Receiving Checkpoints

Pickup and destination receipt are separate events.

```text
Source confirms pickup
        ↓
Driver / Vehicle Custody
        ↓
Destination confirms receipt
        ↓
Draft ERPNext completion document reviewed
        ↓
Authorized submission
```

Transport Trip Task and proof statuses must make custody visible:

```text
Awaiting Pickup
In Driver Custody
Arrived at Destination
Receipt Confirmed
ERPNext Draft Created
ERPNext Posted
Exception
```

These may be derived task milestones rather than a single freely editable status field.

### 11.1 Main Warehouse to Depot

```text
Approved Material Request
→ Transport Demand
→ Transport Load / Vehicle Allocation
→ Warehouse creates and submits Add-to-Transit Stock Entry
→ Delivery Trip with receiving-destination stops only (no loading POD)
→ Driver transports goods
→ Depot Transfer Receipt task records actual receipt
→ Server creates and submits End Transit for confirmed receipt quantities
→ Demand completes
```

### 11.2 Depot to Main Warehouse return

```text
Submitted Return Request
→ Transport Demand
→ Transport Load / Vehicle Allocation / Delivery Trip
→ Driver Proof of Pickup records actual collected quantities
→ Automatic Add-to-Transit Stock Entry for collected quantities only
→ In Driver Custody
→ Main/receiving depot confirms receipt
→ Automatic End Transit posting into the approved receiving Returns Warehouse
→ Return Inspection / Quality Inspection
→ Stock Entry to accepted or rejected destination
```

Phase 6's depot-held rejection pickup/receiving slice is implemented for testing. Return Inspection integration for receiving Stock Entries and mandatory-QI operational preparation remain outstanding; see `transport_orchestration_phase_6_progress.md`. Vehicle-held recovery and customer/supplier pickups are separate follow-up work.

### 11.3 Customer return pickup

```text
Submitted Return Request
→ Transport Demand
→ Return Pickup task
→ Driver Proof of Pickup
→ In Driver Custody
→ Returns Warehouse confirms receipt
→ Draft Sales Return generated/updated
→ Authorized review and submission
→ Return Inspection
```

### 11.4 Supplier pickup

```text
Submitted Purchase Order with a company-arranged Delivery Method with Buying enabled
→ Transport Demand
→ Supplier Pickup task
→ Driver Proof of Pickup
→ Authorized Purchase Receipt submitted into vehicle Transit Warehouse
→ Company-owned inventory in vehicle custody
→ Company Warehouse confirms arrival
→ Normal Material Transfer from vehicle warehouse to receiving warehouse
→ Authorized review and submission (not End Transit)
```

The company owns collected goods at supplier pickup, not only after arrival.
Receipt posting and required QI remain authorized purchasing/warehouse actions.
A draft Purchase Receipt alone does not update inventory. The warehouse-side
creation/transfer actions are implemented; connecting them to supplier trip tasks
and mobile proof is still Phase 7 follow-up work.

---

## 12. Returns Warehouse and Inspection Configuration

### 12.1 Warehouse fields

Add transport configuration to Warehouse or a company-aware child configuration:

```text
vuna_is_transport_depot
vuna_returns_warehouse
vuna_saleable_warehouse
vuna_rejected_returns_warehouse
vuna_default_receiving_address
```

Prefer direct Warehouse fields for V1 because depots are already Warehouses and configuration is location-specific.

Validation:

- configured warehouses belong to the same company;
- a Returns Warehouse cannot be a group warehouse;
- destination Return Request uses the configured Returns Warehouse by default;
- missing configuration blocks return receipt planning with an actionable message;
- users may override defaults only with suitable permissions.

### 12.2 Inspection flow

- Returned stock first enters the selected Returns Warehouse.
- Return Inspection references the submitted Sales Return or completed depot-return Stock Entry.
- Quality Inspection is performed by authorized quality users.
- Accepted and rejected quantities retain batch/serial lineage.
- Stock Entry transfers inspected quantities to configured target warehouses.
- Transport proof does not determine quality outcome.

---

## 13. Mobile Application Changes

### 13.1 Trip detail

The mobile trip view should show ordered physical stops. Each stop expands into task cards.

Example:

```text
Stop 3 — Nkubu Depot

[Deliver Transfer]
Main Warehouse → Nkubu Depot
12 items · 1,450 kg

[Collect Depot Return]
RR-00042
3 items · 180 kg
```

### 13.2 Task actions

Task-specific actions replace delivery-only assumptions:

```text
Confirm Arrival
Start Task
Capture Delivery
Capture Pickup
Confirm Transfer Receipt
Record Failed Attempt
Confirm Departure
```

### 13.3 Offline behaviour

- Manifest, stops, tasks, and allowed item rows are included in the offline trip package.
- Proof submissions use deterministic request IDs.
- Offline excess/unexpected quantities remain exceptions after synchronization.
- A completed server task is not duplicated when the same offline proof retries.
- Attachments retry independently without duplicating proof.

### 13.4 Visibility

The driver sees:

- planned quantity;
- actual quantity inputs;
- item and UOM;
- pickup/delivery location;
- batch/serial controls when required;
- evidence requirements;
- task status and synchronization status.

The driver does not see valuation rates, accounts, internal approval controls, or unrestricted warehouse selectors.

---

## 14. Server-Side Invariants

### 14.1 Demand

```text
planned_stock_qty
= allocated_stock_qty
+ unallocated_stock_qty

executed_stock_qty <= planned_stock_qty
unless an approved exception exists
```

### 14.2 Load

```text
sum(active draft reservations + submitted allocations)
<= demand item remaining quantity
```

### 14.3 Manifest

For each item lineage and route segment:

```text
onboard_after
= onboard_before
+ loaded_or_collected
- delivered_or_unloaded
```

The balance may never be negative.

### 14.4 Vehicle capacity

```text
peak_onboard_weight
<= effective vehicle capacity
```

Capacity override follows existing Transport Settings and must record the authorizing user and reason.

### 14.5 Proof

```text
actual quantity
= completed quantity
+ variance quantity
```

Proof items must map to assigned task items. Excess and unexpected quantities cannot mutate the source demand silently.

### 14.6 ERPNext completion documents

- Generated rows retain source row lineage.
- Generated warehouse choices come from demand/configuration, not driver input.
- Cancellation of a generated document reopens/reconciles demand execution state.
- Submission updates posted quantity; draft creation alone does not mark stock as posted.
- Repeated generation returns the active existing draft instead of creating duplicates.

---

## 15. UI and Workbench Changes

### 15.1 Dispatch Planning

Expand the planning workbench to show normalized Transport Demand with filters for:

```text
movement type
company
source
destination
zone
required date
priority
delivery group
compatibility group
allocation status
```

Planners can select compatible quantities and create Transport Loads.

### 15.2 Transport Load

Show:

- movement types;
- demand/source-document links;
- source and destination summaries;
- item quantities and weight;
- compatibility warnings;
- stop contribution;
- allocation and trip progress.

### 15.3 Vehicle Allocation

Add the cargo preview described in Section 7 before vehicle/driver assignment is submitted.

### 15.4 Delivery Trip

Show:

- stop sequence;
- task cards under each stop;
- manifest summary;
- starting, collected, delivered, and remaining cargo;
- proofs and generated drafts through Connections;
- exceptions requiring review.

### 15.5 Return Request

Act as a return process cockpit showing:

- returning party and source;
- destination Returns Warehouse;
- transport demand;
- load/trip/task;
- pickup proof;
- custody state;
- receipt stock document;
- Return Inspection and Quality Inspection;
- accepted/rejected stock destination;
- commercial resolution where applicable.

---

## 16. Implementation Phases

### Phase 0 — Branch and regression baseline

1. Complete/merge or rebase the Returns V2 branch.
2. Create `feature/transport-orchestration-v2`.
3. Record current passing unit, integration, lint, and Semgrep baseline.
4. Add architecture decision references to the new branch.
5. Do not mix unrelated mobile branding or demo-data work into this feature.

### Phase 1 — Transport Demand foundation

Deliverables:

- Transport Demand and Transport Demand Item DocTypes.
- Shared location snapshot and UOM helpers.
- Adapter interface and Delivery Order adapter.
- Material Request Material Transfer adapter.
- Idempotent synchronization and cancellation policy.
- Demand list/report and Connections.
- Tests for duplicate generation, partial quantities, source cancellation, UOM, and company isolation.

No mobile changes are required in this phase.

### Phase 2 — Transport Load rename and generic planning

Deliverables:

- rename `Delivery Load` to `Transport Load` across DocTypes, links, services, APIs, reports, dashboards, and UI;
- rename `Delivery Load Order` to `Transport Load Item`;
- rename `Vehicle Allocation.delivery_loads` to `transport_loads` while retaining the `Vehicle Allocation Load` child DocType;
- rename `Vehicle Allocation Load.delivery_load` to `transport_load`;
- rename custom lineage fields such as `vuna_delivery_load` to `vuna_transport_load` on Delivery Note, Delivery Stop, POD, allocation, and trip integrations;
- Generic demand reservation/allocation service.
- Migration of legacy load rows to Transport Demand lineage.
- Planning workbench queries based on Transport Demand.
- Compatibility validation across generic items.
- Removal of strict same-leaf-zone assumption.
- Tests for concurrency, partial allocation, release on cancellation, and mixed demand types.

### Phase 3 — Vehicle Allocation cargo preview

Deliverables:

- Read-only `Vehicle Allocation Item` child table: Item, Quantity, UOM, Weight, Weight UOM, and Transport Load.
- Automatically populate the table from selected loads and rebuild it server-side on validation.
- Keep each load and UOM separate; combine only matching item rows within the same load and units.
- Retain the saved item list on submitted allocations; backfill valid historical allocations without changing source quantities.
- Existing capacity and compatibility controls remain authoritative.
- Route, pickup, onboard balance, and peak-segment calculations belong to Delivery Trip/task implementation, not this allocation table.

This deliberately simpler scope supersedes the broader Vehicle Allocation preview described in Section 7, following the confirmed UI decision on 2026-11-03.

### Phase 4 — Generic trip stops, tasks, and manifest

Deliverables:

- Transport Trip Task DocType.
- Delivery Stop generic location fields.
- Trip Manifest Item child table.
- trip generation from allocation demands;
- task grouping by Address;
- manual stop reordering and recalculation;
- updated lineage and cancellation guards;
- existing customer Delivery Note/POD flow preserved through tasks.

### Phase 5 — Warehouse transfer execution (essential V1)

Deliverables:

- Pre-trip allocation stock preparation and destination Transfer Receipt tasks.
- Proof of Delivery and item DocTypes.
- mobile transfer-task UI and offline support;
- Material Request to Add-to-Transit Stock Entry linking;
- automatic End Transit posting on depot POD submission;
- normal receiving versus quarantine stock routing, preserving vehicle-held rejection;
- demand/task/manifest reconciliation.

Acceptance scenarios:

- one Material Request, one depot;
- multiple Material Requests bundled to one depot;
- multiple depot stops on one trip;
- partial receipt;
- failed receipt;
- cancelled initial or end-transit Stock Entry;
- batch and serialized items.

### Phase 6 — Depot return execution (essential V1)

Implementation update (2026-11-05): manual depot returns, receiving Stock
Entry-based Return Inspection, vehicle-held End Transit recovery, warehouse
draft-stock/QI preparation, and offline pickup/transfer POD queuing are implemented.
See `transport_orchestration_phase_6_progress.md` for verified scenarios, deliberate
V1 boundaries, and outstanding physical-device/concurrent-dispatch acceptance.

Deliverables:

- generalized Return Request party/location fields;
- depot return demand adapter;
- depot Returns Warehouse configuration;
- depot pickup and receiving tasks;
- Add-to-Transit and End Transit Stock Entry linkage;
- Return Inspection from received stock;
- mobile Proof of Pickup and destination receipt;
- tests for partial pickup, partial receipt, and inspection lineage.

### Phase 7 — Customer return pickup and supplier pickup

Implementation update (2026-11-05): approved customer return assignment and pickup
already exist. Supplier PO opt-in, policy-controlled demand generation, requested
UOM preservation and source/endpoint validation are implemented as slice 7A.
The warehouse-side PO-to-vehicle Purchase Receipt and normal arrival transfer
actions are implemented. Supplier loads/tasks and mobile proof integration remain
pending; Phase 7 is not complete. See `transport_orchestration_phase_7_progress.md`.

Deliverables:

- customer return tasks linked to existing Sales Return workflow;
- supplier pickup demand from opted-in Purchase Orders;
- draft Purchase Receipt generation;
- mixed customer-delivery and pickup stops;
- custody and variance review UI;
- role and permission tests.

### Phase 8 — Route and zone expansion

Deliverables:

- richer route templates;
- hierarchical zone compatibility;
- route-seeded stop ordering;
- coordinate completeness warnings;
- segment distance and duration calculation;
- route reporting.

Automatic optimization remains a separate future project.

### Phase 9 — Cleanup and deprecation

After production confidence:

- remove temporary Delivery Load and Delivery Load Order compatibility aliases;
- remove delivery-only assumptions from execution summaries;
- decide whether Delivery Order remains a sales cockpit;
- consolidate proof dashboards and reports;
- update consultant, user, and mobile documentation.

---

## 17. Migration and Backward Compatibility

### 17.1 Existing Delivery Orders and Loads

- Backfill Transport Demand for active submitted Delivery Orders.
- Rename Delivery Load to Transport Load while preserving document names, docstatus, amendments, comments, assignments, and links.
- Rename Delivery Load Order to Transport Load Item and backfill Transport Demand lineage.
- Preserve historical `DL-...` names; use `TL-...` for newly created Transport Loads.
- Rename custom fields and child links from `delivery_load`/`vuna_delivery_load` to `transport_load`/`vuna_transport_load` with temporary compatibility handling at API boundaries.
- Preserve original names and links for audit.
- Completed/cancelled historical loads remain read-only and need not be fully migrated unless required for reporting.

### 17.2 Existing Delivery Trips

- Do not rewrite completed trips.
- Backfill tasks only for active draft/submitted trips where safe.
- Existing POD-linked Delivery Stops remain valid.
- New mixed-trip behaviour applies to newly created trips after migration.

### 17.3 Existing Return Requests

- Existing customer returns retain Customer lineage.
- New party/location fields are backfilled from Customer and delivery address.
- Depot return fields apply only to the new return source/type.
- Returns V2 inspection and stock-disposition behaviour remains intact.

### 17.4 Cancellation order

Cancellation guards must respect dependency order:

```text
ERPNext completion document
→ proof
→ trip task
→ delivery trip
→ vehicle allocation
→ transport load
→ transport demand
→ source document
```

Where cancellation is not allowed, show the exact blocking document and connection.

---

## 18. Testing Strategy

### 18.1 Unit tests

- demand adapters;
- location resolution;
- UOM conversion and precision;
- allocation reservations;
- compatibility rules;
- peak capacity calculation;
- stop grouping;
- task state transitions;
- proof variance validation;
- draft-document idempotency;
- cancellation reconciliation.

### 18.2 Integration tests

At minimum:

1. Material Request to completed end-transit Stock Entry.
2. Two Material Requests bundled into one load.
3. Multiple loads assigned to one Vehicle Allocation.
4. Mixed outbound delivery and depot transfer.
5. Depot return through Returns Warehouse and inspection.
6. Customer stop containing POD and return pickup.
7. Supplier pickup to draft Purchase Receipt.
8. Pickup causing peak capacity after departure.
9. Partial and excess quantities.
10. Batch, serial, and Serial and Batch Bundle preservation.
11. Offline proof retry.
12. Driver attempting unauthorized warehouse/document changes.

### 18.3 Permission tests

Verify that a mobile driver cannot:

- read trips assigned to another driver;
- alter task planning fields;
- create unrelated stock rows;
- submit ERPNext stock documents;
- inspect returns;
- bypass proof quantity and lineage checks.

### 18.4 Regression tests

Existing customer-delivery tests must continue to cover:

- Sales Order to Delivery Order;
- Transport Load planning, including migrated customer-delivery demand;
- Vehicle Allocation;
- Delivery Note generation;
- Delivery Trip generation;
- mobile POD;
- Return Request from partial/rejected POD;
- trip completion and cancellation.

---

## 19. Deferred Work

The following are explicitly outside the first implementation:

- transport cost allocation;
- Landed Cost Voucher integration;
- automatic route optimization;
- volume/pallet/crate capacity as blocking dimensions;
- supplier returns;
- third-party carrier portals;
- automated commercial charging;
- advanced compatibility matrices;
- geofencing enforcement;
- automatic Purchase Order pickup for every supplier order.

The model must remain extensible to these features without implementing them prematurely.

---

## 20. Remaining Configuration Decisions

These decisions do not block the document model but must be resolved before their related phase is released:

1. Which workflow state, in addition to docstatus, makes a Material Request transport-ready?
2. Which role confirms depot receipt and reviews the End Transit Stock Entry draft?
3. Whether Transfer Dispatch Confirmation requires a signature or only warehouse-user confirmation.
4. Which evidence fields are mandatory per task/proof type.
5. Whether compatibility warnings may be overridden and by which role.
6. How long draft ERPNext documents may remain pending after trip completion.
7. Whether depot Return Inspection may be skipped for configured item groups.
8. Whether customer-return Sales Return drafts are created at pickup or only after destination receipt. The recommended default is after destination receipt.

All of these should be settings or workflow policy where company practices may differ.

---

## 21. Recommended First Development Slice

The first development slice should be deliberately narrow:

```text
Submitted Material Request (Material Transfer)
→ Transport Demand
→ Transport Load allocation
→ Vehicle Allocation cargo preview
```

It should not yet modify mobile trip execution.

This slice proves the foundational model by answering:

- Can one ERPNext Material Request safely produce normalized transport demand?
- Can demand quantities be partially and concurrently allocated?
- Can the renamed Transport Load and existing Vehicle Allocation support non-sales cargo?
- Can the planner see accurate item and weight totals?
- Can existing customer-delivery planning continue unchanged?

Only after those invariants are stable should trip tasks, mobile proof, and automated ERPNext draft generation be added.

---

## 22. Definition of Done for Orchestration V2

The architecture is complete when:

- customer deliveries, warehouse transfers, depot returns, customer return pickups, and supplier pickups use one normalized planning model;
- a Vehicle Allocation previews all cargo across its loads;
- mixed trips contain ordered physical stops with one or more tasks per stop;
- onboard cargo can increase and decrease without losing lineage;
- peak weight capacity is validated across the route;
- drivers submit constrained proof without broad ERPNext permissions;
- proof creates reviewable drafts rather than silently posting stock/accounting;
- depot returns move through ERPNext in-transit stock and configured Returns Warehouses;
- Quality Inspection remains separate from transport receipt;
- existing customer delivery/POD behaviour remains supported;
- cancellation, retry, partial execution, and batch/serial lineage are covered by automated tests.

## 23. Unplanned Collection Slice (2026-11-04)

The normal process is an approved/submitted Return Request followed by planned,
assigned pickup work. Unplanned collections are an opt-in exception under
**Transport Settings → Features → Allow Unplanned Return Pickups**, disabled by
default. The switch governs new reports and draft submission in Desk and mobile,
and the reference-item lookup endpoints. It does not create or bypass an approval
workflow; existing demand-source workflow requirements continue to apply.
Switching it off preserves submitted evidence, dispatch review, stock receipt,
cancellation and idempotent retries of already-submitted reports. Stops with
historical reports retain a read-only Collection History entry on mobile.

Unplanned collections use the existing **Proof of Delivery**, with handover type
**Unplanned Collection** and direction **Pickup**. They do not create a historical
Transport Demand or pickup task. Every collection must reference an existing stop
on that trip; no new stop is fabricated. An assigned driver can record them while
the trip is started and not completed, from **Trip → Stop → Unplanned collections**.
Desk users have **Delivery Trip → expand stop row → Record Unplanned Collection**.
Collection history is scoped to that stop. Collection and delivery remain separate
proofs, so either can be recorded first while the trip is active.

The report records the collection stop and derives the source customer/warehouse
from that stop and its transport tasks (or explicit Warehouse Address link).
Drivers do not select Customer/Depot again. Before choosing items they must load
a submitted non-return **Delivery Note** for that stop's customer, or a submitted
**Stock Entry** with incoming rows at that depot. Wrong-company and unrelated
documents are rejected. Mixed stops may choose the document type, not an arbitrary
customer or depot. Loading/Add-to-Transit entries are not depot receipts.

Only source-document item rows/batches appear in the picker. Original transaction
UOM and conversion factor are retained, with source row identity preserved even
when the same item appears on several lines. Bundle identities take precedence
over legacy fields. Each batch gets its own option; serial subsets must belong to
the referenced row. Collected stock quantities across non-cancelled reports cannot
exceed the original source row/batch quantity; repeated serial reporting is blocked.
These are collection-evidence limits, not a replacement for ERPNext's stock/return
balance checks when receiving goods. The report also records the release contact,
reason, actual collection time, reporting user/time and trip driver/vehicle.
Mobile retries with the same request ID and payload return the same submitted
proof; changing a previously used payload is rejected. Collection is not a QC
acceptance: its outcome is **Collected**, review **Pending Review**.

Dispatch can link an existing same-company Return Request using **Review
Collection**. Customer cases must still be raised against the legitimate
Delivery Note or stock-updating Sales Invoice through the existing returns flow;
the report cannot manufacture that sales authority. For unknown depot cases,
**Create → Return Request** creates a draft from the collected quantities after
dispatch confirms the original stock warehouse derived from the reference and
chooses the receiving holding warehouse.
It does not assert an original outbound delivery. Review records an audit comment.

For depot goods that have already arrived, submit the reviewed Return Request,
then use **Collection POD → Create → Stock Entry (Material Transfer)**. This is a
standard **draft** transfer from the approved depot stock warehouse to the
receiving Returns/Quarantine Warehouse, with no fabricated intermediate transit
posting. Warehouse staff confirm arrival by submitting it under their normal ERPNext
permissions. Batch/serial bundles, availability, QI, valuation and GL checks remain
ERPNext's responsibility. Drivers cannot invoke dispatch review or authorize stock
or credit. Customer receipts continue through standard Sales Return processing.

Every collected row must be received together in this slice; partial warehouse
receipt of a single report is deferred. A collection can cover part of an existing
Return Request. Ambiguous repeated item/batch source rows must be resolved before
review. Existing active pickup demands must be resolved before linking an unplanned
collection; reviewed quantities are excluded from subsequent pickup planning.

One active stock receipt is permitted per report. Server validation prevents
quantity/UOM/warehouse/identity substitution. Submitted receipt stock contributes
to Return Request received quantities; cancelling stock reopens that balance.
Cancelling/deleting its Return Request reopens collection review without deleting
the physical collection evidence. This path does not complete the still-pending
Stock Entry-based Return Inspection work.

Verification: `test_unplanned_pickup_service`, `test_depot_return_demand_service`,
and rollback-only `unplanned_pickup_smoke.run(batched=True/False)`. The latter seeds
disposable stock, exercises real receipts and cancellation, and always rolls back.
Offline collection queuing, supplier collections, physical-device UX validation,
unknown batch/serial registration and historical/completed-trip collection reporting
are deferred. No native dependency or release APK is added by this slice.

## 24. Direct Approved Return Assignment (2026-11-05)

An approved/submitted customer or depot Return Request can now be assigned directly
to an existing submitted Delivery Trip and unfinished stop, without Transport
Demand, Transport Load or another Vehicle Allocation. This is an authorized pickup
task, not an unplanned collection, and remains available with unplanned pickups off.
The trip must have a driver, match the company, and not be completed/cancelled.
Existing configured Return Request workflow-state requirements still apply.

Use **Return Request → Actions → Assign Pickup to Delivery Trip**, select the trip
and stop, and refresh that trip on the main mobile app. A **Return pickup: RR…**
task appears at the selected stop. Only dispatch roles with write access to both
documents may assign/remove work; the driver only executes the assigned proof.
Customer stops must identify the same customer. Depot stops must identify the
source warehouse or its explicitly configured returns holding warehouse. Direct
depot assignment currently requires one source warehouse. Approved batch/serial
identities must be present; unresolved batch distributions must be resolved before
assignment. No source expectation quantities are rewritten.

The existing Transport Trip Task/POD models are reused with explicit Return
Request and Return Request Item references; no hidden demand or load is created.
Repeated assignment to the same active trip/stop returns the same task. Another
active assignment, an existing pickup demand, an already-collected unplanned
report or exhausted collection quantity blocks duplicate work. Partial pickup
leaves only its actual uncollected balance eligible for later assignment.

Driver proof retains UOM, batch/serial, quantity and assigned-trip checks. Customer
pickup is custody evidence only: no inventory, Sales Return or credit is posted.
Its existing approved RR sales-return/credit-note workflow handles receipt and
commercial resolution. Depot pickup posts actual collection into the trip vehicle's
transit warehouse, using standard ERPNext stock checks and without broad driver
stock permissions. Warehouse staff use standard **End Transit** from that stock
voucher, receiving into the RR Return Warehouse; no receiving stop is fabricated.
Its posting/cancellation updates RR received quantities. Mandatory QI is not bypassed.

On ordinary customer trips, a stop requires both its normal delivery POD and all
direct pickup tasks to finish. Pickup-only stops need no fake Delivery Note.
Demand-based trip plan validation ignores direct assignments, preserving existing
planning snapshots. Cancelled tasks are excluded from execution/mobile display.
Direct work does not run load suggestions, route optimization or load-level capacity
planning; dispatch remains responsible for operationally suitable assignments.

**Task → Actions → Remove Pickup Assignment** releases unexecuted work. If proof or
stock exists, cancel downstream receiving, pickup stock and pickup proof first.
The owning pickup POD/Task stock backlinks are navigation references; safe stock
cancellation preserves ERPNext's own link exclusions without ignoring active
receiving PODs or stock. RR cancellation requires releasing direct assignments.

Verification includes direct-assignment permission/approval/quantity tests, stop
completion regressions, and rollback-only `direct_return_pickup_smoke.run()` and
`run_customer()`. These exercise mobile proofs, depot transit/receipt/cancellation,
customer evidence without inventory/credit, and no new planning documents. No new
approval workflow, native dependency, APK or supplier pickup support is introduced.
