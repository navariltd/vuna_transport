const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function load(relativePath) {
  let handlers;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, relativePath), "utf8"),
    {
      frappe: {
        ui: {
          form: {
            on: (_doctype, events) => {
              handlers = events;
            },
          },
        },
      },
      __: (value) => value,
    }
  );
  return handlers;
}

test("delivery methods use independent Buying, Selling and Internal checkboxes", () => {
  const metadata = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/delivery_method/delivery_method.json"
      ),
      "utf8"
    )
  );
  for (const fieldname of ["buying", "selling", "internal"]) {
    const field = metadata.fields.find((row) => row.fieldname === fieldname);
    assert.equal(field.fieldtype, "Check");
    assert.ok(metadata.field_order.includes(fieldname));
  }
  assert.equal(
    metadata.fields.some((row) => row.fieldname === "applies_to"),
    false
  );
});

test("sales method query uses applicability, not fulfilment labels", () => {
  const events = load("../public/js/sales_order.js");
  let query;
  events.setup({
    set_query: (field, fn) => {
      assert.equal(field, "vuna_delivery_method");
      query = fn();
    },
  });
  assert.equal(query.filters.disabled, 0);
  assert.equal(query.filters.selling, 1);
  assert.equal(query.filters.buying, undefined);
});

test("transport responsibility defaults workflow regardless of method name", async () => {
  const events = load(
    "../vuna_transport/doctype/delivery_method/delivery_method.js"
  );
  for (const [arranger, expected] of [
    ["Company", 1],
    ["Counterparty", 0],
  ]) {
    let values;
    await events.transport_arranged_by({
      doc: { delivery_method_name: "Courier", transport_arranged_by: arranger },
      set_value: async (value) => {
        values = value;
      },
    });
    assert.equal(values.requires_transport_planning, expected);
    assert.equal(values.requires_vehicle_allocation, expected);
    assert.equal(values.requires_delivery_trip, expected);
  }
});

test("purchase pickup address depends on responsibility, not retired fulfilment type", () => {
  const metadata = JSON.parse(
    fs.readFileSync(
      path.join(__dirname, "../vuna_transport/custom/purchase_order.json"),
      "utf8"
    )
  );
  const fields = metadata.custom_fields;
  const responsibility = fields.find(
    (field) => field.fieldname === "vuna_transport_arranged_by"
  );
  const address = fields.find(
    (field) => field.fieldname === "vuna_pickup_address"
  );
  assert.equal(
    responsibility.fetch_from,
    "vuna_delivery_method.transport_arranged_by"
  );
  assert.equal(
    address.mandatory_depends_on,
    "eval:doc.vuna_transport_arranged_by == 'Company'"
  );
  assert.equal(
    fields.some((field) => field.fieldname === "vuna_delivery_fulfilment_type"),
    false
  );
});
