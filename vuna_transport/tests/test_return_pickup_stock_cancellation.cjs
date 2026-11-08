const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("pickup stock excludes ownership backlinks but retains downstream stock cascade", () => {
  let handlers;
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/return_pickup_stock_entry.js"),
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
      },
    }
  );
  const frm = {
    doc: { vuna_return_pickup_proof: "POD-1" },
    ignore_doctypes_on_cancel_all: ["Serial and Batch Bundle"],
  };
  handlers.refresh(frm);
  for (const name of [
    "Serial and Batch Bundle",
    "Proof of Delivery",
    "Transport Trip Task",
  ])
    assert.ok(frm.ignore_doctypes_on_cancel_all.includes(name));
  assert.ok(!frm.ignore_doctypes_on_cancel_all.includes("Stock Entry"));
  const ordinary = {
    doc: {},
    ignore_doctypes_on_cancel_all: ["Serial and Batch Bundle"],
  };
  handlers.refresh(ordinary);
  assert.deepEqual(ordinary.ignore_doctypes_on_cancel_all, [
    "Serial and Batch Bundle",
  ]);
});
