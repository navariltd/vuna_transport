const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function controller(native = true) {
  const requests = [];
  const vuna_transport = { DispatchPlanning: {} };
  const frappe = {
    provide() {},
    pages: { "dispatch-planning": {} },
    call: async (request) => {
      requests.push(request);
      return { message: {} };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/page/dispatch_planning/dispatch_planning.js"
      ),
      "utf8"
    ),
    { frappe, vuna_transport, cint: Number, $: () => ({ data: () => 0 }) }
  );
  const result = Object.create(
    vuna_transport.DispatchPlanning.Controller.prototype
  );
  result.customer_context = native
    ? {
        doctype: "Transport Demand",
        order_field: "transport_demand",
        item_field: "transport_demand_item",
      }
    : {
        doctype: "Delivery Order",
        order_field: "delivery_order",
        item_field: "delivery_order_item",
      };
  result.filters = {
    source_warehouse: { get_value: () => "Loading warehouse" },
  };
  return { result, requests };
}

test("native planner serializes canonical links and the selected loading warehouse", async () => {
  const { result, requests } = controller();
  await result.call("evaluate_manual_load_plan", {
    allocations: [
      {
        delivery_order: "TD-1",
        delivery_order_item: "DI-1",
        allocated_quantity: 2,
      },
    ],
  });
  const row = requests[0].args.allocations[0];
  assert.equal(row.transport_demand, "TD-1");
  assert.equal(row.transport_demand_item, "DI-1");
  assert.equal(row.allocated_quantity, 2);
  assert.equal("delivery_order" in row, false);
  assert.equal("delivery_order_item" in row, false);
  assert.equal(requests[0].args.source_warehouse, "Loading warehouse");
});

test("legacy planner keeps its own reference dialect", async () => {
  const { result, requests } = controller(false);
  await result.call("evaluate_manual_load_plan", {
    allocations: [
      {
        delivery_order: "DO-1",
        delivery_order_item: "OI-1",
        allocated_quantity: 1,
      },
    ],
  });
  assert.equal(requests[0].args.allocations[0].delivery_order, "DO-1");
  assert.equal("transport_demand" in requests[0].args.allocations[0], false);
});

test("using a canonical suggestion retains exact item IDs for subsequent edits", async () => {
  const { result } = controller();
  result.suggestions = [
    {
      allocations: [
        {
          transport_demand: "TD-1",
          transport_demand_item: "DI-1",
          allocated_quantity: 2,
        },
      ],
    },
  ];
  result.allocations = new Map();
  result.ensure_details = async (names) => assert.equal(names[0], "TD-1");
  result.render_demand = () => {};
  result.evaluate_plan = async () => {};
  result.$root = { find: () => [{ scrollIntoView() {} }] };
  await result.use_suggestion({ currentTarget: {} });
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
  assert.equal(result.allocations.get("DI-1").delivery_order, "TD-1");
});
