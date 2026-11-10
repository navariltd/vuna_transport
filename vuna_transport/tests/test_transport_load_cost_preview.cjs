const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(movement = "Supplier Pickup", enabled = true) {
  const state = { requests: [], buttons: {}, messages: [] };
  let events;
  const escape = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  const frappe = {
    ui: {
      form: {
        on: (doctype, handlers) => {
          if (doctype === "Transport Load") events = handlers;
        },
      },
      Dialog: class {
        constructor(options) {
          state.dialog = options;
        }
        show() {
          state.shown = true;
        }
      },
    },
    model: { can_read: () => true },
    utils: { escape_html: escape },
    format: (value) => String(value ?? 0),
    msgprint: (message) => state.messages.push(message),
    call: async (options) => {
      state.requests.push(options);
      if (options.method.endsWith("get_customer_planning_context")) {
        return {
          message: { doctype: "Transport Demand", costing_enabled: enabled },
        };
      }
      return { message: state.estimates || { cost_structures: [] } };
    },
  };
  const context = { frappe, __: (value) => value };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/transport_load/transport_load.js"
      ),
      "utf8"
    ),
    context
  );
  const frm = {
    doc: {
      name: "TL",
      movement_type: movement,
      docstatus: 1,
      delivery_orders: [],
    },
    fields_dict: {
      delivery_orders: { grid: { update_docfield_property() {} } },
    },
    is_new: () => false,
    is_dirty: () => Boolean(state.dirty),
    get_field: () => null,
    trigger() {},
    add_custom_button: (label, action) => {
      state.buttons[label] = action;
    },
  };
  return { state, frm, events, context, frappe };
}

test("saved customer, supplier and transfer loads expose the same cost action", async () => {
  for (const movement of [
    "Customer Delivery",
    "Supplier Pickup",
    "Warehouse Transfer",
  ]) {
    const { state, events, frm } = fixture(movement);
    await events.refresh(frm);
    assert.equal(typeof state.buttons["Estimate Costs"], "function");
    await state.buttons["Estimate Costs"]();
    const request = state.requests.at(-1);
    assert.ok(request.method.endsWith("get_transport_load_cost_estimates"));
    assert.equal(request.args.transport_load, "TL");
    assert.equal(state.shown, true);
  }
});

test("cost action is hidden when disabled, cancelled or permission is missing", async () => {
  for (const mode of ["disabled", "cancelled", "denied"]) {
    const { state, events, frm, frappe } = fixture(
      "Supplier Pickup",
      mode !== "disabled"
    );
    if (mode === "cancelled") frm.doc.docstatus = 2;
    if (mode === "denied") frappe.model.can_read = () => false;
    await events.refresh(frm);
    assert.equal(state.buttons["Estimate Costs"], undefined);
  }
});

test("dirty loads must be saved before a preview request", async () => {
  const { state, context, frm } = fixture();
  state.dirty = true;
  await context.show_transport_load_cost_estimates(frm);
  assert.equal(state.requests.length, 0);
  assert.match(state.messages[0], /Save the Transport Load/);
});

test("preview displays breakdown and warnings without interpreting document text as HTML", async () => {
  const { state, context, frm } = fixture();
  state.estimates = {
    warnings: ["Mixed <currencies>"],
    cost_structures: [
      {
        cost_structure_label: "<script>bad</script>",
        cost_structure_assignment: "Assignment",
        currency: "KES",
        estimated_full_cost: 150,
        estimated_distance: 10,
        distance_uom: "Kilometer",
        warnings: ["Missing <location>"],
        cost_breakdown: [{ explanation: "Fuel <5>" }],
      },
    ],
  };
  await context.show_transport_load_cost_estimates(frm);
  const html = state.dialog.fields[0].options;
  assert.ok(!html.includes("<script>"));
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /Fuel &lt;5&gt;/);
  assert.match(html, /Mixed &lt;currencies&gt;/);
  assert.match(html, /Missing &lt;location&gt;/);
  assert.match(html, /150 KES/);
});
