const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

test("refresh preserves upstream exclusions after ERPNext resets them, but permits POD cascade", () => {
  let handlers;
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/delivery_trip.js"),
      "utf8"
    ),
    {
      frappe: { ui: { form: { on: (_doctype, value) => (handlers = value) } } },
      __: (text) => text,
    }
  );
  const frm = {
    doc: { docstatus: 2, delivery_stops: [] },
    set_query() {},
    toggle_display() {},
    page: { set_inner_btn_group_as_primary() {} },
  };
  handlers.setup(frm);
  for (let refresh = 0; refresh < 2; refresh++) {
    // This assignment occurs in ERPNext's standard refresh handler.
    frm.ignore_doctypes_on_cancel_all = ["Delivery Note"];
    handlers.refresh(frm);
    for (const upstream of [
      "Delivery Note",
      "Vehicle Allocation",
      "Transport Load",
      "Transport Trip Task",
      "Serial and Batch Bundle",
    ])
      assert.ok(frm.ignore_doctypes_on_cancel_all.includes(upstream));
    for (const downstream of [
      "Proof of Delivery",
      "Return Request",
      "Stock Entry",
    ])
      assert.ok(!frm.ignore_doctypes_on_cancel_all.includes(downstream));
  }
});
