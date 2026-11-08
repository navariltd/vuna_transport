const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(movement = "Warehouse Transfer") {
  const state = {
    calls: [],
    messages: [],
    routes: [],
    buttons: [],
    disabled: false,
  };
  const items = [
    {
      transport_demand: "TD",
      transport_demand_item: "DI-1",
      item_code: "Milk",
      item_name: "Milk",
      available_quantity: 8,
      uom: "Carton",
    },
    {
      transport_demand: "TD",
      transport_demand_item: "DI-2",
      item_code: "Yoghurt",
      item_name: "Yoghurt",
      available_quantity: 3,
      uom: "Crate",
    },
  ];
  let handlers;
  const frappe = {
    ui: {
      form: {
        on: (_doctype, events) => {
          handlers = events;
        },
      },
      Dialog: class {
        constructor(options) {
          Object.assign(this, options);
          state.dialog = this;
          this.fields_dict = {
            items: {
              grid: {
                get_selected_children: () =>
                  options.fields[0].data.filter((row) => row.__checked),
              },
            },
          };
        }
        show() {
          state.shown = true;
        }
        hide() {
          state.hidden = true;
        }
        disable_primary_action() {
          state.disabled = true;
        }
        enable_primary_action() {
          state.disabled = false;
        }
      },
    },
    call: async (options) => {
      state.calls.push(options);
      if (options.method.endsWith("create_transport_load_from_demands")) {
        if (state.fail) throw new Error("Creation failed");
        return { message: "TL" };
      }
      return { message: items };
    },
    msgprint: (message) => state.messages.push(message),
    set_route: (...route) => state.routes.push(route),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/transport_demand/transport_demand.js"
      ),
      "utf8"
    ),
    { frappe, __: (text) => text, flt: (value) => Number(value) || 0 }
  );
  handlers.refresh({
    doc: {
      name: "TD",
      docstatus: 1,
      movement_type: movement,
      source_document_type: "Sales Order",
      __onload: { supplier_requires_planning: true },
    },
    add_custom_button: (label, action) => state.buttons.push({ label, action }),
    call: () => {
      throw new Error("Must not create a load before selection");
    },
  });
  return state;
}

for (const movement of [
  "Warehouse Transfer",
  "Customer Delivery",
  "Supplier Pickup",
]) {
  test(`${movement} opens a preselected, quantity-filled load dialog`, async () => {
    const state = fixture(movement);
    await state.buttons
      .find((button) => button.label === "Transport Load")
      .action();
    assert.equal(state.shown, true);
    assert.equal(state.calls.length, 1);
    const data = state.dialog.fields[0].data;
    assert.equal(data.length, 2);
    assert.ok(
      data.every(
        (row) =>
          row.__checked && row.allocated_quantity === row.available_quantity
      )
    );
    assert.equal(data[0].uom, "Carton");
    assert.equal(data[1].uom, "Crate");
    if (movement === "Warehouse Transfer")
      assert.ok(state.calls[0].method.endsWith("get_transfer_load_items"));
  });
}

test("only checked rows and edited quantities are sent to create the draft load", async () => {
  const state = fixture();
  await state.buttons[0].action();
  const data = state.dialog.fields[0].data;
  data[0].allocated_quantity = 2;
  data[1].__checked = 0;
  await state.dialog.primary_action();
  const allocations = JSON.parse(
    JSON.stringify(state.calls[1].args.allocations)
  );
  assert.deepEqual(allocations, [
    {
      transport_demand: "TD",
      transport_demand_item: "DI-1",
      allocated_quantity: 2,
    },
  ]);
  assert.deepEqual(state.routes[0], ["Form", "Transport Load", "TL"]);
  assert.equal(state.hidden, true);
  assert.equal(state.disabled, false);
});

test("no selection and invalid quantities never create a load", async () => {
  const state = fixture();
  await state.buttons[0].action();
  const data = state.dialog.fields[0].data;
  data.forEach((row) => {
    row.__checked = 0;
  });
  await state.dialog.primary_action();
  assert.equal(state.calls.length, 1);
  data[0].__checked = 1;
  for (const quantity of [0, -1, 9, Number.NaN, Number.POSITIVE_INFINITY]) {
    data[0].allocated_quantity = quantity;
    await state.dialog.primary_action();
    assert.equal(state.calls.length, 1);
  }
});

test("failed creation leaves the dialog open and retry enabled", async () => {
  const state = fixture();
  await state.buttons[0].action();
  state.fail = true;
  await assert.rejects(state.dialog.primary_action(), /Creation failed/);
  assert.equal(state.disabled, false);
  assert.equal(state.hidden, undefined);
});
