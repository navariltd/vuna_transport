const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(values = {}) {
  let handlers;
  const buttons = [];
  const prompts = [];
  const calls = [];
  const routes = [];
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/supplier_vehicle_purchase_receipt.js"),
      "utf8"
    ),
    {
      frappe: {
        ui: {
          form: {
            on: (_name, value) => {
              handlers = value;
            },
          },
        },
        prompt: (field, action) => prompts.push({ field, action }),
        call: async (options) => {
          calls.push(options);
          return { message: "STE-1" };
        },
        set_route: (...args) => routes.push(args),
      },
      __: (value) => value,
    }
  );
  const frm = {
    doc: {
      name: "PR-1",
      company: "Company",
      docstatus: 1,
      vuna_pickup_purchase_order: "PO-1",
      items: [{ vuna_pickup_destination_warehouse: "MAIN" }],
      ...values,
    },
    add_custom_button: (label, action) => buttons.push({ label, action }),
  };
  return { handlers, frm, buttons, prompts, calls, routes };
}

test("only submitted company-owned supplier receipts offer vehicle transfers", () => {
  for (const values of [
    { docstatus: 0 },
    { is_return: 1 },
    { vuna_pickup_purchase_order: null },
  ]) {
    const context = fixture(values);
    context.handlers.refresh(context.frm);
    assert.equal(context.buttons.length, 0);
  }
});

test("arrival creates a normal material transfer with a receiving warehouse, not End Transit", async () => {
  const context = fixture();
  context.handlers.refresh(context.frm);
  assert.equal(context.buttons[0].label, "Stock Entry (Material Transfer)");
  context.buttons[0].action();
  assert.equal(context.prompts[0].field.default, "MAIN");
  assert.equal(context.prompts[0].field.get_query().filters.company, "Company");
  await context.prompts[0].action({ warehouse: "DEPOT" });
  assert.equal(context.calls[0].args.source_name, "PR-1");
  assert.equal(context.calls[0].args.target_warehouse, "DEPOT");
  assert.equal(
    context.calls[0].method,
    "vuna_transport.services.execution.supplier_vehicle_transfer_service.create_vehicle_material_transfer"
  );
  assert.deepEqual(context.routes[0], ["Form", "Stock Entry", "STE-1"]);
});
