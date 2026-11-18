const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
  const state = { calls: [], messages: [], html: "" };
  const transport = { road_routing: {} };
  const frappe = {
    provide() {},
    msgprint: (message) => state.messages.push(message),
    show_alert: (message) => {
      state.alert = message;
    },
    utils: {
      escape_html: (value) =>
        String(value)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;"),
    },
    format: (value) =>
      `<div style='text-align: right'>${Number(value).toFixed(2)}</div>`,
    call: async (options) => {
      state.calls.push(options);
      return (
        state.response || {
          message: {
            distance_meters: 1200,
            duration_seconds: 3600,
            legs: [],
            warnings: [],
          },
        }
      );
    },
    ui: {
      Dialog: class {
        constructor(options) {
          state.options = options;
          state.dialog = this;
          this.values = { return_to_start: 0 };
          this.fields_dict = {
            result: {
              $wrapper: {
                html: (value) => {
                  state.html = value;
                },
                empty: () => {
                  state.html = "";
                },
              },
            },
          };
        }
        get_primary_btn() {
          return {
            prop: (key, value) => {
              state.disabled = value;
            },
          };
        }
        get_secondary_btn() {
          return {
            hide: () => {
              state.apply_visible = false;
            },
            show: () => {
              state.apply_visible = true;
            },
          };
        }
        hide() {
          state.options.onhide();
        }
        get_value(key) {
          return this.values[key];
        }
        show() {
          state.shown = true;
        }
      },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(__dirname, "../public/js/road_routing.js"),
      "utf8"
    ),
    {
      frappe,
      vuna_transport: transport,
      format_number: (value, format, precision) =>
        Number(value).toFixed(precision),
      __: (value, args = []) =>
        value.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    }
  );
  const frm = {
    doc: {
      doctype: "Transport Load",
      name: "TL-1",
      docstatus: 0,
      movement_type: "Customer Delivery",
      modified: "2026-11-18 12:00:00.000000",
    },
    is_new: () => false,
    is_dirty: () => Boolean(state.dirty),
    refresh_field: (field) => {
      state.refreshed = field;
    },
    dirty: () => {
      state.dirty = true;
    },
    set_value: (field, value) => {
      frm.doc[field] = value;
    },
    clear_table: (field) => {
      frm.doc[field] = [];
    },
    add_child: (field, row) => {
      frm.doc[field].push(row);
    },
  };
  return { state, frappe, frm, routing: transport.road_routing };
}

test("opening a preview makes no billable call; explicit action sends document identity only", async () => {
  const { state, frm, routing } = fixture();
  routing.show_preview(frm);
  assert.equal(state.calls.length, 0);
  assert.doesNotMatch(
    JSON.stringify(state.options.fields),
    /may incur charges|billing|Google Cloud/
  );
  assert.equal(
    state.options.fields.some((field) => field.fieldname === "start_address"),
    false
  );
  await state.options.primary_action(state.dialog.values);
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].args.name, "TL-1");
  assert.equal(state.calls[0].args.doctype, "Transport Load");
  assert.equal("start_address" in state.calls[0].args, false);
  assert.match(state.html, /1.20 km/);
  assert.match(state.html, /1.00 driving hours/);
  assert.match(state.html, /Google Maps/);
  assert.equal(state.disabled, false);
  state.options.onhide();
  assert.equal(state.html, "");
});

test("allocation application sends a signed distance receipt, not browser-calculated costs", () => {
  const { frm, routing } = fixture();
  frm.doc.doctype = "Vehicle Allocation";
  const result = {
    allocation_route_rows: [
      { stop_key: "own-key", address: "A", source_rows: '["load-row"]' },
    ],
    route_fingerprint: "manifest",
    source_warehouse: "Main",
    return_to_start: 1,
    route_preview_token: "signed-receipt",
    distance_meters: 150000,
    duration_seconds: 2000,
    cost_estimate: { marginal_cost: 300 },
  };
  assert.equal(routing.apply_stop_order(frm, result), true);
  assert.equal(frm.doc.route_stops[0].stop_key, "own-key");
  assert.equal(frm.doc.route_status, "Proposed");
  assert.equal(frm.doc.route_return_to_start, 1);
  assert.equal(frm.doc.route_preview_token, "signed-receipt");
  assert.equal(frm.doc.route_planned_distance, undefined);
  assert.equal(frm.doc.estimated_distance, undefined);
  assert.equal(frm.doc.estimated_marginal_cost, undefined);
});

test("trip application preserves shared-address stops and rejects incomplete permutations", () => {
  const { frm, routing } = fixture();
  frm.doc.doctype = "Delivery Trip";
  frm.doc.delivery_stops = [
    { name: "s1", address: "Shared", customer: "C1" },
    { name: "s2", address: "Shared", customer: "C2" },
  ];
  assert.equal(
    routing.apply_stop_order(frm, { trip_stop_order: ["s2"] }),
    false
  );
  assert.equal(
    routing.apply_stop_order(frm, {
      trip_stop_order: ["s2", "s1"],
      return_to_start: 1,
    }),
    true
  );
  assert.equal(frm.doc.delivery_stops[0].customer, "C2");
  assert.equal(frm.doc.delivery_stops[1].idx, 2);
  assert.equal(frm.doc.vuna_route_return_to_start, 1);
});

test("routing settings keep the administrator billing and data-sharing notice", () => {
  const settings = JSON.parse(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/transport_settings/transport_settings.json"
      ),
      "utf8"
    )
  );
  const provider = settings.fields.find(
    (field) => field.fieldname === "routing_provider"
  );
  assert.match(provider.description, /incur request charges/);
  assert.match(provider.description, /coordinates to Google/);
  assert.match(provider.description, /Google Cloud quotas/);
  const credential = settings.fields.find(
    (field) => field.fieldname === "google_optimization_service_account"
  );
  assert.equal(credential.fieldtype, "Password");
  assert.equal(
    settings.fields.find(
      (field) => field.fieldname === "enable_google_optimization"
    ).default,
    "0"
  );
});

test("optimizer controls follow administrator enablement and support draft trips", () => {
  const { frm, state, routing } = fixture();
  routing.show_preview(frm, { can_optimize: false });
  assert.equal(
    state.options.fields.find((field) => field.fieldname === "optimize_stops")
      .hidden,
    true
  );
  routing.show_preview(frm, { can_optimize: true });
  assert.equal(
    state.options.fields.find((field) => field.fieldname === "optimize_stops")
      .hidden,
    false
  );
  frm.doc.doctype = "Delivery Trip";
  routing.show_preview(frm, { can_optimize: true });
  assert.equal(
    state.options.fields.find((field) => field.fieldname === "optimize_stops")
      .hidden,
    false
  );
});

test("route numbers use plain formatting rather than escaped Frappe field HTML", () => {
  const { routing } = fixture();
  const html = routing.render_result({
    distance_meters: 225200,
    duration_seconds: 16180,
    legs: [
      {
        from_label: "Origin",
        to_label: "Destination",
        distance_meters: 225200,
        duration_seconds: 16180,
      },
    ],
    warnings: [],
  });
  assert.match(html, /225.20 km · 4.49 driving hours/);
  assert.match(html, /269.67 min/);
  assert.doesNotMatch(html, /text-align|&lt;div|&#39;/);
});

test("unsaved changes prevent preview", () => {
  const { state, frm, routing } = fixture();
  state.dirty = true;
  routing.show_preview(frm);
  assert.equal(state.messages.length, 1);
  assert.equal(state.shown, undefined);
});

test("duplicate clicks cannot send concurrent requests and changed inputs invalidate response", async () => {
  const { state, frm, routing, frappe } = fixture();
  let resolve;
  frappe.call = (options) => {
    state.calls.push(options);
    return new Promise((done) => {
      resolve = done;
    });
  };
  routing.show_preview(frm);
  const pending = state.options.primary_action({ ...state.dialog.values });
  await state.options.primary_action({ ...state.dialog.values });
  assert.equal(state.calls.length, 1);
  state.dialog.values.return_to_start = 1;
  resolve({ message: { legs: [] } });
  await pending;
  assert.match(state.html, /route inputs changed/);
  assert.doesNotMatch(state.html, /Google Maps/);
});

test("master labels and warnings are escaped, and provider errors are not rendered", async () => {
  const { state, frm, routing, frappe } = fixture();
  const html = routing.render_result({
    distance_meters: 1000,
    duration_seconds: 60,
    legs: [
      {
        from_label: "<script>bad</script>",
        to_label: "Depot",
        distance_meters: 1000,
        duration_seconds: 60,
      },
    ],
    warnings: ["<img>"],
  });
  assert.doesNotMatch(html, /<script>|<img>/);
  assert.match(html, /&lt;script&gt;/);
  frappe.call = async () => {
    throw new Error("secret");
  };
  routing.show_preview(frm);
  await state.options.primary_action(state.dialog.values);
  assert.doesNotMatch(state.html, /secret/);
  assert.match(state.html, /No route preview/);
});

test("applying an optimized order preserves rows, quantities and names and leaves saving to the user", () => {
  const { frm, state, routing } = fixture();
  frm.doc.delivery_orders = [
    { name: "A", delivery_address: "First", allocated_quantity: 5 },
    { name: "B", delivery_address: "Second", allocated_quantity: 7 },
    { name: "C", delivery_address: "First", allocated_quantity: 9 },
  ];
  const original = frm.doc.delivery_orders[1];
  assert.equal(
    routing.apply_stop_order(frm, { load_row_order: ["B", "A", "C"] }),
    true
  );
  assert.equal(frm.doc.delivery_orders[0], original);
  assert.deepEqual(
    frm.doc.delivery_orders.map((row) => row.allocated_quantity),
    [7, 5, 9]
  );
  assert.deepEqual(
    frm.doc.delivery_orders.map((row) => row.stop_sequence),
    [1, 2, 2]
  );
  assert.equal(state.dirty, true);
  assert.equal(state.refreshed, "delivery_orders");
});

test("invalid permutations and submitted loads cannot apply a suggested order", () => {
  const { frm, routing } = fixture();
  frm.doc.delivery_orders = [{ name: "A" }, { name: "B" }];
  for (const order of [["A"], ["A", "A"], ["A", "Other"]]) {
    assert.equal(
      routing.apply_stop_order(frm, { load_row_order: order }),
      false
    );
  }
  frm.doc.docstatus = 1;
  assert.equal(
    routing.apply_stop_order(frm, { load_row_order: ["B", "A"] }),
    false
  );
});

test("customers at the same address remain separate deliveries after reordering", () => {
  const { frm, routing } = fixture();
  frm.doc.delivery_orders = [
    {
      name: "A",
      customer: "BKN",
      delivery_address: "Utawala",
      allocated_quantity: 5,
    },
    {
      name: "B",
      customer: "BKN",
      delivery_address: "Kisumu",
      allocated_quantity: 7,
    },
    {
      name: "C",
      customer: "Wephukulu",
      delivery_address: "Kisumu",
      allocated_quantity: 9,
    },
  ];
  assert.equal(
    routing.apply_stop_order(frm, { load_row_order: ["B", "C", "A"] }),
    true
  );
  assert.deepEqual(
    frm.doc.delivery_orders.map((row) => row.stop_sequence),
    [1, 2, 3]
  );
  assert.deepEqual(
    frm.doc.delivery_orders.map((row) => row.customer),
    ["BKN", "Wephukulu", "BKN"]
  );
  assert.deepEqual(
    frm.doc.delivery_orders.map((row) => row.allocated_quantity),
    [7, 9, 5]
  );
});

test("optimization is reviewed before applying and stale suggestions are rejected", async () => {
  const { frm, state, routing } = fixture();
  frm.doc.delivery_orders = [
    { name: "A", delivery_address: "First" },
    { name: "B", delivery_address: "Second" },
  ];
  state.response = {
    message: {
      distance_meters: 1000,
      duration_seconds: 60,
      legs: [],
      source_modified: frm.doc.modified,
      load_row_order: ["B", "A"],
    },
  };
  routing.show_preview(frm);
  state.dialog.values.optimize_stops = 1;
  await state.options.primary_action({ ...state.dialog.values });
  assert.equal(state.apply_visible, true);
  assert.equal(frm.doc.delivery_orders[0].name, "A");
  frm.doc.modified = "changed";
  state.options.secondary_action();
  assert.equal(frm.doc.delivery_orders[0].name, "A");
  assert.match(state.messages.at(-1), /document changed/);
  state.options.fields
    .find((field) => field.fieldname === "optimize_stops")
    .onchange();
  assert.equal(state.apply_visible, false);
});
