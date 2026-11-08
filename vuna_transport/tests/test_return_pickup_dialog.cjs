const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fixture(call, beforeWrite = async () => {}) {
  const context = {
    frappe: { ui: { form: { on() {} } }, call },
    __: (message) => message,
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
  const values = { delivery_trip: "MAT-DT-2026-00031", delivery_stop: "" };
  const dialog = {
    disabled: false,
    options: [],
    description: "",
    get_value: (key) => values[key],
    async set_value(key, value) {
      await beforeWrite(value);
      values[key] = value;
      context.update_pickup_assignment_action(dialog);
    },
    set_df_property(_field, property, value) {
      this[property] = value;
    },
    disable_primary_action() {
      this.disabled = true;
    },
    enable_primary_action() {
      this.disabled = false;
    },
  };
  return {
    dialog,
    values,
    load: () => context.load_pickup_assignment_stops(dialog, "RR-2026-00014"),
  };
}

test("single matching stop waits for async reset and selection before allowing assignment", async () => {
  const reset = deferred();
  const selected = deferred();
  let called = false;
  const setup = fixture(
    async () => {
      called = true;
      return {
        message: [
          { value: "v1mbm3mtkq", label: "2. Ruiru Depot Warehouse - MCD" },
        ],
      };
    },
    (value) => (value ? selected.promise : reset.promise)
  );
  const loading = setup.load();
  assert.equal(setup.dialog.disabled, true);
  await Promise.resolve();
  assert.equal(called, false);
  reset.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(setup.dialog.disabled, true);
  selected.resolve();
  await loading;
  assert.equal(setup.values.delivery_stop, "v1mbm3mtkq");
  assert.equal(setup.dialog.disabled, false);
});

test("multiple matches keep an explicit blank until the user selects a valid stop", async () => {
  const setup = fixture(async () => ({
    message: [
      { value: "S1", label: "Depot 1" },
      { value: "S2", label: "Depot 2" },
    ],
  }));
  await setup.load();
  assert.equal(setup.dialog.options[0], "");
  assert.equal(setup.dialog.disabled, true);
  await setup.dialog.set_value("delivery_stop", "S2");
  assert.equal(setup.dialog.disabled, false);
});

test("a late response from a previously selected trip cannot overwrite the current stop", async () => {
  const first = deferred();
  const setup = fixture(({ args }) =>
    args.delivery_trip === "MAT-DT-2026-00031"
      ? first.promise
      : Promise.resolve({
          message: [{ value: "NEW-STOP", label: "New depot" }],
        })
  );
  const old = setup.load();
  await new Promise((resolve) => setImmediate(resolve));
  setup.values.delivery_trip = "OTHER-TRIP";
  await setup.load();
  first.resolve({ message: [{ value: "OLD-STOP", label: "Old depot" }] });
  await old;
  assert.equal(setup.values.delivery_stop, "NEW-STOP");
  assert.equal(setup.dialog.disabled, false);
});

test("no matching stops or a failed lookup cannot enable assignment", async () => {
  for (const call of [
    async () => ({ message: [] }),
    async () => {
      throw new Error("Offline");
    },
  ]) {
    const setup = fixture(call);
    await setup.load();
    assert.equal(setup.values.delivery_stop, "");
    assert.equal(setup.dialog.disabled, true);
    assert.ok(setup.dialog.description.length > 0);
  }
});
