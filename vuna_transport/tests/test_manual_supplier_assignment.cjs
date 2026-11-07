const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(file, doc, canReceive = true) {
  let handlers, prompt;
  const buttons = [],
    calls = [],
    routes = [];
  const frappe = {
    ui: {
      form: {
        on: (_doctype, events) => {
          handlers = events;
        },
      },
    },
    model: {
      can_create: () => canReceive,
      open_mapped_doc: (options) => calls.push(options),
    },
    prompt: (field, action) => {
      prompt = { field, action };
    },
    confirm: (_message, action) => action(),
    call: async (options) => {
      calls.push(options);
      return { message: "RESULT" };
    },
    set_route: (...args) => routes.push(args),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../vuna_transport/doctype", file),
      "utf8"
    ),
    { frappe, __: (value) => value }
  );
  const frm = {
    doc,
    add_custom_button: (label, action, group) =>
      buttons.push({ label, action, group }),
    reload_doc: () => {},
  };
  handlers.refresh(frm);
  return { buttons, calls, routes, getPrompt: () => prompt };
}

test("submitted supplier demand offers explicit assignment to an active same-company trip", async () => {
  const state = fixture("transport_demand/transport_demand.js", {
    name: "TD",
    docstatus: 1,
    company: "Company",
    movement_type: "Supplier Pickup",
  });
  assert.equal(state.buttons.length, 1);
  assert.equal(state.buttons[0].label, "Assign to Trip");
  state.buttons[0].action();
  const prompt = state.getPrompt();
  const filters = prompt.field.get_query().filters;
  assert.equal(filters.company, "Company");
  assert.equal(filters.docstatus, 1);
  assert.deepEqual(Array.from(filters.status[1]), ["Completed", "Cancelled"]);
  await prompt.action({ delivery_trip: "DT" });
  assert.equal(state.calls[0].args.transport_demand, "TD");
  assert.equal(state.calls[0].args.delivery_trip, "DT");
  assert.deepEqual(state.routes[0], ["Form", "Transport Trip Task", "RESULT"]);
});

test("draft supplier demands cannot be assigned", () => {
  assert.equal(
    fixture("transport_demand/transport_demand.js", {
      docstatus: 0,
      movement_type: "Supplier Pickup",
    }).buttons.length,
    0
  );
});

test("supplier collection uses a reviewed receipt, not a driver stock-posting or POD action", async () => {
  const state = fixture("transport_trip_task/transport_trip_task.js", {
    name: "TASK",
    direct_assignment: 1,
    task_type: "Supplier Pickup",
    status: "Ready",
  });
  const receipt = state.buttons.find(
    (button) => button.label === "Purchase Receipt (Vehicle)"
  );
  assert.equal(receipt.group, "Create");
  await receipt.action();
  assert.equal(state.calls[0].args.task_name, "TASK");
  assert.deepEqual(state.routes[0], ["Form", "Purchase Receipt", "RESULT"]);
  assert.equal(
    state.buttons.some((button) => button.label === "Proof of Delivery"),
    false
  );
  const driver = fixture(
    "transport_trip_task/transport_trip_task.js",
    { direct_assignment: 1, task_type: "Supplier Pickup", status: "Ready" },
    false
  );
  assert.equal(
    driver.buttons.some(
      (button) => button.label === "Purchase Receipt (Vehicle)"
    ),
    false
  );
});

test("existing return and transfer proof actions remain available", () => {
  const pickup = fixture("transport_trip_task/transport_trip_task.js", {
    name: "TASK",
    return_request: "RR",
    task_type: "Return Pickup",
    status: "Ready",
  });
  assert.ok(
    pickup.buttons.some((button) => button.label === "Remove Pickup Assignment")
  );
  assert.ok(
    pickup.buttons.some((button) => button.label === "Proof of Pickup")
  );
  const receipt = fixture("transport_trip_task/transport_trip_task.js", {
    task_type: "Transfer Receipt",
    status: "Ready",
  });
  assert.ok(
    receipt.buttons.some((button) => button.label === "Proof of Delivery")
  );
});
