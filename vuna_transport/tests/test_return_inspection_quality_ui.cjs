const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function form(setting = 1) {
  const handlers = {};
  const frappe = {
    ui: {
      form: {
        on: (doctype, events, handler) => {
          handlers[doctype] ||= {};
          if (typeof events === "string") handlers[doctype][events] = handler;
          else Object.assign(handlers[doctype], events);
        },
      },
    },
    db: { get_single_value: async () => setting },
    get_doc: () => ({ item_code: "Milk", delivery_note_item: "DNI-1" }),
  };
  const context = {
    frappe,
    cint: Number,
    __: (value, args = []) =>
      value.replace(/\{(\d+)\}/g, (_, index) => args[index]),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/return_inspection/return_inspection.js"
      ),
      "utf8"
    ),
    context
  );
  return {
    handlers: handlers["Return Inspection"],
    frappe,
    create: context.create_return_quality_inspections,
  };
}

function creationForm() {
  const scope = form();
  const messages = [];
  const calls = [];
  const buttonStates = [];
  let dialog;
  scope.frappe.msgprint = (message) => messages.push(message);
  scope.frappe.show_alert = () => {};
  scope.frappe.call = async (request) => {
    calls.push(request);
    return { message: { created: ["QI-1"] } };
  };
  scope.frappe.ui.Dialog = class {
    constructor(options) {
      this.options = options;
      this.fields_dict = {
        results: {
          grid: {
            get_selected_children: () =>
              options.fields[0].data.filter((row) => row.__checked),
          },
        },
      };
      dialog = this;
    }
    get_primary_btn() {
      return { prop: (name, value) => buttonStates.push(value) };
    }
    show() {
      this.shown = true;
    }
    hide() {
      this.hidden = true;
    }
  };
  const frm = {
    doc: {
      name: "RIN-1",
      modified: "2026-11-15 16:00:00",
      results: [
        {
          name: "GROUP-1",
          item_code: "Milk",
          result: "Accepted",
          inspection_qty: 5,
        },
        {
          name: "GROUP-2",
          item_code: "Milk",
          result: "Rejected",
          inspection_qty: 3,
        },
        { name: "GROUP-3", item_code: "Cream", quality_inspection: "EXISTING" },
      ],
    },
    is_dirty: () => false,
    fields_dict: { results: { grid: { get_selected_children: () => [] } } },
    reload_doc: async () => {
      frm.reloaded = true;
    },
  };
  return { ...scope, frm, messages, calls, buttonStates, dialog: () => dialog };
}

test("Create opens a native row-selection dialog without creating QIs immediately", async () => {
  const state = creationForm();
  state.create(state.frm);
  assert.equal(state.calls.length, 0);
  const dialog = state.dialog();
  assert.equal(dialog.options.title, "Create Quality Inspections");
  assert.equal(dialog.options.fields[0].data.length, 2);
  assert.ok(dialog.options.fields[0].data.every((row) => row.__checked));
  dialog.options.fields[0].data[0].__checked = false;
  await dialog.options.primary_action();
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].args.result_rows.length, 1);
  assert.equal(state.calls[0].args.result_rows[0], "GROUP-2");
  assert.equal(state.calls[0].args.modified, state.frm.doc.modified);
  assert.ok(state.frm.reloaded);
  assert.ok(dialog.hidden);
  assert.deepEqual(state.buttonStates, [true, false]);
});

test("Create respects selected form rows and skips rows with existing QIs", () => {
  const state = creationForm();
  state.frm.fields_dict.results.grid.get_selected_children = () => [
    { name: "GROUP-2" },
  ];
  state.create(state.frm);
  const data = state.dialog().options.fields[0].data;
  assert.equal(Boolean(data[0].__checked), false);
  assert.equal(Boolean(data[1].__checked), true);
});

test("unsaved changes and empty selection never make creation requests", async () => {
  const state = creationForm();
  state.frm.is_dirty = () => true;
  state.create(state.frm);
  assert.match(state.messages[0], /Save the Return Inspection/);
  assert.equal(state.dialog(), undefined);
  state.frm.is_dirty = () => false;
  state.create(state.frm);
  const dialog = state.dialog();
  dialog.options.fields[0].data.forEach((row) => {
    row.__checked = false;
  });
  await dialog.options.primary_action();
  assert.match(state.messages.at(-1), /Select at least one/);
  assert.equal(state.calls.length, 0);
});

test("a changed document invalidates an already-open creation dialog", async () => {
  const state = creationForm();
  state.create(state.frm);
  state.frm.doc.modified = "2026-11-15 17:00:00";
  await state.dialog().options.primary_action();
  assert.equal(state.calls.length, 0);
  assert.match(state.messages[0], /has changed/);
});

test("new Return Inspections default the checkbox from settings only once", async () => {
  const { handlers } = form();
  const values = [];
  const frm = {
    doc: { inspection_required: 0 },
    is_new: () => true,
    set_value: async (...args) => values.push(args),
  };
  await handlers.onload(frm);
  await handlers.onload(frm);
  assert.equal(values.length, 1);
  assert.deepEqual(values[0], ["inspection_required", 1]);
  frm.doc = { inspection_required: 0 };
  await handlers.onload(frm);
  assert.equal(values.length, 2);
});

test("settings never override existing saved Return Inspections", async () => {
  const { handlers } = form();
  await handlers.onload({
    doc: { inspection_required: 0 },
    is_new: () => false,
    set_value: () => assert.fail("Saved checkbox was changed"),
  });
});

test("late settings responses do not overwrite a user checkbox change", async () => {
  const { handlers, frappe } = form(0);
  let resolve;
  frappe.db.get_single_value = () =>
    new Promise((done) => {
      resolve = done;
    });
  const frm = {
    doc: { inspection_required: 0 },
    is_new: () => true,
    set_value: () => assert.fail("User choice was overwritten"),
  };
  const loading = handlers.onload(frm);
  frm.doc.inspection_required = 1;
  resolve(0);
  await loading;
});

test("QI link filters allow both draft and submitted evidence for the exact received row", () => {
  const { handlers } = form();
  let query;
  handlers.setup({
    doc: { sales_return: "RETURN-1" },
    set_query: (field, ...args) => {
      if (field === "quality_inspection") query = args.at(-1);
    },
  });
  const filters = query(
    { sales_return: "RETURN-1" },
    "Return Inspection Result",
    "GROUP-1"
  ).filters;
  assert.equal(filters.docstatus[0], "<");
  assert.equal(filters.docstatus[1], 2);
  assert.equal(filters.reference_name, "RETURN-1");
  assert.equal(filters.child_row_reference, "DNI-1");
});

test("Inspection Required precedes results and every row exposes its QI within the grid width", () => {
  const load = (name) =>
    JSON.parse(
      fs.readFileSync(
        path.join(__dirname, `../vuna_transport/doctype/${name}/${name}.json`),
        "utf8"
      )
    );
  const parent = load("return_inspection");
  const child = load("return_inspection_result");
  const request = load("return_request");
  assert.ok(!request.field_order.includes("inspection_required"));
  assert.ok(
    !request.fields.some((row) => row.fieldname === "inspection_required")
  );
  assert.ok(
    parent.field_order.indexOf("inspection_required") <
      parent.field_order.indexOf("results")
  );
  const field = parent.fields.find(
    (row) => row.fieldname === "inspection_required"
  );
  assert.equal(field.fieldtype, "Check");
  assert.equal(field.allow_on_submit, undefined);
  assert.equal(
    child.fields.find((row) => row.fieldname === "quality_inspection")
      .in_list_view,
    1
  );
  assert.equal(
    child.fields
      .filter((row) => row.in_list_view)
      .reduce((total, row) => total + row.columns, 0),
    10
  );
  assert.equal(parent.creation, "2026-10-28 05:37:24.584050");
  assert.equal(child.creation, "2026-10-28 08:04:55.941221");
});
