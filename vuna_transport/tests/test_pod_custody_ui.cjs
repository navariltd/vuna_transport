const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function form() {
  const handlers = {};
  const row = {
    rejected_qty: 20,
    depot_received_rejected_qty: 15,
    rejected_serial_no: "S1\nS2",
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/proof_of_delivery/proof_of_delivery.js"
      ),
      "utf8"
    ),
    {
      frappe: {
        ui: { form: { on: (doctype, events) => (handlers[doctype] = events) } },
        get_doc: () => row,
        model: {
          set_value: (_cdt, _cdn, values) => Object.assign(row, values),
        },
      },
      flt: (value) => Number(value) || 0,
    }
  );
  return {
    row,
    update: () =>
      handlers["Proof of Delivery Item"].rejected_goods_handling(
        { doc: { proof_type: "Transfer Receipt" } },
        "Proof of Delivery Item",
        "ROW"
      ),
  };
}

test("all-depot and all-vehicle choices require no quantity input", () => {
  const { row, update } = form();
  row.rejected_goods_handling = "All received by depot";
  update();
  assert.equal(row.depot_received_rejected_qty, 20);
  assert.equal(row.rejected_with_driver_qty, 0);
  assert.equal(row.depot_received_rejected_serial_no, "S1\nS2");
  row.rejected_goods_handling = "All remain on vehicle";
  update();
  assert.equal(row.depot_received_rejected_qty, 0);
  assert.equal(row.rejected_with_driver_qty, 20);
});

test("split retains entered depot quantity and calculates vehicle balance", () => {
  const { row, update } = form();
  row.rejected_goods_handling = "Split between depot and vehicle";
  update();
  assert.equal(row.depot_received_rejected_qty, 15);
  assert.equal(row.rejected_with_driver_qty, 5);
  const schema = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/proof_of_delivery_item/proof_of_delivery_item.json"
      ),
      "utf8"
    )
  );
  assert.ok(
    schema.fields
      .find((field) => field.fieldname === "depot_received_rejected_qty")
      .depends_on.includes("Split between depot and vehicle")
  );
  assert.equal(
    schema.fields.find(
      (field) => field.fieldname === "rejected_with_driver_qty"
    ).read_only,
    1
  );
});
