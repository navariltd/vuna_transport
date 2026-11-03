# Vuna Transport – Operational Scenarios and Business Rules for Transport Architecture

## Purpose

This document captures the current business scenarios, operational rules, and design decisions that should guide the next stage of Vuna Transport architecture.

The goal is to give Codex concrete real-world transport examples before proposing:

- document models,
- lifecycle/status diagrams,
- ERPNext mappings,
- load bundling rules,
- route/zone logic,
- driver workflows,
- phased implementation plans,
- compatibility/migration decisions.

The scenarios below reflect the current operating model and should be treated as the primary business reference for the next design pass.

---

# 1. Main Warehouse → Depot Transfer

## Scenario

Main warehouse replenishes a depot.

Example:

```text
Main Warehouse
    ↓
Nkubu Depot
```

## Requested by

Depot Supervisor

## Source

Main Warehouse

## Destination

Depot Warehouse

Depots are represented as standard ERPNext Warehouses.

## ERPNext document that starts it

```text
Material Request
Type: Material Transfer
```

The Material Request is approved first.

After approval, the movement begins using an in-transit stock movement.

Expected stock flow:

```text
Material Request
    ↓
Approved
    ↓
Stock Entry
Main Warehouse → In-Transit Warehouse
    ↓
Physical Transport
    ↓
End-Transit Stock Entry
In-Transit Warehouse → Depot Warehouse
```

The final implementation should reuse ERPNext's standard in-transit stock workflow rather than create a second stock-transfer mechanism in Vuna.

## Items

Items requested by the depot through the Material Request.

For V1, Material Request type is primarily:

```text
Material Transfer
```

Other Material Request types may be considered later.

## Who approves it

Handled through the existing ERPNext approval/workflow process.

Exact approval roles can remain company-configurable.

## Who plans transport

Transport Department.

The Transport Department is responsible for:

- route planning,
- load bundling,
- vehicle selection,
- vehicle allocation,
- fleet coordination,
- zones/routes where applicable.

## Can it share a vehicle with other work?

Yes.

Depot replenishment may share a vehicle with customer deliveries or other compatible transport work when:

- destinations are compatible,
- the movements share a route,
- vehicle capacity allows it,
- operational timing permits it.

Example:

```text
Main Warehouse
    ↓
Customer Delivery
    ↓
Nkubu Depot Replenishment
```

## What the driver must do

The driver primarily executes the planned trip.

For depot replenishment, the driver should also capture **Proof of Delivery** at the depot to confirm that the transferred goods physically reached the destination.

## Proof required

At minimum, proof should support confirmation that the depot received the goods.

Potential proof details include:

- delivered quantities,
- receiver name,
- signature,
- timestamp,
- GPS,
- photos where needed,
- shortages / discrepancy reasons.

## ERPNext document posted after completion

Final/end-transit Stock Entry.

This completes the stock movement from:

```text
In-Transit Warehouse
→ Depot Warehouse
```

## Who pays for transport

Company.

---

# 2. Customer → Depot / Company Return

## Scenario

Goods are returned from a customer back to the company.

The destination may be either:

- the Main Warehouse / Factory, or
- a nearby Depot.

The destination is selected based on operational convenience and company rules.

## Requested by

A return may be initiated by:

- Customer,
- Salesperson,
- optionally Driver.

Driver initiation should remain optional rather than mandatory.

## Source

Customer's saved ERPNext Address.

No separate Vuna delivery-location master is required for the customer.

## Destination

One of:

```text
Main Warehouse / Factory
Nearby Depot
```

Goods may therefore be returned to either the main site or a depot.

## ERPNext / Vuna document that starts it

```text
Return Request
```

The Return Request is the Vuna orchestration document for this reverse movement.

It may originate from:

- customer request,
- POD rejection,
- failed delivery,
- salesperson action,
- optionally driver action.

## Who approves it

Exact approval rules are not finalized.

Use ERPNext/Vuna roles and permissions/workflows later to define this.

Do not hardcode approval roles into the transport architecture.

## Who plans transport

Transport Department where company transport is required.

However, a return may also move on an already active trip.

Example:

```text
Driver makes customer delivery
    ↓
Collects customer return
    ↓
Continues remaining deliveries
    ↓
Eventually delivers returned goods to depot/factory
```

## Can it share a vehicle with other work?

Yes.

This is an important requirement.

Customer returns may share a vehicle with:

- remaining customer deliveries,
- depot replenishment,
- depot returns,
- supplier pickups,
- other compatible work.

Returned goods therefore do not automatically require a dedicated vehicle or dedicated trip.

## What the driver must do

The driver should capture **Proof of Pickup**.

This confirms that goods were physically collected from the customer.

The pickup proof may later be reconciled against proof of receipt at the depot/factory.

## Proof required

For pickups, capture:

- actual quantities collected,
- recipient/provider name,
- signature,
- photos,
- GPS,
- timestamp,
- reason for shortages / missing quantity where applicable.

## ERPNext document posted after completion

For a standard customer return:

```text
Sales Return
(Return Delivery Note)
```

If the original transaction was an Invoice with `Update Stock`, the appropriate return may instead be handled through the corresponding ERPNext return/credit flow.

The exact ERPNext mapping should be confirmed during architecture design.

## Who pays for transport

Company, depending on the applicable delivery/fulfilment arrangement.

Transport charging/costing logic is out of scope for the current release.

---

# 3. Depot → Main Warehouse / Factory Return

## Scenario

A depot returns stock to the Main Warehouse or Factory.

Example:

```text
Nkubu Depot
    ↓
Main Warehouse
```

## Requested by

Initiated through the same generic:

```text
Return Request
```

The intent is to reuse Return Request for all return scenarios rather than create separate return-request documents for each movement type.

## Source

Depot Warehouse.

## Destination

Main Warehouse or Factory Warehouse.

## ERPNext / Vuna document that starts it

```text
Return Request
```

The stock movement itself will ultimately use ERPNext warehouse-to-warehouse stock movement.

## Who approves it

To be handled through roles, permissions, and workflow configuration later.

## Who plans transport

Transport Department.

## Can it share a vehicle with other work?

Yes.

This should behave similarly to a customer return.

For example:

```text
Vehicle leaves Main Warehouse
    ↓
Customer deliveries
    ↓
Depot replenishment
    ↓
Collect depot returns
    ↓
Return to Main Warehouse
```

## What the driver must do

Capture **Proof of Pickup** at the depot.

## Proof required

Same pickup proof expectations:

- quantities,
- provider/receiver name,
- signature,
- photos,
- GPS,
- timestamp,
- shortage/discrepancy reasons.

## ERPNext document posted after completion

```text
Stock Entry
```

because this is a warehouse-to-warehouse movement.

The final architecture should determine whether the movement uses an in-transit flow similar to depot replenishment.

## Who pays for transport

Company.

---

# 4. Supplier → Company Pickup

## Scenario

The company collects purchased goods from a supplier.

Example:

```text
Supplier
    ↓
Company Warehouse
```

## Requested by

Company procurement process.

## Source

Supplier's saved ERPNext Address.

No separate supplier-delivery-location master is required for V1.

## Destination

Company Warehouse.

## ERPNext document that starts it

Current preferred source:

```text
Purchase Order
```

The Purchase Order represents the goods that the company expects to collect.

This should be validated against ERPNext purchasing behaviour during architecture design.

## Items

Purchase Order items.

## Who approves it

Procurement / ERPNext approval workflow.

Exact roles are not part of the transport design.

## Who plans transport

Transport Department.

Supplier pickups requiring company vehicles are always planned by Transport.

## Can it share a vehicle with other work?

Yes.

Supplier pickups can share a vehicle when:

- the supplier lies on or near the planned route,
- capacity is available,
- timing is compatible,
- the cargo is compatible with existing load.

Example:

```text
Main Warehouse
    ↓
Customer Delivery
    ↓
Supplier Pickup
    ↓
Depot Delivery
    ↓
Return to Warehouse
```

## What the driver must do

Capture **Proof of Pickup** confirming the materials collected from the supplier.

## Proof required

At minimum:

- collected quantities,
- supplier/provider name,
- signature,
- photos,
- GPS,
- timestamp,
- shortage / discrepancy reasons.

## ERPNext document posted after completion

```text
Purchase Receipt
```

The Purchase Receipt is the final stock receipt at the company warehouse.

## Who pays for transport

Not yet confirmed.

This must remain an open business question.

Do not hardcode supplier pickups as company-paid or supplier-paid until clarified.

---

# 5. Mixed Trip – Deliveries + Transfers + Pickups

## Scenario

A single vehicle performs multiple types of transport work in one trip.

This is an essential design requirement.

A single trip may contain all of the following simultaneously:

```text
Deliver customer orders
Deliver depot replenishment
Collect customer returns
Collect depot returns
Collect supplier materials
```

Example:

```text
Main Warehouse
    ↓
Customer A Delivery
    ↓
Supplier Pickup
    ↓
Customer B Delivery + Customer Return Pickup
    ↓
Nkubu Depot Replenishment
    ↓
Collect Depot Return
    ↓
Main Warehouse
```

## Documents that may initiate work in the trip

Pickups may originate from:

```text
Return Request
Purchase Order
```

Deliveries may originate from:

```text
Delivery Note
In-Transit Stock Entry
```

or from the upstream planning documents that generate them.

## Who plans transport

Transport Department.

The Transport Department determines:

- which requests share the vehicle,
- route order,
- vehicle allocation,
- capacity feasibility,
- operational timing.

## Can it share a vehicle with other work?

Yes.

This is the defining requirement of the mixed-trip model.

The architecture should **not** assume one trip = one business-document type.

## Driver responsibilities

The driver should see a sequence of stop tasks.

Each stop may contain one or more actions, for example:

```text
Stop: Customer A
- Deliver DN-0001
- Capture POD

Stop: Supplier X
- Collect PO-0012 items
- Capture Proof of Pickup

Stop: Customer B
- Deliver DN-0002
- Collect RR-0007 return
- Capture POD
- Capture Proof of Pickup

Stop: Nkubu Depot
- Deliver depot replenishment
- Capture POD
- Collect depot return
- Capture Proof of Pickup
```

## Cargo / manifest requirement

The trip must track both:

```text
Cargo loaded at departure
Cargo delivered during the trip
Cargo collected during the trip
Cargo remaining on vehicle
```

A vehicle's cargo therefore changes dynamically as stops are completed.

The future document model should support this explicitly.

---

# Cross-Cutting Business Rules

## Locations

### Depots

Depots are already represented as:

```text
ERPNext Warehouses
```

Do not create a parallel Vuna Depot master unless a future requirement genuinely requires one.

### Customers

Use customers' existing ERPNext Addresses.

### Suppliers

Use suppliers' existing ERPNext Addresses.

### Separate delivery locations

No separate Vuna delivery-location master is currently maintained.

Reuse standard ERPNext address/location data.

---

# Planning

Transport planning is owned by:

```text
Transport Department
```

They are responsible for:

- routes,
- zones,
- fleet,
- vehicle allocations,
- bundling,
- combining compatible requests,
- day-to-day dispatch planning.

The system should support planners rather than automatically making all planning decisions.

---

# Routes

Current operating model is primarily:

```text
Dynamically planned each day
```

Do not assume all routes are fixed recurring routes.

Route/zone masters may still be useful for:

- geography,
- recurring destinations,
- planning assistance,
- reporting,
- costing,

but the architecture must support dynamic daily routing.

---

# Capacity

Primary planning metric:

```text
Weight
```

Weight is defined through Vehicle Category / vehicle-capacity configuration.

For V1, capacity planning can primarily use:

```text
vehicle weight capacity
vs
planned cargo weight
```

Do not require volume, pallet spaces, crate count, or carton count unless they become a confirmed requirement.

The architecture should remain extensible to those metrics later.

---

# Material Requests

Current essential Material Request type:

```text
Material Transfer
```

This supports warehouse/depot replenishment.

Other Material Request types may be supported later.

Potential future configuration may define which Material Request types generate transport demand, but this is not yet required for V1.

---

# Returns

Goods may be returned to:

```text
Main Warehouse / Factory
Depot
```

The Main Warehouse has a dedicated Returns Warehouse.

At depots, return handling currently depends on the Depot Supervisor.

The Returns feature should therefore not assume that every return destination has a dedicated Returns Warehouse.

Return processing may differ depending on destination.

---

# Driver Proof Requirements

For pickups, capture:

```text
Actual quantities
Signature
Provider / recipient name
Photos
GPS
Timestamp
Reasons for shortages / discrepancies
```

For deliveries, capture equivalent Proof of Delivery information where applicable.

The architecture should distinguish:

```text
Proof of Delivery
```

from:

```text
Proof of Pickup
```

while allowing both to exist within the same trip/stop.

---

# Supplier Pickup Source Document

Current preferred starting document:

```text
Purchase Order
```

because the final stock document will normally be:

```text
Purchase Receipt
```

This is not fully confirmed.

Codex should investigate whether Purchase Order is the correct transport-demand source or whether another ERPNext document/state would be more appropriate.

---

# Costing

Transport costing is **not part of this design phase**.

For now:

- do not build transport cost allocation into these movement flows,
- do not automatically affect item valuation,
- allow users to use existing ERPNext accounting processes for costs.

Landed Cost Voucher integration can be considered later if required.

---

# V1 Scope

The following movement types are essential for the first release:

```text
Main Warehouse → Depot
Depot → Main Warehouse
```

These must be supported cleanly.

The architecture should also be designed with:

```text
Customer Returns
Supplier Pickups
Mixed Trips
```

in mind because they materially affect the underlying trip/stop model.

However, implementation may be phased.

---

# Critical Architectural Requirement: Mixed Trips

Yes, a single trip **must be capable of combining different movement types**.

A trip may simultaneously contain:

```text
Customer deliveries
Depot replenishment
Customer returns
Depot returns
Supplier pickups
```

The recommended conceptual direction is:

```text
Trip
    ↓
Ordered Stops
    ↓
Stop Tasks
```

Each Stop Task represents one operation such as:

```text
Deliver
Pickup
Transfer Delivery
Return Pickup
Supplier Collection
```

A stop may contain multiple tasks.

Example:

```text
Customer B Stop

Task 1:
Deliver DN-0002

Task 2:
Collect RR-0007

Proof:
POD + Proof of Pickup
```

The manifest should track:

```text
Loaded Cargo
Delivered Cargo
Collected Cargo
Remaining Cargo
```

Do not assume that cargo only decreases during a trip.

---

# Open Questions

The following decisions are still unresolved and should be highlighted rather than guessed:

## Supplier transport payer

Who pays for transport when the company collects goods from a supplier?

Possible answers may depend on:

- supplier agreement,
- Purchase Order terms,
- delivery method,
- contract terms.

Do not hardcode this.

## Supplier pickup source

Purchase Order is the current preferred source, but confirm whether it is the correct ERPNext document/state for creating transport demand.

## Depot return stock flow

Determine whether Depot → Main Warehouse returns should use:

```text
Material Request
+ In-Transit Stock Entry
```

or whether Return Request should independently orchestrate the warehouse transfer while ERPNext Stock Entry remains authoritative.

## Return handling at depots

Main Warehouse has a dedicated Returns Warehouse.

Depots currently rely on Depot Supervisor handling.

Determine how the return architecture should support both without forcing every depot to create a dedicated Returns Warehouse.

---

# What Codex Should Produce Next

Using the scenarios and decisions above, produce:

1. **Proposed document model**
   - identify generic transport-demand objects,
   - trip,
   - stop,
   - stop task,
   - cargo/manifest structures,
   - links to ERPNext documents.

2. **Lifecycle and status diagrams**
   - depot transfer,
   - depot return,
   - customer return,
   - supplier pickup,
   - mixed trip.

3. **ERPNext document mappings**
   - Material Request,
   - Stock Entry,
   - Delivery Note,
   - Return Request,
   - Sales Return,
   - Purchase Order,
   - Purchase Receipt.

4. **Load bundling rules**
   - how different movement types can share a vehicle,
   - compatibility rules,
   - route compatibility,
   - capacity rules.

5. **Trip / stop architecture**
   - especially support for stops containing both deliveries and pickups.

6. **Mobile driver workflow**
   - ordered stops,
   - delivery tasks,
   - pickup tasks,
   - POD,
   - Proof of Pickup,
   - shortages,
   - completion.

7. **Manifest / cargo model**
   - starting cargo,
   - delivered quantity,
   - collected quantity,
   - current onboard cargo.

8. **Phased implementation plan**
   - identify which features belong in V1,
   - which can be added later.

9. **Migration / compatibility risks**
   - especially with existing Delivery Order,
   - Delivery Load,
   - Vehicle Allocation,
   - Delivery Trip,
   - POD,
   - Return Request.

10. **Open questions requiring business confirmation**
    - do not silently invent missing business rules.

The proposed design should reuse existing Vuna and ERPNext structures wherever possible rather than creating parallel models.
