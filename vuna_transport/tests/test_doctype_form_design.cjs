const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
  const handlers = {};
  const context = vm.createContext({
    frappe: {
      ui: {
        form: {
          on: (name, value) => {
            handlers[name] = value;
          },
        },
      },
      utils: {
        escape_html: (value) =>
          String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;"),
      },
      format: (value) => String(value),
    },
    flt: (value) => Number(value) || 0,
    __: (value, args = []) =>
      value.replace(/\{(\d+)\}/g, (_match, i) => args[i]),
  });
  for (const name of ["return_inspection", "vehicle_allocation"]) {
    vm.runInContext(
      fs.readFileSync(
        path.join(__dirname, `../vuna_transport/doctype/${name}/${name}.js`),
        "utf8"
      ),
      context
    );
  }
  function form(doc) {
    const visible = {};
    const labels = {};
    const rendered = {};
    return {
      doc,
      visible,
      labels,
      rendered,
      toggle_display: (field, value) => {
        visible[field] = value;
      },
      set_df_property: (field, property, value) => {
        labels[field] = { property, value };
      },
      get_field: (field) => ({
        $wrapper: {
          html: (value) => {
            rendered[field] = value;
          },
          text: (value) => {
            rendered[field] = value;
          },
          empty: () => {
            rendered[field] = "";
          },
        },
      }),
    };
  }
  return { context, handlers, form };
}

test("split inspection rows do not multiply their received source quantity", () => {
  const { context } = fixture();
  const summary = context.inspection_quantities_by_uom([
    {
      delivery_note_item: "DN1",
      stock_uom: "Pcs",
      received_stock_qty: 20,
      result: "Accepted",
      inspection_qty: 10,
    },
    {
      delivery_note_item: "DN1",
      stock_uom: "Pcs",
      received_stock_qty: 20,
      result: "Rejected",
      inspection_qty: 10,
    },
    {
      delivery_note_item: "DN2",
      stock_uom: "Kg",
      received_stock_qty: 5,
      result: "Accepted",
      inspection_qty: 3,
    },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(summary)), [
    {
      uom: "Pcs",
      received: 20,
      inspected: 20,
      accepted: 10,
      rejected: 10,
      pending: 0,
    },
    {
      uom: "Kg",
      received: 5,
      inspected: 3,
      accepted: 3,
      rejected: 0,
      pending: 2,
    },
  ]);
});

test("receiving stock batches are distinct while split results share one receipt", () => {
  const { context } = fixture();
  const rows = [
    {
      stock_entry_detail: "STE1",
      batch_no: "B1",
      received_stock_qty: 10,
      stock_uom: "Pcs",
      result: "Accepted",
      inspection_qty: 5,
    },
    {
      stock_entry_detail: "STE1",
      batch_no: "B1",
      received_stock_qty: 10,
      stock_uom: "Pcs",
      result: "Rejected",
      inspection_qty: 5,
    },
    {
      stock_entry_detail: "STE1",
      batch_no: "B2",
      received_stock_qty: 12,
      stock_uom: "Pcs",
      result: "Accepted",
      inspection_qty: 12,
    },
  ];
  const summary = context.inspection_quantities_by_uom(rows)[0];
  assert.equal(summary.received, 22);
  assert.equal(summary.inspected, 22);
});

test("mixed UOM rendering hides misleading aggregate fields without changing stored values", () => {
  const { context, form } = fixture();
  const frm = form({
    total_received_stock_qty: 25,
    results: [
      {
        delivery_note_item: "DN1",
        stock_uom: "Pcs",
        received_stock_qty: 20,
        result: "Accepted",
        inspection_qty: 20,
      },
      {
        delivery_note_item: "DN2",
        stock_uom: "Kg",
        received_stock_qty: 5,
        result: "Rejected",
        inspection_qty: 5,
      },
    ],
  });
  const before = JSON.stringify(frm.doc);
  context.render_inspection_uom_summary(frm);
  assert.equal(frm.visible.uom_summary, true);
  assert.equal(frm.visible.total_received_stock_qty, false);
  assert.match(frm.rendered.uom_summary, /Pcs/);
  assert.match(frm.rendered.uom_summary, /Kg/);
  assert.equal(JSON.stringify(frm.doc), before);
});

test("single-UOM labels retain standard numeric fields and clear stale mixed summaries", () => {
  const { context, form } = fixture();
  const frm = form({
    results: [
      { delivery_note_item: "DN", stock_uom: "Pcs", received_stock_qty: 4 },
    ],
  });
  context.render_inspection_uom_summary(frm);
  assert.equal(frm.visible.uom_summary, false);
  assert.equal(frm.visible.total_received_stock_qty, true);
  assert.equal(frm.labels.total_received_stock_qty.value, "Received (Pcs)");
  assert.equal(frm.rendered.uom_summary, "");
});

test("cost breakdown renders saved components safely and preserves the JSON snapshot", () => {
  const { context, form } = fixture();
  const frm = form({
    estimated_cost_currency: "KES",
    cost_breakdown: JSON.stringify([
      {
        component: "<script>bad()</script>",
        basis: "Per Distance",
        quantity: 10,
        uom: "Km",
        rate: 5,
        amount: 50,
      },
    ]),
  });
  const original = frm.doc.cost_breakdown;
  context.render_allocation_cost_breakdown(frm);
  assert.equal(frm.visible.costs_tab, true);
  assert.match(frm.rendered.cost_breakdown_preview, /&lt;script&gt;/);
  assert.doesNotMatch(frm.rendered.cost_breakdown_preview, /<script>/);
  assert.equal(frm.doc.cost_breakdown, original);
});

test("empty or malformed cost snapshots never break the allocation form", () => {
  const { context, form } = fixture();
  for (const value of [null, "not json", {}, []]) {
    const frm = form({ cost_breakdown: value });
    context.render_allocation_cost_breakdown(frm);
    assert.equal(frm.visible.costs_tab, false);
    assert.equal(
      frm.rendered.cost_breakdown_preview,
      "No component estimate is available."
    );
  }
});

test("inspection edits and removals refresh the UOM summary", () => {
  const { handlers } = fixture();
  for (const event of [
    "inspection_qty",
    "stock_uom",
    "received_stock_qty",
    "results_add",
    "results_remove",
  ]) {
    assert.equal(
      typeof handlers["Return Inspection Result"][event],
      "function"
    );
  }
});
