const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture(file, doc = {}, companyTransport = true) {
  let handlers;
  const queries = {},
    buttons = [],
    calls = [];
  const frappe = {
    ui: {
      form: {
        on: (doctype, events) => {
          if (["Return Request", "Material Request"].includes(doctype))
            handlers = events;
        },
      },
    },
    call: async (options) => {
      calls.push(options);
      return {
        message: { enabled: true, company_transport: companyTransport },
      };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, file), "utf8"), {
    frappe,
    __: (value) => value,
  });
  const frm = {
    doc,
    set_query: (field, query) => {
      queries[field] = query;
    },
    add_custom_button: (label, action) => buttons.push({ label, action }),
  };
  handlers.setup(frm);
  return { handlers, frm, queries, buttons, calls };
}

test("Material Transfer methods are filtered by Internal, not method names", () => {
  const state = fixture("../public/js/material_request_transport.js");
  const filters = state.queries.vuna_delivery_method().filters;
  assert.equal(filters.disabled, 0);
  assert.equal(filters.internal, 1);
  assert.equal(filters.selling, undefined);
});

test("return method query follows customer versus depot context", () => {
  const state = fixture(
    "../vuna_transport/doctype/return_request/return_request.js",
    { return_context: "Customer" }
  );
  assert.equal(state.queries.delivery_method().filters.selling, 1);
  state.frm.doc.return_context = "Depot";
  assert.equal(state.queries.delivery_method().filters.internal, 1);
  assert.equal(state.queries.delivery_method().filters.selling, undefined);
});

test("counterparty-arranged transfers do not offer company transport demand", async () => {
  for (const companyTransport of [true, false]) {
    const state = fixture(
      "../public/js/material_request_transport.js",
      {
        name: "MR",
        docstatus: 1,
        material_request_type: "Material Transfer",
        vuna_delivery_method: "Method",
      },
      companyTransport
    );
    await state.handlers.refresh(state.frm);
    assert.equal(state.calls[0].args.delivery_method, "Method");
    assert.equal(state.buttons.length, companyTransport ? 1 : 0);
  }
});

test("other Material Request types keep their existing behaviour", async () => {
  const state = fixture("../public/js/material_request_transport.js", {
    docstatus: 1,
    material_request_type: "Purchase",
  });
  await state.handlers.refresh(state.frm);
  assert.equal(state.calls.length, 0);
  assert.equal(state.buttons.length, 0);
});

test("Return Request uses the shared Link and keeps custody separate", () => {
  const metadata = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/return_request/return_request.json"
      ),
      "utf8"
    )
  );
  const field = metadata.fields.find(
    (row) => row.fieldname === "delivery_method"
  );
  assert.equal(field.fieldtype, "Link");
  assert.equal(field.options, "Delivery Method");
  assert.ok(metadata.field_order.includes("delivery_method"));
  assert.equal(
    metadata.fields.some((row) => row.fieldname === "return_method"),
    false
  );
});

test("warehouse users can select delivery methods without creating or editing masters", () => {
  const metadata = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/delivery_method/delivery_method.json"
      ),
      "utf8"
    )
  );
  for (const role of ["Stock User", "Stock Manager"]) {
    const permission = metadata.permissions.find((row) => row.role === role);
    assert.equal(permission.read, 1);
    assert.equal(permission.select, 1);
    assert.ok(!permission.create && !permission.write);
  }
});
