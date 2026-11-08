const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture({
  role = "Transport Manager",
  isNew = false,
  dirty = false,
} = {}) {
  const buttons = [],
    calls = [],
    messages = [],
    alerts = [];
  let events,
    prompt,
    reloads = 0;
  const frappe = {
    ui: {
      form: {
        on: (_doctype, handlers) => {
          events = handlers;
        },
      },
    },
    user: { has_role: (name) => role === name },
    prompt: (fields, action, title, primary) => {
      prompt = { fields, action, title, primary };
    },
    call: async (options) => {
      calls.push(options);
      return { message: { device: "PHONE" } };
    },
    msgprint: (value) => messages.push(value),
    show_alert: (value) => alerts.push(value),
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/doctype/transport_mobile_device/transport_mobile_device.js"
      ),
      "utf8"
    ),
    { frappe, __: (value) => value }
  );
  const frm = {
    doc: { name: "PHONE", user: "old@example.test", status: "Active" },
    is_new: () => isNew,
    is_dirty: () => dirty,
    add_custom_button: (label, action) => buttons.push({ label, action }),
    reload_doc: async () => {
      reloads += 1;
    },
  };
  events.refresh(frm);
  return {
    frm,
    buttons,
    calls,
    messages,
    alerts,
    get prompt() {
      return prompt;
    },
    get reloads() {
      return reloads;
    },
  };
}

test("only saved devices and device administrators offer reassignment", () => {
  for (const role of ["Transport Manager", "System Manager"]) {
    assert.ok(
      fixture({ role }).buttons.some(
        (button) => button.label === "Reassign Device"
      )
    );
  }
  assert.equal(fixture({ role: "Driver" }).buttons.length, 0);
  assert.equal(fixture({ isNew: true }).buttons.length, 0);
});

test("reassignment requires a new user, reason and explicit local data clearance", () => {
  const state = fixture();
  state.buttons.find((button) => button.label === "Reassign Device").action();
  const fields = Object.fromEntries(
    state.prompt.fields.map((field) => [field.fieldname, field])
  );
  for (const name of ["new_user", "reason", "local_data_cleared"])
    assert.equal(fields[name].reqd, 1);
  assert.equal(fields.local_data_cleared.default, 0);
  assert.equal(fields.new_user.get_query().filters.enabled, 1);
  assert.ok(
    fields.new_user.get_query().filters.name[1].includes("old@example.test")
  );
  assert.ok(fields.new_user.get_query().filters.name[1].includes("Guest"));
  assert.match(fields.notice.options, /cannot remotely erase/);
  assert.match(fields.notice.options, /other active device will be revoked/);
});

test("an unchecked clearance acknowledgement does not call the server", async () => {
  const state = fixture();
  state.buttons.find((button) => button.label === "Reassign Device").action();
  await state.prompt.action({
    new_user: "new@example.test",
    reason: "Handover",
    local_data_cleared: 0,
  });
  assert.equal(state.calls.length, 0);
  assert.equal(state.messages.length, 1);
});

test("the action sends the original owner and refreshes only after server success", async () => {
  const state = fixture();
  state.buttons.find((button) => button.label === "Reassign Device").action();
  state.frm.doc.user = "changed@example.test";
  await state.prompt.action({
    new_user: "new@example.test",
    reason: "Handover",
    local_data_cleared: 1,
  });
  assert.equal(state.calls[0].args.device_name, "PHONE");
  assert.equal(state.calls[0].args.expected_user, "old@example.test");
  assert.equal(state.calls[0].args.new_user, "new@example.test");
  assert.match(state.calls[0].method, /\.reassign_mobile_device$/);
  assert.equal(state.reloads, 1);
  assert.equal(state.alerts[0].indicator, "green");
});

test("unsaved device edits must be saved before reassignment", () => {
  const state = fixture({ dirty: true });
  state.buttons.find((button) => button.label === "Reassign Device").action();
  assert.equal(state.prompt, undefined);
  assert.match(state.messages[0], /Save the device/);
});
