const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function handlers() {
  const events = {};
  let stop;
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/delivery_trip.js"),
      "utf8"
    ),
    {
      frappe: {
        ui: {
          form: {
            on: (doctype, value) => {
              events[doctype] = value;
            },
          },
        },
        get_doc: () => stop,
      },
      __: (message) => message,
    }
  );
  return {
    events,
    select: (value) => {
      stop = value;
    },
  };
}

test("depot stop customer is disabled; mixed and ordinary customer stops remain editable", () => {
  const { events, select } = handlers();
  const changes = [];
  const frm = {
    doc: { vuna_task_based: 1 },
    fields_dict: {
      delivery_stops: {
        grid: {
          grid_rows_by_docname: {
            ROW: { toggle_editable: (...args) => changes.push(args) },
          },
        },
      },
    },
  };
  for (const [stop, editable] of [
    [{ vuna_stop_type: "Depot" }, false],
    [{ vuna_stop_type: "Mixed", delivery_note: "DN-1" }, true],
    [{ vuna_stop_type: "Customer", delivery_note: "DN-1" }, true],
    [{ vuna_task_keys: '["transfer"]' }, false],
  ]) {
    select(stop);
    events["Delivery Stop"].form_render(frm, "Delivery Stop", "ROW");
    assert.deepEqual(changes.pop(), ["customer", editable]);
  }
});

test("pure depot trip hides customer column, mixed trip retains it", () => {
  const { events } = handlers();
  const changes = [];
  const frm = {
    doc: {
      docstatus: 2,
      vuna_task_based: 1,
      delivery_stops: [{ vuna_stop_type: "Depot" }],
    },
    fields_dict: {
      delivery_stops: {
        grid: { update_docfield_property: (...args) => changes.push(args) },
      },
    },
    toggle_display() {},
    remove_custom_button() {},
    page: { set_inner_btn_group_as_primary() {} },
  };
  events["Delivery Trip"].refresh(frm);
  assert.ok(
    changes.some(
      ([field, property, value]) =>
        field === "customer" && property === "hidden" && value
    )
  );
  changes.length = 0;
  frm.doc.delivery_stops.push({
    vuna_stop_type: "Mixed",
    delivery_note: "DN-1",
  });
  events["Delivery Trip"].refresh(frm);
  assert.ok(
    changes.some(
      ([field, property, value]) =>
        field === "customer" && property === "hidden" && !value
    )
  );
});
