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

Decision (2026-11-07): consolidate the customer-sales cockpit into Transport
Demand, but staging already uses Delivery Orders. Migration preparation is pulled
forward; actual cutover and removal remain gated on staging verification. This
does not mark Phase 7, Phase 8 or Phase 9 complete.

After migration and operational confidence:

- remove temporary Delivery Load and Delivery Load Order compatibility aliases;
- remove delivery-only assumptions from execution summaries;
- move Delivery Order sales-planning functionality into Transport Demand before
  retiring the original cockpit;
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

### 17.5 Delivery Order migration preparation (2026-11-07)

This is an explicit administrator operation, not an automatic patch. Deploying
this preparation release does **not** retire Delivery Order or switch the live
sales workflow. Existing forms, APIs, source policies and original links remain.
No mobile change is required for this preparation step.

`vuna_transport.services.demand.delivery_order_migration` provides:

- `prepare()` — read-only preflight by default, with conflicts and a name map;
- `prepare(dry_run=False, ...)` — transactional copies and additive shadow links;
- `verify()` — read-only source/snapshot/row/link checks after preparation.

Preparation includes draft, submitted, completed and cancelled orders, not just
active orders. It keeps historical quantities (including zero-outstanding rows),
requested UOMs, factors and Stock UOM quantities. Missing/inconsistent UOM data is
a blocker, not permission to infer a conversion from today's Item master.

An existing single Transport Demand wrapper is reused with its current name,
child IDs, document state and live quantities unchanged. New copies retain the
original Delivery Order and item names in the *different* target DocTypes, along
with source metadata and docstatus. Sales Order/row references, routing/handling
and location snapshots, capability/access requirements and dimensions now have
real Transport Demand/Item columns as well as the recovery snapshot. Original
business state, custom columns and custom child tables remain in that snapshot.
Existing root/row quantities or lineage must match before reuse; conflicting
nonblank customer-planning values also require review.

Normal `insert`, `save`, `submit` and source-generation hooks are deliberately not
replayed during historical import. Stock Ledger, GL, the original Delivery Orders
and their items are fingerprinted before/after apply; any change rolls back the
operation. The service never commits internally. `bench execute` commits only on
successful completion. A failed preflight applies nothing; a failed verification
rolls back the preparation savepoint.

Shadow demand links are populated on Transport Load Item, Delivery Note, Delivery
Stop, POD/POD Item and Return Request/Return Request Item, across document states.
Existing nonblank conflicting links, orphan legacy references or mismatched load
parent/item lineage block the whole requested operation. The existing links stay
authoritative. Original comments, communications, attachment records, assignments,
versions and shares stay attached to Delivery Order; their metadata is also
snapshotted. Binary attachments still require the normal files backup.

Staging sequence (replace `STAGING_SITE`; these are operator steps, not automatic
deployment instructions):

1. Take and retain a database **and files** backup before deploying this additive
   release. Deploy and run the normal site migration to add the hidden fields.
2. Run the preflight while Delivery Orders are still in use:

   ```sh
   bench --site STAGING_SITE execute vuna_transport.services.demand.delivery_order_migration.prepare
   ```

   Review every blocker and the mapping. Resolve data issues explicitly; the
   script never edits source quantities, overwrites mismatches or skips failures
   during a full-site apply. Do not use a subset to claim a complete migration.
3. Rehearse on a restored staging copy. For an approved preparation window, put
   the site in maintenance mode and pause its workers, scheduler and integrations
   through the hosting platform. Maintenance mode alone does not stop background
   writers. Take a fresh full backup, then rerun preflight against frozen data.
4. Apply and retain its report, especially the map and ledger fingerprints:

   ```sh
   bench --site STAGING_SITE execute vuna_transport.services.demand.delivery_order_migration.prepare --kwargs '{"dry_run": False, "backup_confirmed": True, "workers_paused": True}'
   bench --site STAGING_SITE execute vuna_transport.services.demand.delivery_order_migration.verify
   ```

   The capitalized Python booleans are intentional for `bench execute`. Optional
   `source_names=["DO-..."]` limits an explicitly reviewed pilot; omitted means all
   Delivery Orders. Retry against unchanged data is idempotent. Verification is
   read-only; it does not re-assert historical ledger checksums after normal
   operations have resumed.
5. Retain Delivery Order and all old fields. If legacy operations resume and
   change prepared source data, the next run blocks on the changed checksum;
   automatic refresh of already-used historical mappings is intentionally not
   implemented. Therefore copying is a controlled preparation/rehearsal step,
   **not continuous synchronization or the final cutover**. Before resuming after
   an unsuccessful rehearsal, roll back the preparation transaction or restore
   its full backup; there is no broad “delete migrated demands” command.

Every report deliberately returns `ready_for_retirement=False`. Before removal we
still must port Sales Order generation/grouping and outstanding-quantity sync,
item/quantity load dialogs, dispatch planning/suggestions, load validation,
allocation previews, note/stop/proof/return execution, cancellation, settings,
connections, dashboards and native audit links. Then switch source lineage to
Sales Order, review site customizations/dynamic references and run a final
maintenance-window reconciliation. Do not delete the original DocType, its child
DocType or old fields based on a successful preparation report.

Local verification: `test_delivery_order_migration` plus rollback-only
`vuna_transport.tests.delivery_order_migration_smoke.run`. The latter rehearses
valid local history, verifies a retry, compares ledgers/source records, and rolls
back all target/shadow-link changes. Its report separates invalid legacy records;
the real whole-site apply never silently excludes them.

### 17.6 Customer cockpit field transfer — first cutover slice

Transport Demand now has Demand, Delivery & Planning and Connections tabs.
Customer-only routing, service windows, access/capability requirements, geographic
snapshots and transport totals appear only for Customer Delivery. Item rows retain
Sales Order Item identity, ordered quantity, source warehouse, delivery date,
handling, weight/volume and pallet/case/crate measures. No new customer fields are
mandatory for Buying or Internal demands; float quantities use system precision.

Equivalent columns are not duplicated:

| Delivery Order / Item | Transport Demand / Item |
| --- | --- |
| `delivery_address` | `destination_address` |
| `required_delivery_date` | `required_date` |
| item `transport_qty` | `transaction_qty` |
| item `uom` | `transaction_uom` |

The existing stock-UOM allocation columns remain canonical. Legacy aggregate
transaction-quantity counters are retained in the recovery snapshot rather than
added as competing totals for rows with potentially different requested UOMs.
Original amendment identity also remains in the recovery snapshot pending the
actual link cutover; no Amended From Link is populated with a Delivery Order name
and falsely presented as an existing Transport Demand.

New/refreshed DO-backed demands populate these real columns from the verified
source. Existing wrappers gain them through explicit migration preparation;
deployment does not silently rewrite historical submitted records. The migration
fills new columns without replacing live target quantities, lifecycle state or
row identity. Draft/Cancel state remains standard; dispatched/delivered/closed
customer snapshots are not reopened by allocation reconciliation. List indicators
support these statuses and the existing generic allocation statuses.

Rebuilding a source snapshot now retains persisted child IDs and stock allocation
caches instead of deleting/recreating linked rows on every save. Client-supplied
names/counters are not used as an authority. Migration smoke coverage includes an
ordinary customer draft save, in addition to import, verification and retry.

This first slice is additive. The subsequent source/planning preview is described
below. Do not remove Delivery Order yet. Unresolved records remain untouched for
a separate review. Staging has not been changed, and local data rehearsals are
rolled back.

### 17.7 Direct sales demand generation and draft bundling — second cutover slice

Implemented behind a temporary site opt-in, `customer_demand_cutover=1`:

- Sales Order **Create → Transport Demand** and automatic source policy generate
  customer Transport Demand directly, without creating a Delivery Order or item.
- Existing grouping/handling/measurement helpers now build a plain snapshot;
  native generation does not instantiate the legacy DocType. The group key keeps
  delivery date, address, origin, zone, route, handling, method and delivery group.
- Stable native source keys include the selected Sales Order row identities, so
  newly added source rows can form another demand batch without rewriting prior
  demand/item IDs. Retries return the same complete active native demand set.
- Active native and legacy source rows both count as covered, in both generation
  directions. Ambiguous legacy source-row references require review rather than
  creating a potential duplicate. Cancelled native keys require planner review.
- Source workflow, company transport method, permissions and auto-submit policy
  remain enforced. Native demand uses **Auto Submit Transport Demand**, not the
  older Delivery Order auto-submit option.
- Canonical native quantities refresh after submitted Sales Order updates and
  standard direct Delivery Note submission/cancellation. Source-row identities
  remain stable. Reducing below still-reserved quantities or changing an active
  demand's UOM/group lineage requires releasing that planning first. Posted
  customer quantities are distinguished from pending reservations.
- Native demand **Create → Transport Load** presents the standard editable grid:
  choose items, change requested-UOM quantities and uncheck rows. The list action
  can bundle multiple compatible native customer demands into one draft load.
- Draft load rows use `transport_demand` / `transport_demand_item`, not invented
  legacy links. Server validation rechecks company, source lineage, live Sales
  Order balance, requested/stock UOM conversion, duplicate rows, method, zone,
  handling profile, compatibility, dimensions and vehicle/site requirements.
  Loading warehouse may be deferred or changed to another active warehouse in
  the company; it becomes mandatory at submission. Native reservation writes use
  Sales Order → demand lock order, matching source generation/refresh.
- Native customer draft loads are kept separate from warehouse-transfer and
  legacy customer load rows. Route defaults, distance estimates, physical
  capability/access limits and capacity calculations reuse shared services.
- Sales Order Connections includes Transport Demand while retaining legacy
  Delivery Orders for historical navigation.

**Execution boundary at the end of this slice (superseded by 17.8):** these native customer loads are planning drafts in this
slice. They cannot submit through either save/insert validation or the submit
action until native customer warehouse/delivery execution is ported. The execution
overview explicitly identifies that boundary; they are never mislabelled as
depot transfers or offered an Add-to-Transit posting. Legacy loads and warehouse
transfers retain their existing execution. The dispatch workbench/suggestions,
Pick Lists/Delivery Notes, allocation/trip readiness, stops, POD/returns, reports
and final historical link cutover are still to be switched before retirement.

**Staging safety:** the opt-in is off by default. Without it, the Sales Order UI,
automatic generation and saved generation actions retain the legacy source path.
The explicit Transport Demand source adapter can still be rehearsed in rollback
tests. This is development preview code, not permission to enable the switch on
an operational staging site or delete its Delivery Order DocTypes.

For a local/restored-copy preview only:

```sh
bench --site meru-dairy.localhost set-config customer_demand_cutover 1
bench --site meru-dairy.localhost clear-cache
```

Refresh a submitted Sales Order with eligible stock rows **not already covered
by Delivery Orders**. Check its source policy is enabled; use a fresh test order
if necessary. Generate/review/submit native demand, open the item/quantity dialog
and save a draft load. No physical stock or accounting entry is expected. Load
submission intentionally reports the execution boundary; it is not a warehouse
transfer. Reset the opt-in to `0` to restore legacy UI/automatic generation; keep
any explicit preview records for deliberate review rather than auto-deleting
them. Neither local nor staging activation is performed by deployment or tests.

Verification: `test_sales_transport_demand`, source-policy/dashboard regressions,
and rollback-only `sales_transport_demand_smoke.run`. The rehearsal exercises
Cartons with a real conversion factor, optional origin, demand retry/save/submit,
partial load reservation, source quantity refresh/reduction protection and the
execution guard. It compares source/planning/stock/GL fingerprints after rollback
and verifies no Delivery Order was created. No existing historical records are
rewritten, deleted or force-cancelled by this slice.

---

### 17.8 Native customer execution — third cutover slice (2026-11-08)

Native Sales Order → Transport Demand → Transport Load now reuses the existing
customer execution services rather than the depot-transfer task engine. Native
loads can submit with a valid loading warehouse, then create standard Pick Lists
and Delivery Notes, allocate a vehicle and create a Delivery Trip. Requested UOM,
conversion factor and exact Sales Order child-row references are preserved.

`customer_reference_service` selects explicit native or legacy Link columns.
Prepared historical records carrying both links remain on the legacy path; no
Transport Demand ID is ever put in a Delivery Order Link. Delivery Note item →
Transport Load Item → Transport Demand Item is the authoritative execution chain.
Single-value parent links are navigation aids, not the source of quantity truth.

Server-side checks cover company/customer/destination, exact Sales Order row,
requested UOM/factor, same-company stock warehouses and cumulative non-cancelled
draft/submitted Delivery Note quantities against each load-row allocation. Multiple
loads may consolidate notes only under one active allocation and the existing
commercial/warehouse compatibility key. Allocation/trip readiness requires exact
submitted item quantities and no draft notes, not merely a note header.

Standard customer PODs populate canonical demand-item links from delivered rows.
Partial/rejected delivery creates the existing Return Request, preserving requested
UOM and batch/bundle identity through existing return services. Demand completion
compares individual stock-UOM item balances, never sums unlike quantities. Each
load's outcome is reconciled independently even when one consolidated proof covers
multiple loads. Later allocation refreshes retain proof-derived delivery outcomes.
Methods without a required trip complete from submitted note quantities instead.

Cancelling a native Delivery Note before an active trip reopens readiness and
returns affected loads to Allocated/Approved, with an audit reason. Cancelling an
accepted POD reopens its load/trip/demand progress. Existing ERPNext downstream
stock/return cancellation safeguards still apply; no blanket link bypass is added.

Verification: `test_native_customer_execution` plus the existing planning,
returns, allocation, lineage and mobile trip execution regressions. Rollback-only
`sales_transport_demand_smoke.run(verify_execution=True)` exercises real batched
stock, requested Cartons (factor 12), Pick List, Delivery Notes, cancellation,
trip, partial/rejected POD, automatic RR and POD cancellation. Set
`consolidated=True` to exercise one note/proof spanning two loads with different
outcomes. Stock/accounting posting and validation are real; driver notifications
are suppressed. Source/planning/Stock Ledger/GL fingerprints must match after
rollback. No existing historical records are rewritten.

`without_trip=True` separately verifies Delivery Methods that finish from the
submitted notes, including reopening after note cancellation. Local verification
passed 431 regression tests, all three native execution rehearsals, the depot
POD/End Transit rehearsal, pre-commit checks and the application asset build.

**Pending at the end of this slice (screens are completed in 17.9):** dispatch workbench/suggestions
and remaining reports/navigation consumers, historical reference cutover and
review of unresolved migration records, followed by a restored-staging rehearsal
and explicit activation. The customer-demand opt-in remains off by default and
has not been enabled on the local or staging site. Delivery Order/Item DocTypes
and original data are retained. No deletes, remote migrations, commits or pushes
are performed by this slice.

---

### 17.9 Customer planning screens and navigation — fourth cutover slice (2026-11-08)

The Dispatch Planning customer tab, detail picker, native FilterGroup, manual
load evaluation/creation, cost previews, ranked suggestions and suggestion
acceptance now follow the server-selected customer source. With the opt-in on,
they use Sales Order-backed customer Transport Demands; prepared Delivery Order
wrappers, warehouse transfers, returns and supplier collections cannot leak into
that customer pool. The existing depot/supplier planning paths are unchanged.

The source dialect maps date/address fields for read-only presentation, while
all native writes use `transport_demand` / `transport_demand_item` Links. Site
mode, company, submitted state, movement/source type and current source eligibility
are server-controlled. FilterGroup cannot override those boundaries. Live source
quantities/UOM/grouping and draft/submitted reservations use the same checks as
native load creation; accepting a stale/over-allocated suggestion is rejected.

Source-free demands remain visible when a loading warehouse is selected. That
warehouse is preserved by manual plans and suggestions, validated for company,
warehouse permissions and operational status, and used for stock warnings.
Draft loads may still defer it; submission may not. Native Transport Load forms
offer **Planning → Get Customer Items** with editable quantities in requested UOM,
and hide empty legacy Link columns. Existing legacy loads retain their picker.

New **Open Transport Demand** and **Unallocated Transport Demands** reports cover
outstanding customer delivery demand by item/UOM with live balances and normal
document/company permissions. Unlike-UOM quantities are not given a misleading
grand total. Legacy reports remain available for history. Pending Delivery Notes
show the canonical demand link and exclude return notes; Dispatch Readiness uses
the load's active allocation rather than a single-load allocation backlink.
Dispatch sidebars/workspaces and the main workspace place Transport Demand before
the retained **Delivery Orders (Legacy)** link, and point active report navigation
to the new reports. Delivery Method and Transport Demand connections now expose
the canonical demand/load relationship. Consolidated-note progress uses quantity
coverage rather than assuming one Delivery Note per source; POD progress follows
the delivered item lineage even when another load owns the note header.

Verification includes `test_customer_planning_screens`, the existing planner and
execution regression suites, and `node vuna_transport/tests/test_customer_planning_ui.cjs`.
`sales_transport_demand_smoke.run(verify_planning=True, verify_execution=True)`
rehearses real source-free planning, a manual draft reservation, requested-UOM
details/reports, suggestion acceptance/stale retry, and batched delivery/POD/return
execution. Its fixture and stock/accounting changes are always rolled back.

Local verification passed 449 regression tests and three client-side selection
tests, both ordinary and consolidated planning/execution rehearsals, pre-commit
checks and the asset build. Reports/navigation and canonical source-field
visibility were synchronized to `meru-dairy.localhost`; the customer cutover
setting and historical transaction records were not changed.

**Activation boundary:** this slice prepares screens; it does not toggle the site
setting or migrate/delete historical records. Next is the restored-staging
migration rehearsal and unresolved-record review, then explicit activation and
later Delivery Order retirement. The legacy paths are deliberately retained for
that interval, not a second native execution engine.

---

### 17.10 Historical cutover review — local rehearsal (2026-11-08)

`delivery_order_cutover_audit.audit()` is a read-only Administrator/System Manager
CLI inventory, not a patch, promotion command or switch activation. `summary()`
returns the review cases and a deterministic review checksum. The audit compares
exact Sales Order child rows, historical/source UOM arithmetic, current source
group keys, duplicate native keys/source rows and existing downstream load links.
Protected-table fingerprints must remain unchanged; no hooks, repairs, cancellations,
deletions or stock/accounting postings are performed.

```sh
bench --site meru-dairy.localhost execute vuna_transport.services.demand.delivery_order_cutover_audit.summary
```

Local findings: 92 Delivery Orders, 83 preparation candidates and nine orders
blocked by missing UOM/conversion data (11 item rows). The exact Sales Order rows
provide UOM/conversion evidence for all 11, but proposals require approval and do
not mutate originals. Six blocked orders already have load references; these are
not safe to discard merely because their old quantities are incomplete.

Additional cutover review identifies two multi-group source orders
(`DO-16-09-2026-00001`, `DO-27-08-2026-00001`) and six inactive Sales Order sources.
Categories overlap, giving 16 review cases in total. One inactive-source case
still has a submitted/allocated load; no new planning or historical regeneration
may be inferred from its current source balance. Preserve history and review its
existing work explicitly. Passing preparation is not permission to promote all
83 copies as native demand with a one-to-one source mapping.

The rollback-only preparation rehearsal passed for the 83 candidates, reusing
21 existing demands and creating 62 temporary copies. Retry is idempotent, normal
draft save preserves child identities/counters, and all fixture, source, ledger
and downstream-link changes are rolled back. This is local-clone evidence, not
a rehearsal against the user's staging site and not a committed migration.

Next decisions: approve source-backed UOM recovery in migration targets, define
row-preserving splits for the two mixed source groups, and resolve inactive-source
history/attached loads. Originals and audit snapshots remain intact. Only then
implement/rehearse reviewed historical promotion, take/confirm backups and pause
writers before an explicit apply. Keep the switch off and retain Delivery Order
until that verification is complete. Tests: `test_delivery_order_cutover_audit`
and the migration/native-demand suites; 72 targeted tests pass.

---

### 17.11 Source-backed UOM recovery on every site (2026-11-08)

The operator-approved recovery is part of
`vuna_transport.services.demand.delivery_order_migration.prepare`, not a local-only
fixture or a list of hard-coded record IDs. Dry-run, apply and verify all use the
same logic on local, restored staging, staging and production sites.

Only missing UOM/conversion data is recovered. The exact `sales_order_item` must
belong to the original Sales Order and match company, customer and Item. The
source transaction/stock UOMs, positive finite conversion factor and original
quantity arithmetic must agree. Known conflicting units, factors or quantities,
missing/ambiguous source rows, invalid numbers and quantities exceeding the
original source row remain blockers. Today's Item conversion table is not used.
Cancelled source documents may supply historical unit evidence; they are never
reactivated by recovery.

The original Delivery Order and item rows are not edited. The migration copy
retains the requested transaction quantity, receives the recovered UOM/factor and
derived stock quantity, and converts its reservation/allocation caches consistently.
The recovery snapshot retains the unmodified original source/checksum plus each
row's before/resolved values and exact Sales Order evidence. Retry/verification
blocks if that evidence changes or an existing target contradicts it. Apply locks
the relevant Sales Orders/items before legacy sources and targets; no stock or GL
business hooks are replayed.

Normal saves of recovered prepared drafts reconstruct a verified in-memory
source projection, preserving target child IDs, recovered quantities and persisted
provenance even if the client supplies different recovery metadata. Ordinary
stock-item/disabled-item validation remains in force; historical non-stock rows
are not made executable merely because their units were recovered.

Operator procedure for staging/production (replace `YOUR_SITE`; no remote site
is changed by development tests):

1. Deploy the code and its required schema through the normal release process.
2. Run the default read-only preparation and inspect `blockers`,
   `uom_recovered_rows` and per-row `uom_recoveries`:

   ```sh
   bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_migration.prepare
   bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_cutover_audit.summary
   ```

3. Rehearse on a disposable restored copy first. For a live apply, take and verify
   a full database/files backup, enable maintenance mode, pause queue workers and
   integrations, and keep other writers stopped. Only then confirm these safeguards:

   ```sh
   bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_migration.prepare --kwargs '{"dry_run": False, "backup_confirmed": True, "workers_paused": True}'
   bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_migration.verify
   ```

   The kwargs use Python booleans as required by `bench execute`. Maintenance mode
   is checked independently; flags are operator attestations, not commands that
   pause workers or create a backup. A failed apply rolls back; the module does
   not commit internally. Bench commits only a successful command.

4. Review the apply report and remaining cutover cases before resuming operations.
   Preparation/recovery does not enable `customer_demand_cutover`, split mixed
   planning groups, resolve inactive-source loads, retire DocTypes or delete data.

Local full-site rollback rehearsal now covers all 92 Delivery Orders, with 11 UOM
recoveries across nine orders (71 temporary new copies, 21 reused targets). Four
recovered stock-item drafts save correctly; two historical non-stock cases still
hit the normal stock-item guard. Repeated apply/verify is idempotent, all original
Delivery Orders/Sales Orders and stock/accounting fingerprints remain unchanged,
and the rehearsal is rolled back. Remaining grouping/inactive-source review is
separate from these now-resolved preparation UOM blockers.

Verification after this change: 472 transport/migration regression tests and
pre-commit checks pass, along with the full 92-record rollback rehearsal.

---

### 17.12 Final Delivery Order retirement (2026-11-08)

This supersedes the staged cutover limitations in sections 17.5–17.11. Customer
planning now runs on Sales Order → Transport Demand → Transport Load, including
requested UOM, planning, picking, delivery, proof, returns and reporting. Once the
legacy DocType is absent, native customer demand is mandatory even if an old
`customer_demand_cutover` site setting is zero. Historical names may retain their
`DO-…` prefix; they are Transport Demand records, not Delivery Orders.

`delivery_order_retirement` promotes exact item identities, splits mixed source
groups, preserves original source/audit snapshots, recovers missing UOMs from
exact Sales Order rows, and reconciles downstream item/header links. Inactive or
non-stock history is marked Historical Only and excluded from new planning.
Wholly obsolete unexecuted loads/allocations are closed only after checking for
active stock, trip and proof dependencies. No stock or accounting hooks are
replayed. Unknown dynamic links or site-specific automations block retirement
rather than being silently reassigned. Existing shares follow their target(s).

Local apply on `meru-dairy.localhost`: **92 sources → 94 native Transport Demands**,
11 recovered demand UOM rows, nine historical-only targets, two obsolete loads closed.
Twelve historical POD rows also have their missing units recovered from exact
Delivery Note evidence. Their recorded quantities remain unchanged: a proof that
recorded pieces stays in pieces, rather than being misinterpreted as cartons.
The manifest retains complete before/resolved proof evidence. The recovery is
included in future retirement applies; already-retired sites can run the guarded,
idempotent `recover_historical_proof_units` companion under the same safeguards.
Delivery Order and Delivery Order Item metadata and their obsolete forms/reports
are removed. Original business tables remain as a forensic archive, not usable
DocTypes; source snapshots and the retirement manifest are also retained. There
is deliberately no destructive SQL table drop. Recovery requires the verified
database/files backup and matching pre-retirement app revision; there is no
automatic reverse migration.

For staging/production, rehearse on a restored copy and **do not run `bench migrate`
before retirement**. A pre-migration hook and first pre-model-sync guard stop an
upgrade while legacy metadata remains. Keep maintenance mode on and workers,
integrations and other writers paused throughout schema preparation and apply:

```sh
bench --site YOUR_SITE backup --with-files
bench --site YOUR_SITE set-maintenance-mode on
# Verify the backup and pause workers/integrations before confirming these flags.
bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_retirement.install_schema --kwargs '{"backup_confirmed": True, "workers_paused": True}'
bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_retirement.inspect
# Review blockers, historical-only targets, closures and the projection checksum.
bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_retirement.retire --kwargs '{"dry_run": False, "backup_confirmed": True, "workers_paused": True, "expected_checksum": "REVIEWED_CHECKSUM"}'
bench --site YOUR_SITE execute vuna_transport.services.demand.delivery_order_retirement.verify
bench --site YOUR_SITE migrate
bench build --app vuna_transport
bench --site YOUR_SITE set-maintenance-mode off
# Resume workers/integrations only after verification.
```

Schema preparation installs the customer-planning fields and canonical links
before data conversion, without recreating either retired DocType. Schema
preflight reports any remaining missing columns explicitly.
Never bypass blockers with force deletion. A successful apply is transactional;
normal `bench execute` commits on success. Rollback-only retirement and native
execution smoke tests are available for restored development sites. Ordinary
post-retirement verification checks source snapshots and item lineage without
expecting ledgers to remain frozen after legitimate new business transactions.

Verification: 458 remaining transport/migration regressions, planning UI tests,
pre-commit checks and the asset build pass. The removed legacy DocType tests no
longer belong to the live suite; migration tests use archived source fixtures.
A real-data rollback rehearsal deletes metadata, exercises native planning and
batched stock/picking/POD/returns, then verifies protected source/ledger recovery.
No staging or production site was modified by this local implementation.

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
