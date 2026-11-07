<div align="center">
  <img src="./vuna_transport/public/images/vuna_transport.png" alt="Vuna Transport Logo" height="250px" width="200px"/>
  <h1>Vuna Transport</h1>
  <p>Transportation planning and delivery execution for Frappe and ERPNext</p>

  [![CI](https://github.com/navariltd/vuna_transport/actions/workflows/ci.yml/badge.svg?event=schedule)](https://github.com/navariltd/vuna_transport/actions/workflows/ci.yml)
</div>

<div align="center">
  <a href="https://docs.navari.co.ke/vunatransport/introduction">Documentation</a>
</div>

## Vuna Transport

**Vuna Transport** is a transportation management application built on **Frappe Framework** and **ERPNext**.

It helps businesses move from customer demand to completed delivery by coordinating Delivery Orders, Delivery Loads, fleet availability, Vehicle Allocations, Delivery Notes, Delivery Trips, and Proof of Delivery. ERPNext remains authoritative for stock, accounting, customers, warehouses, vehicles, drivers, and delivery transactions while Vuna Transport provides the transport-planning and execution layer around them.

## Operational Flow

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

## Key Features

* **Fleet Management** - Manage vehicle categories, manufacturers, models, vehicles, assets, classifications, compliance, availability, and incidents.
* **Driver Availability** - Reuse ERPNext Drivers while checking active status, licence validity, allocations, and trips.
* **Delivery Order Generation** - Generate transport demand from eligible Sales Order Items with configurable Delivery Groups and historical snapshots.
* **Delivery Load Planning** - Bundle multiple Delivery Orders or Item lines, support partial quantities, and validate capacity and transport compatibility.
* **Dispatch Planning** - Plan demand using warehouse, customer, date, and geography filters while viewing fleet and driver availability.
* **Vehicle Allocation** - Assign vehicles and drivers to one or more Delivery Loads with server-side availability and capacity validation.
* **Proof of Delivery** - Capture accepted, rejected, partial, and failed delivery outcomes with item-level quantities and rejection reasons.
* **Exceptions and Returns** - Track unresolved delivery outcomes, custody, return requests, inspections, stock returns, and commercial follow-up.
* **Transport Costing Estimates** - Estimate transport costs using Cost Components, Cost Structures, Cost Structure Assignments, destinations, distance, and vehicle context.
* **Geography-Aware Planning** - Use ERPNext Territories with Counties, Constituencies, Towns, addresses, warehouses, and coordinate-based planning data.
* **Driver App Roadmap** - Planned React Native workflows for assigned deliveries, Proof of Delivery, expense requests, and location tracking.

## Basic Usage

### Prepare Transport Demand

1. Create or submit a Sales Order with stocked Items.
2. Confirm the customer delivery Address and source Warehouse.
3. Confirm Item Delivery Groups and Transport Compatibility Groups where required.
4. Generate Delivery Orders from the eligible Sales Order.

### Plan a Delivery Load

1. Open **Dispatch Planning** from the Dispatch Operations workspace.
2. Filter the delivery demand by Company, date range, Warehouse, geography, or Customer.
3. Select a Vehicle Category where the planning team wants to fill a specific vehicle type.
4. Select Delivery Orders or individual Item quantities.
5. Review weight, volume, remaining capacity, compatibility, and recommended categories.
6. Create a Delivery Load after server-side validation.

### Allocate and Execute

1. Open the Delivery Load or create a Vehicle Allocation from the Fleet Management workspace.
2. Select a compatible submitted Vehicle Category, Vehicle, Driver, and Cost Structure where required.
3. Attach one or more Delivery Loads.
4. Create Delivery Notes according to the configured Transport Settings flow.
5. Create or open the ERPNext Delivery Trip.
6. Complete Proof of Delivery for each delivery stop.

### Handle an Exception

1. Record accepted and rejected quantities in Proof of Delivery.
2. Add the rejection reason and delivery remarks where applicable.
3. Create or review the resulting Delivery Exception or Return Request.
4. Record custody, return destination, inspection, replacement, credit, or other resolution actions.

## Configuration

Vuna Transport uses standard ERPNext configuration wherever practical.

* **Transport Settings** control workflow order, default UOMs, capacity override rules, and transport planning behavior.
* **Vehicle Category** defines physical capacity, payload limits, UOMs, and vehicle requirements.
* **Vehicle** identifies the physical resource and may link to an ERPNext Asset and Transport Cost Structure.
* **Driver** remains the standard ERPNext/HRMS Driver record.
* **Delivery Method** defines applicability with **Buying**, **Selling** and **Internal** checkboxes (enable multiple to share a method), and who arranges transport (`Company` or `Counterparty`). Material Transfers use Internal methods; customer Return Requests use Selling and depot returns Internal. Return Requests use the same master, not a separate Return Method select. Custody and stock/quality decisions remain separate. Vehicle Allocation separately selects owned or subcontracted transport.
* **Transport Demand** retains the movement purpose and derives **Demand Type** using ERPNext terminology: customer deliveries are **Selling**, supplier pickups **Buying**, and depot transfers/returns **Internal**. A trip can serve multiple demand types; delivery methods do not determine inventory ownership or stock posting.
* **Delivery Method workflow** requires planning before a new allocation and allocation before a new trip. Company-arranged methods may leave all three options off for explicit manual demand creation; automatic source generation then stays off. Submitted manual supplier demands can use **Assign to Trip** to reuse an active trip's allocation, vehicle and driver without creating another load/allocation. The warehouse team confirms collection through the task's **Purchase Receipt (Vehicle)** action; drivers are not granted inventory-posting authority. Counterparty-arranged methods bypass the company transport workflow.
* **Delivery Group** controls how Item demand is split into Delivery Orders.
* **Transport Compatibility Group** controls which demand can share a Delivery Load.
* **Cost Components** define reusable cost types as Fixed or Variable.
* **Cost Structures** define the costing model; **Cost Structure Assignments** define effective rates and amounts.
* **Territory, County, Constituency, Town, Address, and Warehouse** provide delivery geography and location context.

## Under the Hood

* [**Frappe Framework**](https://github.com/frappe/frappe): Provides the application framework, database layer, authentication, permissions, document model, Desk, and APIs.
* [**ERPNext**](https://github.com/frappe/erpnext): Provides customers, Items, stock, warehouses, Vehicles, Drivers, Delivery Notes, Delivery Trips, accounting, and standard ERP workflows.
* [**Frappe HRMS**](https://github.com/frappe/hrms): Provides employee and driver-related functionality used by the application.
* **Vuna Transport Services**: Provide demand generation, load validation, suggestions, fleet availability, allocation, execution, POD, returns, geography, and costing logic.
* **Native Frappe Desk UI**: Provides the standard forms, workspaces, reports, and dispatch planning experience.
* **Vue inside a Frappe Desk Page**: Powers the reactive Dispatch Planning workbench experience where applicable.
* **React Native**: Planned technology for the Driver App; it is not required for the Desk application.

## Production Setup

Vuna Transport can be deployed on an environment that supports Frappe, ERPNext, and HRMS. Production setup should include backups, permissions review, role configuration, scheduled jobs, and a tested document workflow.

## Development Setup

### Manual Installation

1. Set up a Frappe bench using the [Frappe installation guide](https://docs.frappe.io/framework/user/en/installation), then start the server:

   ```bash
   bench start
   ```

2. Create a site:

   ```bash
   bench new-site vuna_transport.localhost
   ```

3. Install ERPNext and HRMS:

   ```bash
   bench get-app erpnext
   bench get-app hrms
   bench --site vuna_transport.localhost install-app erpnext
   bench --site vuna_transport.localhost install-app hrms
   ```

4. Get and install Vuna Transport:

   ```bash
   bench get-app https://github.com/navariltd/vuna_transport --branch develop
   bench --site vuna_transport.localhost install-app vuna_transport
   ```

5. Migrate and build assets:

   ```bash
   bench --site vuna_transport.localhost migrate
   bench build --app vuna_transport
   ```

6. Open the site:

   ```text
   http://vuna_transport.localhost:8000
   ```

## Documentation

Read the [Vuna Transport documentation](https://docs.navari.co.ke/vunatransport/introduction) for setup instructions, fleet management, dispatch planning, costing, delivery execution, Proof of Delivery, returns, troubleshooting, and developer references.

The documentation source is maintained locally in the [`docs/`](docs/introduction.md) directory and can be imported or synchronized into the Navari Frappe Wiki.

## Contributing

This project uses `pre-commit` for formatting and linting.

```bash
cd apps/vuna_transport
pre-commit install
pre-commit run --all-files
```

The configured checks may include:

* ruff
* eslint
* prettier
* pyupgrade

## CI

GitHub Actions can:

* Install the application and run backend tests.
* Run formatting and linting checks.
* Run Frappe security and dependency checks.

## License

AGPL-3.0
