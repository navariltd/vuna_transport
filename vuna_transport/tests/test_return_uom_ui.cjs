const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(values = {}) {
  const events = {};
  const row = {
    uom: "Pcs",
    _return_uom: "Carton",
    conversion_factor: 12,
    requested_return_qty: 2,
    delivered_qty: 2,
    remaining_qty: 2,
    stock_uom: "Pcs",
    delivery_note_item: "DNI",
    ...values,
  };
  const calls = [];
  const frappe = {
    ui: {
      form: {
        on: (type, handlers) => {
          events[type] = handlers;
        },
      },
    },
    get_doc: () => row,
    call: async (options) => {
      calls.push(options);
      return {
        message: {
          conversion_factor: row.uom === "Pcs" ? 1 : 12,
          stock_uom: "Pcs",
        },
      };
    },
    model: {
      set_value: async (cdt, cdn, field, value) => {
        if (typeof field === "string") row[field] = value;
        else Object.assign(row, field);
      },
    },
  };
  const context = {
    frappe,
    flt: (value) => Number(value) || 0,
    __: (value) => value,
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/return_request/return_request.js"
      ),
      "utf8"
    ),
    context
  );
  const frm = { doc: { docstatus: 0, items: [row] } };
  return {
    row,
    frm,
    frappe,
    calls,
    context,
    change: () =>
      events["Return Request Item"].uom(frm, "Return Request Item", "RRI"),
  };
}

test("changing cartons to pieces preserves stock quantity", async () => {
  const state = fixture();
  await state.change();
  assert.equal(state.row.requested_return_qty, 24);
  assert.equal(state.row.delivered_qty, 24);
  assert.equal(state.row.remaining_qty, 24);
  assert.equal(state.row.conversion_factor, 1);
  assert.equal(state.calls[0].args.source_doctype, "Delivery Note Item");
});

test("changing back to cartons does not multiply quantities twice", async () => {
  const state = fixture();
  await state.change();
  state.row.uom = "Carton";
  await state.change();
  assert.equal(state.row.requested_return_qty, 2);
  assert.equal(state.row.conversion_factor, 12);
});

test("submitted requests do not change unit or quantities", async () => {
  const state = fixture();
  state.frm.doc.docstatus = 1;
  await state.change();
  assert.equal(state.calls.length, 0);
  assert.equal(state.row.requested_return_qty, 2);
});

test("invalid UOM restores previous selection and leaves quantities unchanged", async () => {
  const state = fixture();
  state.frappe.call = async () => {
    throw new Error("Invalid UOM");
  };
  await assert.rejects(state.change, /Invalid UOM/);
  assert.equal(state.row.uom, "Carton");
  assert.equal(state.row.conversion_factor, 12);
  assert.equal(state.row.requested_return_qty, 2);
  assert.equal(state.row._changing_return_uom, false);
});

test("source stock invoices and depot receipts resolve the matching parent", async () => {
  for (const [fields, expected] of [
    [
      { delivery_note_item: null, sales_invoice_item: "SII" },
      "Sales Invoice Item",
    ],
    [
      { delivery_note_item: null, stock_entry_detail: "SED" },
      "Stock Entry Detail",
    ],
    [
      { delivery_note_item: null, source_pod_item: "PODI" },
      "Proof of Delivery Item",
    ],
  ]) {
    const state = fixture(fields);
    await state.change();
    assert.equal(state.calls[0].args.source_doctype, expected);
  }
});

test("UOM is editable in draft but conversion factor remains server-owned", () => {
  const metadata = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/return_request_item/return_request_item.json"
      ),
      "utf8"
    )
  );
  const uom = metadata.fields.find((field) => field.fieldname === "uom");
  const factor = metadata.fields.find(
    (field) => field.fieldname === "conversion_factor"
  );
  assert.ok(!uom.read_only);
  assert.ok(!uom.allow_on_submit);
  assert.equal(factor.read_only, 1);
});

test("reopening item selection preserves an existing piece-based return", () => {
  const state = fixture({ conversion_factor: 1, requested_return_qty: 5 });
  let configuration;
  state.frappe.ui.Dialog = class {
    constructor(options) {
      configuration = options;
    }
    show() {}
  };
  state.context.show_returnable_items_dialog(state.frm, [
    {
      delivery_note_item: "DNI",
      uom: "Carton",
      stock_uom: "Pcs",
      conversion_factor: 12,
      requested_return_qty: 2,
      delivered_qty: 2,
    },
  ]);
  const row = configuration.fields[0].data[0];
  assert.equal(row.uom, "Pcs");
  assert.equal(row.conversion_factor, 1);
  assert.equal(row.requested_return_qty, 5);
  assert.equal(row.available_return_qty, 24);
  assert.equal(row.delivered_qty, 24);
});
