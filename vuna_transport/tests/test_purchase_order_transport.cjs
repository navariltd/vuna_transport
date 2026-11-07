const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture({
  enabled = true,
  optedIn = true,
  status = "To Receive and Bill",
} = {}) {
  let handlers;
  const calls = [];
  const buttons = [];
  const routes = [];
  const queries = {};
  const prompts = [];
  const frm = {
    doc: {
      name: "PO-1",
      docstatus: 1,
      supplier: "SUP-1",
      company: "Company",
      vuna_delivery_method: optedIn ? "Our Collection" : null,
      status,
    },
    is_dirty: () => false,
    set_query: (field, query) => {
      queries[field] = query;
    },
    set_value: async () => {},
    add_custom_button: (label, action, group) =>
      buttons.push({ label, action, group }),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/purchase_order_transport.js"),
      "utf8"
    ),
    {
      frappe: {
        ui: {
          form: {
            on: (_doctype, value) => {
              handlers = value;
            },
          },
        },
        call: async (options) => {
          calls.push(options);
          return {
            message: options.args.source_type
              ? { enabled, company_pickup: optedIn }
              : options.args.allocation_name
              ? "PR-1"
              : ["TD-1"],
          };
        },
        prompt: (field, action) => prompts.push({ field, action }),
        set_route: (...args) => routes.push(args),
      },
      __: (value) => value,
    }
  );
  return { handlers, frm, calls, buttons, routes, queries, prompts };
}

test("ordinary or closed purchase orders never offer transport generation", async () => {
  for (const options of [
    { optedIn: false },
    { status: "Closed" },
    { status: "On Hold" },
  ]) {
    const context = fixture(options);
    await context.handlers.refresh(context.frm);
    assert.equal(context.calls.length, 0);
    assert.equal(context.buttons.length, 0);
  }
});

test("supplier pickup needs the configured source policy as well as PO opt-in", async () => {
  const context = fixture({ enabled: false });
  await context.handlers.refresh(context.frm);
  assert.equal(context.buttons.length, 0);
});

test("pickup address search is filtered to the purchase supplier", () => {
  const context = fixture();
  context.handlers.setup(context.frm);
  const query = context.queries.vuna_pickup_address();
  assert.equal(query.filters.link_doctype, "Supplier");
  assert.equal(query.filters.link_name, "SUP-1");
  assert.equal(query.filters.disabled, 0);
});

test("delivery method link filters explicit purchasing applicability, not names", () => {
  const context = fixture();
  context.handlers.setup(context.frm);
  const filter = context.queries.vuna_delivery_method().filters;
  assert.equal(filter.disabled, 0);
  assert.equal(filter.buying, 1);
  assert.equal(filter.selling, undefined);
  assert.equal(filter.fulfilment_type, undefined);
});

test("source action generates demand, not a stock or purchase receipt", async () => {
  const context = fixture();
  await context.handlers.refresh(context.frm);
  assert.equal(context.buttons[0].group, "Create");
  await context.buttons[0].action();
  assert.equal(context.calls[1].args.source_doctype, "Purchase Order");
  assert.equal(context.calls[1].args.source_name, "PO-1");
  assert.equal(
    context.calls[1].method,
    "vuna_transport.services.demand.transport_demand_service.create_transport_demands"
  );
  assert.deepEqual(context.routes[0], ["Form", "Transport Demand", "TD-1"]);
});

test("vehicle receipt is an explicit separate purchasing action with company-filtered allocation", async () => {
  const context = fixture();
  await context.handlers.refresh(context.frm);
  const action = context.buttons.find(
    (button) => button.label === "Purchase Receipt (Vehicle)"
  );
  action.action();
  assert.equal(context.prompts[0].field.get_query().filters.company, "Company");
  assert.equal(context.prompts[0].field.get_query().filters.docstatus, 1);
  await context.prompts[0].action({ allocation: "VA-1" });
  assert.equal(context.calls[1].args.allocation_name, "VA-1");
  assert.equal(
    context.calls[1].method,
    "vuna_transport.services.execution.supplier_vehicle_receipt_service.create_vehicle_purchase_receipt"
  );
  assert.deepEqual(context.routes[0], ["Form", "Purchase Receipt", "PR-1"]);
});
