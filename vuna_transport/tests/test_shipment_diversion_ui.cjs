const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function load(script) {
  const handlers = {};
  const calls = [];
  const frappe = {
    ui: {
      form: {
        on: (doctype, events) =>
          Object.assign((handlers[doctype] ||= {}), events),
      },
    },
    model: {
      can_create: () => true,
      open_mapped_doc: (args) => calls.push(args),
    },
    msgprint: (message) => calls.push(message),
  };
  const context = { frappe, __: (message) => message };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, script), "utf8"),
    context
  );
  return { handlers, context, frappe, calls };
}

test("Divert Shipment opens a linked unsaved plan only from an eligible customer return", () => {
  const state = load(
    "../vuna_transport/doctype/return_request/return_request.js"
  );
  let action;
  const frm = {
    doc: {
      docstatus: 1,
      return_context: "Customer",
      proof_of_delivery: "POD",
      delivery_note: "DN",
      status: "Awaiting Sales Return",
    },
    perm: [{ write: 1 }],
    is_dirty: () => false,
    add_custom_button: (label, callback, group) => {
      assert.equal(label, "Divert Shipment");
      assert.equal(group, "Create");
      action = callback;
    },
  };
  state.context.add_shipment_diversion_action(frm);
  action();
  assert.equal(
    state.calls[0].method,
    "vuna_transport.services.execution.shipment_diversion_service.make_shipment_diversion"
  );
  assert.equal(state.calls[0].frm, frm);
  frm.is_dirty = () => true;
  action();
  assert.match(state.calls[1], /Save the Return Request/);
});

test("draft, depot, closed and read-only cases do not offer diversion", () => {
  const state = load(
    "../vuna_transport/doctype/return_request/return_request.js"
  );
  const base = {
    docstatus: 1,
    return_context: "Customer",
    proof_of_delivery: "POD",
    delivery_note: "DN",
    status: "Awaiting Sales Return",
  };
  for (const changes of [
    { docstatus: 0 },
    { return_context: "Depot" },
    { status: "Resolved" },
    { status: "Closed" },
    { proof_of_delivery: null },
  ]) {
    state.context.add_shipment_diversion_action({
      doc: { ...base, ...changes },
      perm: [{ write: 1 }],
      add_custom_button: () => assert.fail("Unexpected action"),
    });
  }
  state.frappe.model.can_create = () => false;
  state.context.add_shipment_diversion_action({
    doc: base,
    perm: [{ write: 1 }],
    add_custom_button: () => assert.fail("Permission bypass"),
  });
});

test("destination controls scope customers, addresses and receiving warehouses", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  const queries = {};
  const frm = {
    doc: { company: "Company", original_customer: "A" },
    fields_dict: { items: { grid: {} } },
    set_query: (name, fn) => {
      queries[name] = fn;
    },
  };
  state.handlers["Shipment Diversion"].setup(frm);
  assert.equal(queries.customer().filters.name[1], "A");
  assert.equal(queries.delivery_address().filters.name[1], "");
  frm.doc.customer = "B";
  assert.equal(queries.delivery_address().filters.link_name, "B");
  assert.equal(queries.destination_warehouse().filters.company, "Company");
  assert.equal(
    queries.destination_warehouse().filters.warehouse_type[1],
    "Transit"
  );
});

test("changing destination clears stale customer or warehouse fields", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  const changes = [];
  const frm = {
    doc: { destination_type: "Depot" },
    set_value: (...args) => changes.push(args),
  };
  const handlers = state.handlers["Shipment Diversion"];
  handlers.destination_type(frm);
  assert.equal(changes[0][0].customer, "");
  assert.equal(changes[0][0].delivery_address, "");
  frm.doc.destination_type = "Customer";
  handlers.destination_type(frm);
  assert.equal(changes[1][0], "destination_warehouse");
  handlers.customer(frm);
  assert.equal(changes[2][0], "delivery_address");
});

test("approval UI does not claim to have posted stock or changed a trip", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  let message;
  state.handlers["Shipment Diversion"].refresh({
    doc: { docstatus: 1, return_request: "RR-1" },
    set_intro: (value) => {
      message = value;
    },
  });
  assert.match(message, /not created by approval/);
  assert.equal(state.calls.length, 0);
});

test("definitions preserve audit timestamps, enforce roles and reset copied approval", () => {
  const doc = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/shipment_diversion/shipment_diversion.json"
      ),
      "utf8"
    )
  );
  const fields = Object.fromEntries(
    doc.fields.map((field) => [field.fieldname, field])
  );
  assert.equal(doc.is_submittable, 1);
  assert.equal(doc.track_changes, 1);
  assert.equal(fields.custody_confirmed.no_copy, 1);
  assert.equal(fields.approved_by.read_only, 1);
  assert.equal(fields.vehicle_warehouse.read_only, 1);
  assert.ok(
    !doc.permissions.some((permission) => permission.role === "Driver")
  );
  assert.match(doc.creation, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}$/);
  assert.ok(
    doc.field_order.indexOf("destination_type") <
      doc.field_order.indexOf("items")
  );
});

test("approved diversions offer destination-specific draft document actions", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  const actions = new Map();
  const frm = {
    doc: {
      name: "SD",
      docstatus: 1,
      return_request: "RR",
      destination_type: "Customer",
    },
    perm: [{ write: 1 }],
    set_intro() {},
    is_dirty: () => false,
    add_custom_button: (label, callback, group) => {
      if (group !== "Create") return;
      actions.set(label, callback);
    },
  };
  state.handlers["Shipment Diversion"].refresh(frm);
  assert.deepEqual(
    [...actions.keys()],
    ["Sales Return", "Sales Order", "Delivery Note"]
  );
  let request;
  state.frappe.call = (args) => {
    request = args;
  };
  actions.get("Sales Return")();
  assert.equal(request.args.shipment_diversion, "SD");
  assert.equal(request.args.document_type, "Sales Return");
  assert.equal(request.freeze, true);
  actions.clear();
  frm.doc.destination_type = "Depot";
  state.handlers["Shipment Diversion"].refresh(frm);
  assert.deepEqual([...actions.keys()], ["Sales Return", "Stock Entry"]);
  frm.is_dirty = () => true;
  request = null;
  actions.get("Stock Entry")();
  assert.equal(request, null);
});

test("unapproved and read-only diversions do not offer document creation", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  for (const docstatus of [0, 2]) {
    state.handlers["Shipment Diversion"].refresh({
      doc: { docstatus, return_request: "RR" },
      perm: [{ write: 1 }],
      set_intro() {},
      add_custom_button: () => assert.fail("Unapproved creation"),
    });
  }
  state.handlers["Shipment Diversion"].refresh({
    doc: { docstatus: 1, return_request: "RR" },
    perm: [{ read: 1 }],
    set_intro() {},
    add_custom_button: () => assert.fail("Read-only creation"),
  });
});

test("trip resumption is an explicit confirmed dispatcher action", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  const actions = new Map();
  const frm = {
    doc: {
      name: "SD",
      docstatus: 1,
      return_request: "RR",
      destination_type: "Depot",
    },
    perm: [{ write: 1 }],
    set_intro() {},
    is_dirty: () => false,
    add_custom_button: (label, callback, group) => {
      if (group === "Actions") actions.set(label, callback);
    },
  };
  let confirmation, request;
  state.frappe.confirm = (_message, callback) => {
    confirmation = callback;
  };
  state.frappe.call = (args) => {
    request = args;
  };
  state.handlers["Shipment Diversion"].refresh(frm);
  assert.equal(request, undefined);
  assert.ok(actions.has("Add to Original Trip"));
  actions.get("Resume Original Trip")();
  assert.equal(request, undefined);
  confirmation();
  assert.match(request.method, /resume_original_trip$/);
  assert.equal(request.args.shipment_diversion, "SD");
});

test("depot exceptions offer a follow-up without offering a second Sales Return", () => {
  const state = load(
    "../vuna_transport/doctype/shipment_diversion/shipment_diversion.js"
  );
  const actions = new Map();
  const creates = [];
  const frm = {
    doc: {
      name: "PARENT",
      docstatus: 1,
      return_request: "RR",
      destination_type: "Depot",
      status: "Needs Review",
      execution_task: "TASK",
    },
    perm: [{ write: 1 }],
    is_dirty: () => false,
    set_intro() {},
    add_custom_button: (label, callback, group) => {
      if (group === "Actions") actions.set(label, callback);
      else creates.push(label);
    },
  };
  state.handlers["Shipment Diversion"].refresh(frm);
  actions.get("Redirect Remaining Goods")();
  assert.match(state.calls[0].method, /make_followup_diversion$/);
  assert.equal(state.calls[0].frm, frm);
  creates.length = 0;
  frm.doc.previous_diversion = "EARLIER";
  state.handlers["Shipment Diversion"].refresh(frm);
  assert.ok(!creates.includes("Sales Return"));
});
