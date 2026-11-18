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
    doc: { doctype: "Transport Load", name: "TL-1" },
    is_new: () => false,
    is_dirty: () => Boolean(state.dirty),
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
  assert.match(provider.description, /may incur charges/);
  assert.match(provider.description, /stop coordinates to Google/);
  assert.match(provider.description, /billing and request quotas/);
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
