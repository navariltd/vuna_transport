const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(category = null) {
  const events = {},
    queries = {},
    buttons = [],
    messages = [],
    scrolled = [],
    calls = [],
    changes = [];
  const row = { delivery_load: "TL" };
  const context = {
    __: (value) => value,
    frappe: {
      ui: {
        form: {
          on: (doctype, handlers) => {
            events[doctype] = handlers;
          },
        },
      },
      msgprint: (message) => messages.push(message),
      hide_msgprint: () => {},
      get_doc: () => row,
      call: (options) => {
        calls.push(options);
        return Promise.resolve({ message: null });
      },
      model: {
        set_value: (_cdt, _cdn, field, value) => {
          row[field] = value;
          changes.push([field, value]);
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/vehicle_allocation/vehicle_allocation.js"
      ),
      "utf8"
    ),
    context
  );
  const frm = {
    doc: {
      docstatus: 0,
      company: "Company",
      required_vehicle_category: category,
      delivery_loads: [],
      loaded_items: [],
    },
    set_query: (field, table, query) => {
      queries[field] = query || table;
    },
    scroll_to_field: (field) => scrolled.push(field),
    remove_custom_button: () => {},
    add_custom_button: (label, action) => buttons.push({ label, action }),
  };
  events["Vehicle Allocation"].setup(frm);
  return {
    context,
    events,
    queries,
    buttons,
    messages,
    scrolled,
    calls,
    changes,
    frm,
  };
}

test("load searches explain the missing category without running broad custom queries", () => {
  const state = fixture();
  for (const field of ["delivery_load"]) {
    const query = state.queries[field]();
    assert.equal(query.query, undefined);
    assert.equal(query.filters.name[0], "in");
    assert.equal(query.filters.name[1].length, 0);
  }
  assert.equal(state.calls.length, 0);
  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].title, "Vehicle or Category Required");
  assert.equal(state.scrolled.at(-1), "vehicle");
  state.messages[0].primary_action.action();
  assert.equal(state.scrolled.at(-1), "vehicle");
});

test("vehicles can be selected without first choosing a category", () => {
  const state = fixture();
  const query = state.queries.vehicle();
  assert.equal(query.query, "vuna_transport.api.get_vehicles_for_allocation");
  assert.equal(query.filters.company, "Company");
  assert.equal(query.filters.required_vehicle_category, null);
  assert.equal(state.messages.length, 0);
});

test("vehicle searches include every selected load for capability checks", () => {
  const state = fixture();
  state.frm.doc.delivery_loads = [
    { delivery_load: "AMBIENT" },
    { delivery_load: "COLD" },
    { delivery_load: "" },
  ];
  const query = state.queries.vehicle();
  assert.deepEqual(Array.from(query.filters.delivery_loads), [
    "AMBIENT",
    "COLD",
  ]);
});

test("selecting a vehicle fills category, ownership, supplier and cost structure", async () => {
  const state = fixture();
  state.frm.doc.vehicle = "TRUCK";
  state.frm.doc.cost_structure = "Old Structure";
  state.frm.set_value = (field, value) => {
    state.frm.doc[field] = value;
  };
  state.context.frappe.db = {
    get_value: async () => ({
      message: {
        vuna_vehicle_category: "Canter",
        vuna_vehicle_owner: "Supplier",
        vuna_supplier: "Transporter",
        vuna_transport_cost_structure: "Mileage",
      },
    }),
  };
  await state.events["Vehicle Allocation"].vehicle(state.frm);
  assert.equal(state.frm.doc.required_vehicle_category, "Canter");
  assert.equal(state.frm.doc.allocation_type, "Subcontractor");
  assert.equal(state.frm.doc.vehicle_owner, "Supplier");
  assert.equal(state.frm.doc.supplier, "Transporter");
  assert.equal(state.frm.doc.cost_structure, "Mileage");
});

test("a late response from a previously selected vehicle cannot overwrite the current choice", async () => {
  const state = fixture();
  const pending = {};
  state.context.frappe.db = {
    get_value: (_doctype, name) =>
      new Promise((resolve) => {
        pending[name] = resolve;
      }),
  };
  state.frm.set_value = (field, value) => {
    state.frm.doc[field] = value;
  };
  state.frm.doc.vehicle = "OLD";
  const old = state.events["Vehicle Allocation"].vehicle(state.frm);
  state.frm.doc.vehicle = "NEW";
  const current = state.events["Vehicle Allocation"].vehicle(state.frm);
  pending.NEW({
    message: { vuna_vehicle_category: "FH", vuna_vehicle_owner: "Company" },
  });
  await current;
  pending.OLD({
    message: {
      vuna_vehicle_category: "Canter",
      vuna_vehicle_owner: "Supplier",
    },
  });
  await old;
  assert.equal(state.frm.doc.required_vehicle_category, "FH");
  assert.equal(state.frm.doc.allocation_type, "Owned Fleet");
});

test("the load selection action stays discoverable and directs users to category first", () => {
  const state = fixture();
  state.context.update_load_selection_button(state.frm);
  assert.equal(state.buttons[0].label, "Select Transport Loads");
  state.buttons[0].action();
  assert.equal(state.messages.length, 1);
  assert.equal(state.calls.length, 0);
  assert.equal(state.scrolled[0], "vehicle");
});

test("a category keeps the normal compatibility queries and independent driver lookup", () => {
  const state = fixture("Canter");
  for (const field of ["vehicle", "delivery_load"]) {
    const query = state.queries[field]();
    assert.ok(query.query.startsWith("vuna_transport.api."));
    assert.equal(query.filters.required_vehicle_category, "Canter");
  }
  const empty = fixture();
  assert.equal(
    empty.queries.driver().query,
    "vuna_transport.api.get_drivers_for_allocation"
  );
  assert.equal(empty.messages.length, 0);
  assert.equal(state.messages.length, 0);
});

test("direct child selection is cleared without deleting the row or calling load services", () => {
  const state = fixture();
  state.events["Vehicle Allocation Load"].delivery_load(
    state.frm,
    "Vehicle Allocation Load",
    "ROW"
  );
  assert.deepEqual(state.changes, [["delivery_load", null]]);
  assert.equal(state.messages.length, 1);
  assert.equal(state.calls.length, 0);
  state.events["Vehicle Allocation Load"].delivery_load(
    state.frm,
    "Vehicle Allocation Load",
    "ROW"
  );
  assert.equal(state.messages.length, 1);
  assert.equal(state.calls.length, 0);
});

test("submitted allocations do not offer load selection", () => {
  const state = fixture("Canter");
  state.frm.doc.docstatus = 1;
  state.context.update_load_selection_button(state.frm);
  assert.equal(state.buttons.length, 0);
});
