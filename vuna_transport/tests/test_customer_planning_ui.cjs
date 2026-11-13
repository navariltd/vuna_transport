const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function controller(native = true) {
  const requests = [];
  const layouts = [];
  const controls = [];
  const events = { document: new Map(), window: new Map() };
  const eventTarget = (listeners) => ({
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name) => listeners.delete(name),
  });
  const vuna_transport = { DispatchPlanning: {} };
  const frappe = {
    provide() {},
    pages: { "dispatch-planning": {} },
    router: { slug: (value) => value.toLowerCase().replaceAll(" ", "-") },
    datetime: { str_to_user: (value) => value || "" },
    defaults: { get_default: () => "Example Company" },
    ui: {
      form: {
        make_control(options) {
          const attributes = {};
          const field = {
            $input: { attr: (values) => Object.assign(attributes, values) },
            set_value(value) {
              field.value = value;
            },
            get_value() {
              return field.value || "";
            },
          };
          controls.push({ options, attributes });
          return field;
        },
      },
    },
    call: async (request) => {
      requests.push(request);
      return { message: {} };
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      path.join(
        __dirname,
        "../vuna_transport/page/dispatch_planning/dispatch_planning.js"
      ),
      "utf8"
    ),
    {
      frappe,
      vuna_transport,
      cint: Number,
      clearTimeout,
      document: eventTarget(events.document),
      window: eventTarget(events.window),
      flt: (value) => Number(value) || 0,
      __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, i) => args[i]),
      $: (element) =>
        typeof element === "string"
          ? {
              appendTo() {
                layouts.push(element);
                return this;
              },
            }
          : element.mock || { data: () => 0 },
    }
  );
  const result = Object.create(
    vuna_transport.DispatchPlanning.Controller.prototype
  );
  result.customer_context = native
    ? {
        doctype: "Transport Demand",
        order_field: "transport_demand",
        item_field: "transport_demand_item",
      }
    : {
        doctype: "Delivery Order",
        order_field: "delivery_order",
        item_field: "delivery_order_item",
      };
  result.filters = {
    source_warehouse: { get_value: () => "Loading warehouse" },
  };
  result.escape = (value) =>
    String(value ?? "")
      .replaceAll("<", "&lt;")
      .replaceAll('"', "&quot;");
  result.selector = (value) => value;
  result.number = (value) => String(value);
  return { result, requests, layouts, controls, frappe, events };
}

test("each planning tab owns its demands, quantities and cached details", () => {
  const { result } = controller();
  result.orders = [{ name: "CUSTOMER" }];
  result.allocations.set("CUSTOMER-ITEM", { allocated_quantity: 2 });
  result.active_planning_tab = "depots";
  assert.equal(result.orders.length, 0);
  assert.equal(result.allocations.size, 0);
  result.orders = [{ name: "DEPOT" }];
  result.active_planning_tab = "suppliers";
  assert.equal(result.orders.length, 0);
  result.active_planning_tab = "customers";
  assert.equal(result.orders[0].name, "CUSTOMER");
  assert.equal(result.allocations.get("CUSTOMER-ITEM").allocated_quantity, 2);
});

test("depot items are grouped by demand and retain warehouse stock and transaction UOM", () => {
  const { result } = controller();
  result.set_movement_items("depots", [
    {
      transport_demand: "TD-1",
      transport_demand_item: "DI-1",
      destination_warehouse: "Meru Depot",
      item_code: "Milk",
      uom: "Carton",
      available_quantity: 4,
      stock_warehouse: "Main",
      stock_available_quantity: 100,
      weight: 8,
    },
    {
      transport_demand: "TD-1",
      transport_demand_item: "DI-2",
      destination_warehouse: "Meru Depot",
      item_code: "Cream",
      uom: "Box",
      available_quantity: 2,
      weight: 3,
    },
  ]);
  result.active_planning_tab = "depots";
  assert.equal(result.orders.length, 1);
  assert.equal(result.orders[0].customer_name, "Meru Depot");
  assert.equal(result.orders[0].weight, 11);
  assert.equal(result.details["TD-1"].items.length, 2);
  assert.equal(result.details["TD-1"].items[0].uom, "Carton");
  assert.equal(result.details["TD-1"].items[0].stock_warehouse, "Main");
});

test("supplier rows use collection locations and destination instead of invented on-hand stock", () => {
  const { result } = controller();
  result.set_movement_items("suppliers", [
    {
      transport_demand: "TD-P",
      transport_demand_item: "PI",
      supplier: "Packaging Supplier",
      source_address: "SUP-001",
      delivery_address_summary: "Industrial Road, Meru",
      destination_warehouse: "Main",
      item_code: "Cartons",
      available_quantity: 3,
      uom: "Box",
    },
  ]);
  result.active_planning_tab = "suppliers";
  let html;
  result.$root = {
    find: () => ({
      html: (value) => {
        html = value;
      },
    }),
  };
  result.update_select_all_state = () => {};
  result.render_pagination = () => {};
  result.render_demand();
  assert.match(html, /Packaging Supplier/);
  assert.match(html, /Collect from:<\/span> Industrial Road, Meru/);
  assert.match(html, /To:<\/span> Main/);
  assert.match(html, /Destination/);
  assert.doesNotMatch(html, /Stock on Hand|Select a warehouse|SUP-001/);
  assert.match(html, /dp-order-toggle/);
  assert.match(html, /dp-allocation-qty/);
});

test("switching tabs keeps the Loading Plan visible and invalidates old previews", () => {
  const { result } = controller();
  const toggled = [];
  const element = {
    prop() {
      return this;
    },
    toggleClass() {
      return this;
    },
    attr() {
      return this;
    },
    toggle(value) {
      toggled.push(value);
      return this;
    },
  };
  const selectors = [];
  result.$root = {
    find(selector) {
      selectors.push(selector);
      return element;
    },
  };
  result.render_demand = () => {};
  result.render_plan = () => {};
  result.schedule_plan_evaluation = () => {};
  result.plan_evaluation_id = 1;
  result.cost_request_id = 1;
  result.plan = { totals: {} };
  result.switch_planning_tab("depots");
  assert.equal(result.plan, null);
  assert.equal(result.plan_evaluation_id, 2);
  assert.equal(result.cost_request_id, 2);
  assert.ok(
    selectors.every((value) => !value.includes(".dp-sidebar > .dp-plan"))
  );
  assert.equal(toggled.at(-1), false);
});

test("supplier preview requests preserve canonical item IDs and ignore the outbound warehouse filter", async () => {
  const { result, requests } = controller();
  result.active_planning_tab = "suppliers";
  await result.call("evaluate_manual_load_plan", {
    allocations: [
      {
        delivery_order: "TD-P",
        delivery_order_item: "PI",
        allocated_quantity: 2,
      },
    ],
  });
  assert.equal(requests[0].args.source_warehouse, null);
  assert.equal(requests[0].args.allocations[0].transport_demand, "TD-P");
  assert.equal(requests[0].args.allocations[0].transport_demand_item, "PI");
});

test("planning filters use report-sized inputs with placeholders and accessible labels", () => {
  const { result, controls } = controller();
  result.$root = { find: () => ({}) };
  result.make_filters();
  assert.equal(controls.length, 8);
  for (const { options, attributes } of controls) {
    assert.equal(options.only_input, true);
    assert.equal(options.df.input_class, "input-xs");
    assert.equal(options.df.placeholder, options.df.label);
    assert.equal(attributes["aria-label"], options.df.label);
    assert.equal(attributes.title, options.df.label);
    assert.equal(typeof options.df.onchange, "function");
  }
});

test("vehicle categories use a standard enabled-only Link without preloading a capped list", () => {
  const { result, controls, requests } = controller();
  result.$root = { find: () => ({}) };
  let changed = 0;
  result.category_changed = () => {
    changed++;
  };
  result.make_vehicle_category_field();
  const { df } = controls[0].options;
  assert.equal(df.fieldtype, "Link");
  assert.equal(df.options, "Vehicle Category");
  assert.equal(df.only_select, 1);
  assert.equal(result.vehicle_category.get_query().filters.disabled, 0);
  assert.equal(result.selected_vehicle_category(), null);
  result.vehicle_category.set_value("CATEGORY-145");
  assert.equal(result.selected_vehicle_category(), "CATEGORY-145");
  df.onchange();
  assert.equal(changed, 1);
  assert.equal(requests.length, 0);
  result.vehicle_category.set_value("");
  assert.equal(result.selected_vehicle_category(), null);
});

test("plan evaluation reads the selected category from the Link control", async () => {
  const { result } = controller();
  result.vehicle_category = { get_value: () => "CATEGORY-145" };
  result.allocations = new Map([
    [
      "DI-1",
      {
        delivery_order: "TD-1",
        delivery_order_item: "DI-1",
        allocated_quantity: 1,
      },
    ],
  ]);
  result.plan_evaluation_id = 0;
  result.$root = { find: () => ({ html() {}, prop() {} }) };
  result.loading = () => "Loading";
  result.render_plan = () => {};
  result.call = async (method, args) => {
    assert.equal(method, "evaluate_manual_load_plan");
    assert.equal(args.vehicle_category, "CATEGORY-145");
    return { recommended_vehicle_category: "CATEGORY-145" };
  };
  await result.evaluate_plan();
  assert.equal(result.plan.recommended_vehicle_category, "CATEGORY-145");
});

test("settings-defined planning filters use the same compact controls", async () => {
  const { result, controls, frappe } = controller();
  result.$root = { find: () => ({}) };
  result.configured_filter_keys = new Set();
  result.configure_location_filter_queries = () => {};
  frappe.call = async () => ({
    message: [
      {
        field_key: "priority",
        fieldtype: "Select",
        label: "Priority",
        options: "Low\nHigh",
      },
    ],
  });
  await result.load_configured_filters();
  assert.equal(controls.length, 1);
  assert.equal(controls[0].options.only_input, true);
  assert.equal(controls[0].attributes.placeholder, "Priority");
  result.make_planning_filter({
    fieldname: "flag",
    fieldtype: "Check",
    label: "Flag",
  });
  assert.equal(controls[1].options.only_input, false);
});

function selectionController() {
  const { result } = controller();
  result.orders = [{ name: "TD-1" }];
  result.details = {
    "TD-1": {
      name: "TD-1",
      items: [
        { name: "DI-1", item_code: "Milk", available_quantity: 5 },
        { name: "DI-2", item_code: "Yoghurt", available_quantity: 3 },
        { name: "DI-3", item_code: "Butter", available_quantity: 0 },
      ],
    },
  };
  result.allocations = new Map();
  result.plan_evaluation_id = 0;
  result.clear_suggestions = () => {};
  result.render_plan = () => {};
  result.schedule_plan_evaluation = () => {};
  const controls = new Map();
  result.$root = {
    find(selector) {
      if (!controls.has(selector)) controls.set(selector, {});
      const control = controls.get(selector);
      return {
        get: () => control,
        val: (value) => {
          control.value = value;
        },
        prop: (key, value) => {
          control[key] = value;
        },
      };
    },
  };
  return { result, controls };
}

test("item selection defaults only the chosen item's remaining quantity and supports deselection", () => {
  const { result, controls } = selectionController();
  const checkbox = {
    checked: true,
    mock: { data: (key) => (key === "item" ? "DI-1" : "TD-1") },
  };
  result.toggle_item({ currentTarget: checkbox });
  assert.equal(result.allocations.size, 1);
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 5);
  assert.equal(controls.get('.dp-allocation-qty[data-item="DI-1"]').value, 5);
  assert.equal(
    controls.get(".dp-demand .dp-select-all-orders").indeterminate,
    true
  );
  assert.equal(
    controls.get('[data-order="TD-1"] .dp-order-select').checked,
    false
  );
  checkbox.checked = false;
  result.toggle_item({ currentTarget: checkbox });
  assert.equal(result.allocations.size, 0);
  assert.equal(
    controls.get(".dp-demand .dp-select-all-orders").indeterminate,
    false
  );
});

test("order and page selection exclude exhausted rows and track partial selection", () => {
  const { result, controls } = selectionController();
  result.allocations.set("DI-1", { allocated_quantity: 2 });
  result.allocations.set("DI-2", { allocated_quantity: 3 });
  result.update_select_all_state();
  assert.equal(result.order_selection_state("TD-1").checked, true);
  assert.equal(controls.get(".dp-demand .dp-select-all-orders").checked, true);
  result.allocations.delete("DI-1");
  result.update_select_all_state();
  assert.equal(
    controls.get(".dp-demand .dp-select-all-orders").indeterminate,
    true
  );
  assert.equal(
    controls.get('[data-order="TD-1"] .dp-order-select').indeterminate,
    true
  );
});

test("editing quantities updates item, order and page selection without replacing inputs", () => {
  const { result, controls } = selectionController();
  const input = {
    mock: {
      data: (key) => (key === "item" ? "DI-1" : "TD-1"),
      val: () => "2",
    },
  };
  result.change_quantity({ currentTarget: input });
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
  assert.equal(controls.get('.dp-item-select[data-item="DI-1"]').checked, true);
  input.mock.val = () => "0";
  result.change_quantity({ currentTarget: input });
  assert.equal(result.allocations.has("DI-1"), false);
  assert.equal(
    controls.get('.dp-item-select[data-item="DI-1"]').checked,
    false
  );
});

test("stock display distinguishes unknown warehouse from zero stock and converts to requested UOM", () => {
  const { result } = controller();
  assert.match(result.render_stock({}), /Select a warehouse/);
  assert.doesNotMatch(result.render_stock({}), /0|insufficient/i);
  const item = {
    stock_warehouse: "Main Store",
    stock_available_quantity: 120,
    stock_uom: "Pcs",
    uom: "Carton",
    conversion_factor: 10,
    stock_availability_status: "Available",
  };
  const html = result.render_stock(item);
  assert.match(html, /12 Carton/);
  assert.doesNotMatch(html, /120 Pcs/);
  assert.match(html, /Main Store/);
  assert.match(html, /<span class="dp-stock-warehouse">Main Store<\/span>/);
  assert.match(
    result.render_stock({
      ...item,
      stock_available_quantity: 0,
      stock_availability_status: "Insufficient",
    }),
    /0 Carton/
  );
  assert.match(html, /class="dp-stock-available"/);
  const shortage = result.render_stock({
    ...item,
    stock_availability_status: "Insufficient",
  });
  assert.match(shortage, /class="dp-stock-insufficient"/);
  assert.match(shortage, /class="sr-only"> — Stock is below remaining demand/);
  assert.doesNotMatch(shortage, /<small|dp-stock-shortage/);
  assert.doesNotMatch(
    result.render_stock({}),
    /dp-stock-available|dp-stock-insufficient/
  );
});

test("expanded item rows use transaction UOM, visible selectors and labelled quantity inputs", () => {
  const { result } = selectionController();
  const html = result.render_item_table({
    name: "TD-1",
    items: [
      {
        name: "DI-1",
        item_code: "Milk",
        transport_quantity: 2,
        available_quantity: 2,
        allocated_quantity: 0,
        uom: "Carton",
        stock_uom: "Pcs",
      },
    ],
  });
  assert.match(html, /dp-item-select/);
  assert.match(html, /2 Carton/);
  assert.doesNotMatch(html, /2 Pcs/);
  assert.doesNotMatch(html, /Requested \/ Allocated|dp-item-columns/);
  assert.match(html, /aria-label="Quantity to load for Milk \(Carton\)"/);
  assert.match(html, /aria-label="Select item Milk from order TD-1"/);
  assert.doesNotMatch(html, /<span>Item<\/span><\/label>/);
});

test("customer demand renders item rows immediately without an Items expander", () => {
  const { result } = selectionController();
  let html;
  result.$root = {
    find: () => ({
      html: (value) => {
        html = value;
      },
    }),
  };
  result.update_select_all_state = () => {};
  result.render_pagination = () => {};
  result.render_demand();
  assert.match(html, /dp-item-select/);
  assert.match(html, /Yoghurt/);
  assert.match(
    html,
    /class="dp-order-controls"[\s\S]*?class="dp-order-toggle"/
  );
  assert.doesNotMatch(html, /class="dp-order-identity"><button/);
  assert.doesNotMatch(html, /dp-expand-order/);
  assert.match(html, /Stock on Hand/);
  assert.match(html, /class="dp-order-grouping"/);
  assert.ok(
    html.indexOf('class="dp-order-grouping"') <
      html.indexOf('class="dp-order-meta"')
  );
  assert.match(html, /aria-label="Select order TD-1"/);
  assert.doesNotMatch(html, /<span>Order<\/span><\/label>/);
  assert.doesNotMatch(
    html,
    /Allocated \/ Requested|No territory|dp-item-columns/
  );
});

test("grouping uses compact configured tags with full details in a hidden popover template", () => {
  const { result } = controller();
  const html = result.render_grouping_details({
    transport_zone: "Meru",
    delivery_method: "Company Delivery",
    delivery_group: "Dairy",
    transport_compatibility_group: "Chilled",
    transport_handling_profile: "Cold Chain",
  });
  assert.match(html, /title="Zone: Meru"/);
  assert.match(html, /title="Delivery Method: Company Delivery"/);
  assert.match(html, /title="Delivery Group: Dairy"/);
  assert.match(html, /title="Transport Compatibility Group: Chilled"/);
  assert.match(html, /title="Handling Profile: Cold Chain"/);
  assert.match(
    html,
    /<dt>Transport Compatibility Group<\/dt><dd>Chilled<\/dd>/
  );
  assert.doesNotMatch(html, /<details|<summary/);
  assert.match(
    html,
    /class="dp-grouping-trigger" aria-haspopup="dialog" aria-expanded="false"/
  );
  assert.match(html, /<template class="dp-grouping-template">/);
  const empty = result.render_grouping_details({});
  assert.equal((empty.match(/Not set/g) || []).length, 4);
  assert.doesNotMatch(empty, /Handling Profile|text-danger/);
  const summary = empty.match(/<button[^>]*>([\s\S]*?)<\/button>/)[1];
  assert.match(summary, /Grouping/);
  assert.doesNotMatch(summary, /Not set/);
  assert.match(
    result.render_grouping_details({ transport_zone: '<img src="x">' }),
    /&lt;img/
  );
});

test("grouping popover closes outside, on Escape, and on a second trigger click without changing selection", () => {
  const { result, events } = controller();
  const calls = [];
  const attributes = {};
  const scroll = new Map();
  const content = {};
  let focused = false;
  const trigger = {
    contains: (target) => target === trigger,
    isConnected: true,
    focus: () => {
      focused = true;
    },
  };
  const mock = {
    attr(key, value) {
      attributes[key] = value;
      return mock;
    },
    popover(value) {
      calls.push(value);
      return mock;
    },
    closest: () => ({
      find: () => ({ contents: () => ({ clone: () => content }) }),
    }),
  };
  trigger.mock = mock;
  result.$root = {
    find: () => ({
      on: (name, callback) => scroll.set(name, callback),
      off: (name) => scroll.delete(name),
    }),
  };
  result.allocations = new Map([["DI-1", { allocated_quantity: 2 }]]);
  result.show_grouping_popover({ currentTarget: trigger });
  assert.equal(calls[0].container, "body");
  assert.equal(calls[0].trigger, "manual");
  assert.equal(calls[0].content, content);
  assert.equal(attributes["aria-expanded"], "true");
  events.document.get("pointerdown")({ target: trigger });
  assert.equal(result.grouping_trigger, trigger);
  events.document.get("pointerdown")({ target: { closest: () => ({}) } });
  assert.equal(result.grouping_trigger, trigger);
  events.document.get("pointerdown")({ target: { closest: () => null } });
  assert.equal(result.grouping_trigger, null);
  assert.equal(events.document.size, 0);
  assert.equal(events.window.size, 0);
  assert.equal(scroll.size, 0);
  assert.equal(calls.at(-1), "dispose");
  result.show_grouping_popover({ currentTarget: trigger });
  events.document.get("keydown")({ key: "Escape", preventDefault() {} });
  assert.equal(focused, true);
  assert.equal(attributes["aria-expanded"], "false");
  result.show_grouping_popover({ currentTarget: trigger });
  result.show_grouping_popover({ currentTarget: trigger });
  assert.equal(result.grouping_trigger, null);
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
});

test("collapsing an order preserves selected quantities and updates accessible state", () => {
  const { result, controls } = selectionController();
  result.allocations.set("DI-1", {
    delivery_order: "TD-1",
    allocated_quantity: 2,
  });
  const attributes = {};
  let icon;
  const mock = {
    data: () => "TD-1",
    attr(values) {
      Object.assign(attributes, values);
      return mock;
    },
    text(value) {
      icon = value;
      return mock;
    },
  };
  const event = { currentTarget: { mock } };
  result.toggle_order_details(event);
  assert.equal(controls.get('[data-details="TD-1"]').hidden, true);
  assert.equal(attributes["aria-expanded"], "false");
  assert.equal(attributes["aria-label"], "Expand items for TD-1");
  assert.equal(icon, "▸");
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
  result.toggle_order_details(event);
  assert.equal(controls.get('[data-details="TD-1"]').hidden, false);
  assert.equal(attributes["aria-expanded"], "true");
  assert.equal(icon, "▾");
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
});

test("orders start expanded and retain individual collapse state after rerendering", () => {
  const { result } = selectionController();
  let html;
  result.$root = {
    find: () => ({
      html: (value) => {
        html = value;
      },
    }),
  };
  result.update_select_all_state = () => {};
  result.render_pagination = () => {};
  result.render_demand();
  assert.match(html, /aria-expanded="true" aria-controls="dp-items-TD-1"/);
  assert.doesNotMatch(html, /data-details="TD-1" hidden/);
  result.collapsed_orders = new Set(["TD-1"]);
  result.render_demand();
  assert.match(html, /aria-expanded="false" aria-controls="dp-items-TD-1"/);
  assert.match(html, /id="dp-items-TD-1" data-details="TD-1" hidden/);
  assert.match(html, /Milk/);
});

test("multiple order summaries share one item heading and retain important restrictions", () => {
  const { result } = selectionController();
  result.orders = [
    {
      name: "TD-1",
      customer_name: "Customer A",
      required_delivery_date: "2026-11-13",
      refrigeration_required: 1,
    },
    { name: "TD-2", customer_name: "Customer B", priority: "High" },
  ];
  result.details["TD-1"].restricted_access = true;
  result.details["TD-1"].access_notes = "Use small vehicles";
  result.details["TD-2"] = {
    name: "TD-2",
    items: [{ name: "DI-4", item_code: "Cheese", available_quantity: 4 }],
  };
  let html;
  result.$root = {
    find: () => ({
      html: (value) => {
        html = value;
      },
    }),
  };
  result.update_select_all_state = () => {};
  result.render_pagination = () => {};
  result.render_demand();
  assert.equal((html.match(/class="dp-demand-header"/g) || []).length, 1);
  assert.equal((html.match(/Stock on Hand/g) || []).length, 1);
  assert.match(html, /Customer A/);
  assert.match(html, /Customer B/);
  assert.match(html, /Refrigerated/);
  assert.match(html, /Restricted access/);
  assert.match(html, /Use small vehicles/);
  assert.match(html, /High priority/);
  const groups = [
    ...html.matchAll(
      /<section class="dp-order-group"[^>]*>([\s\S]*?)<\/section>/g
    ),
  ];
  assert.equal(groups.length, 2);
  assert.match(groups[0][1], /Customer A/);
  assert.match(groups[0][1], /Milk/);
  assert.doesNotMatch(groups[0][1], /Cheese|Customer B/);
  assert.match(groups[1][1], /Customer B/);
  assert.match(groups[1][1], /Cheese/);
  assert.doesNotMatch(groups[1][1], /Milk|Customer A/);
  result.orders[1].priority = "Urgent";
  result.render_demand();
  assert.match(html, /Urgent priority/);
});

test("order headers prioritize customer and distinguish delivery branches from loading warehouses", () => {
  const { result } = selectionController();
  result.orders = [
    {
      name: "TD-1",
      customer_name: "Naivas",
      delivery_address: "Naivas Meru - Shipping",
      delivery_address_summary: "Naivas Meru, Kenyatta Highway, Meru",
      delivery_city: "Meru",
      delivery_address_type: "Shipping",
      town: "Meru",
    },
    {
      name: "TD-2",
      customer_name: "Naivas",
      delivery_address: "Naivas Kisii - Shipping",
      delivery_address_summary: "Naivas Kisii, Hospital Road, Kisii",
      delivery_city: "Kisii",
      town: "Kisii",
    },
  ];
  result.details["TD-2"] = { name: "TD-2", items: [] };
  let html;
  result.$root = {
    find: () => ({
      html: (value) => {
        html = value;
      },
    }),
  };
  result.update_select_all_state = () => {};
  result.render_pagination = () => {};
  result.render_demand();
  assert.match(html, /<strong class="dp-order-customer">Naivas<\/strong>/);
  assert.match(
    html,
    /Deliver to:<\/span> Naivas Meru, Kenyatta Highway, Meru<\/span>/
  );
  assert.match(
    html,
    /Deliver to:<\/span> Naivas Kisii, Hospital Road, Kisii<\/span>/
  );
  assert.ok(
    html.indexOf('class="dp-order-customer"') <
      html.indexOf('class="dp-order-reference"')
  );
  assert.doesNotMatch(html, /class="dp-order-destination">Loading warehouse/);
  assert.match(html, /From:<\/span> Loading warehouse/);
  assert.match(html, /class="dp-order-city">— Meru/);
  assert.match(html, /class="dp-order-city">— Kisii/);
  assert.match(html, /Linked address type: Shipping/);
  assert.doesNotMatch(html, /Naivas Meru - Shipping/);
  result.orders[0].delivery_address = null;
  result.orders[0].delivery_address_summary = "";
  result.render_demand();
  assert.match(html, /Deliver to:<\/span> Delivery address not set<\/span>/);
  result.orders[0].delivery_address = "CUST-001-Billing";
  result.render_demand();
  assert.match(html, /Deliver to:<\/span> Delivery address unavailable/);
  assert.doesNotMatch(html, /CUST-001-Billing/);
});

test("availability panels use keyboard-accessible disclosure sections collapsed by default", () => {
  const { result, layouts } = controller();
  result.page = { main: {} };
  result.make_layout();
  const html = layouts[0];
  assert.doesNotMatch(
    html,
    /dp-filter-grid-collapsed|btn-toggle-filters|dp-collapsible-header/
  );
  assert.match(
    html,
    /class="dp-filter-grid" role="group" aria-label="Planning Filters"/
  );
  assert.match(html, /<details class="dp-section dp-fleet-section">/);
  assert.match(html, /<details class="dp-section dp-driver-section">/);
  assert.match(html, /<details class="dp-section dp-suggestions-section">/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.match(
    html,
    /<summary class="dp-section-title">Fleet Availability<\/summary>/
  );
  assert.match(html, /btn-create-load/);
  assert.match(html, /class="dp-category-picker"/);
  assert.doesNotMatch(html, /dp-category-select/);
  assert.ok(
    html.indexOf('class="dp-plan-metrics"') < html.indexOf("btn-evaluate")
  );
  assert.ok(
    html.indexOf("btn-create-load") < html.indexOf('class="dp-plan-content')
  );
  assert.ok(
    html.indexOf("btn-create-load") < html.indexOf('class="dp-costed-vehicles"')
  );
});

test("suggestions expand when requested and collapse when selection invalidates them", async () => {
  const { result } = controller();
  const properties = new Map();
  const messages = new Map();
  result.$root = {
    find(selector) {
      const target = {
        prop(key, value) {
          properties.set(`${selector}:${key}`, value);
          return target;
        },
        html(value) {
          messages.set(selector, value);
          return target;
        },
        addClass() {
          return target;
        },
      };
      return target;
    },
  };
  result.suggestion_request_id = 0;
  result.allocations = new Map();
  result.get_filter_values = () => ({ company: "Example" });
  result.get_configured_filter_values = () => ({});
  result.get_native_filter_values = () => [];
  result.selected_vehicle_category = () => null;
  result.loading = () => "Loading";
  await result.load_suggestions();
  assert.equal(properties.get(".dp-suggestions-section:open"), true);
  assert.match(
    messages.get(".dp-suggestions"),
    /No compatible suggestions found/
  );
  result.clear_suggestions();
  assert.equal(properties.get(".dp-suggestions-section:open"), false);
  assert.match(messages.get(".dp-suggestions"), /Run suggestions/);
  assert.equal(properties.get(".btn-suggest:disabled"), false);
});

test("plan summary stays above the actions and clears when a plan becomes stale", () => {
  const { result } = controller();
  const nodes = new Map();
  result.$root = {
    find(selector) {
      if (!nodes.has(selector)) {
        const node = {
          content: "",
          empty() {
            node.content = "";
            return node;
          },
          html(value) {
            node.content = value;
            return node;
          },
          prop(key, value) {
            node[key] = value;
            return node;
          },
          addClass() {
            return node;
          },
          removeClass() {
            return node;
          },
          end() {
            return result.$root;
          },
        };
        nodes.set(selector, node);
      }
      return nodes.get(selector);
    },
  };
  result.allocations = new Map([["DI-1", { allocated_quantity: 1 }]]);
  result.selected_vehicle_category = () => null;
  result.render_capacity_breakdown = () => "Capacity Available";
  result.requirements_text = () => "";
  result.load_cost_structure_estimates = () => {};
  result.plan = {
    total_stops: 2,
    totals: { weight: 50, volume: 10 },
    weight_uom: "Kg",
    volume_uom: "Litre",
    recommended_vehicle_category: "Canter",
    compatible_vehicle_categories: [],
  };
  result.render_plan();
  assert.match(nodes.get(".dp-plan-metrics").content, /Stops/);
  assert.match(nodes.get(".dp-plan-metrics").content, /Kg/);
  assert.match(nodes.get(".dp-plan-content").content, /Capacity Available/);
  assert.doesNotMatch(nodes.get(".dp-plan-content").content, /dp-plan-metrics/);
  assert.equal(nodes.get(".btn-create-load").disabled, false);
  result.plan = null;
  result.render_plan();
  assert.equal(nodes.get(".dp-plan-metrics").content, "");
  assert.equal(nodes.get(".btn-create-load").disabled, true);
});

test("refresh loads visible items together and replaces stale stock before rendering", async () => {
  const { result } = selectionController();
  result.refresh_request_id = 0;
  result.get_filter_values = () => ({
    company: "Example",
    source_warehouse: "Loading warehouse",
  });
  result.get_configured_filter_values = () => ({});
  result.get_native_filter_values = () => [];
  result.selected_vehicle_category = () => null;
  result.refresh_transfers = () => {};
  result.refresh_suppliers = () => {};
  result.$root = { find: () => ({ html() {} }) };
  result.loading = () => "Loading";
  const requests = [];
  result.call = async (method, args) => {
    requests.push({ method, args });
    return method === "get_planning_demand"
      ? { orders: [{ name: "TD-1" }, { name: "TD-2" }] }
      : [
          { name: "TD-1", items: [{ stock_warehouse: "Loading warehouse" }] },
          { name: "TD-2", items: [] },
        ];
  };
  let rendered = 0;
  result.render_demand = () => {
    rendered++;
    assert.equal(
      result.details["TD-1"].items[0].stock_warehouse,
      "Loading warehouse"
    );
  };
  await result.refresh(false, false);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].method, "get_delivery_order_details");
  assert.equal(
    Array.from(requests[1].args.delivery_order_names).join(","),
    "TD-1,TD-2"
  );
  assert.equal(rendered, 1);
});

test("selecting and clearing a whole demand keeps expanded item quantities synchronized", async () => {
  const { result } = selectionController();
  result.ensure_details = async () => {};
  let rendered = 0;
  result.render_demand = () => {
    rendered++;
  };
  const checkbox = {
    checked: true,
    mock: { closest: () => ({ data: () => "TD-1" }) },
  };
  await result.toggle_order({ currentTarget: checkbox });
  assert.equal(result.allocations.size, 2);
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 5);
  assert.equal(result.allocations.get("DI-2").allocated_quantity, 3);
  assert.equal(result.allocations.has("DI-3"), false);
  checkbox.checked = false;
  await result.toggle_order({ currentTarget: checkbox });
  assert.equal(result.allocations.size, 0);
  assert.equal(rendered, 2);
});

test("warehouse refresh cannot be overwritten by late item detail responses", async () => {
  const { result } = controller();
  result.details = {};
  result.details_requests = new Map();
  let resolve;
  result.call = () =>
    new Promise((done) => {
      resolve = done;
    });
  const oldRequest = result.ensure_details(["TD-1"]);
  result.details = {};
  result.details_requests = new Map();
  resolve([{ name: "TD-1", items: [{ stock_warehouse: "Old warehouse" }] }]);
  await oldRequest;
  assert.equal(result.details["TD-1"], undefined);
});

test("native planner serializes canonical links and the selected loading warehouse", async () => {
  const { result, requests } = controller();
  await result.call("evaluate_manual_load_plan", {
    allocations: [
      {
        delivery_order: "TD-1",
        delivery_order_item: "DI-1",
        allocated_quantity: 2,
      },
    ],
  });
  const row = requests[0].args.allocations[0];
  assert.equal(row.transport_demand, "TD-1");
  assert.equal(row.transport_demand_item, "DI-1");
  assert.equal(row.allocated_quantity, 2);
  assert.equal("delivery_order" in row, false);
  assert.equal("delivery_order_item" in row, false);
  assert.equal(requests[0].args.source_warehouse, "Loading warehouse");
});

test("legacy planner keeps its own reference dialect", async () => {
  const { result, requests } = controller(false);
  await result.call("evaluate_manual_load_plan", {
    allocations: [
      {
        delivery_order: "DO-1",
        delivery_order_item: "OI-1",
        allocated_quantity: 1,
      },
    ],
  });
  assert.equal(requests[0].args.allocations[0].delivery_order, "DO-1");
  assert.equal("transport_demand" in requests[0].args.allocations[0], false);
});

test("using a canonical suggestion retains exact item IDs for subsequent edits", async () => {
  const { result } = controller();
  result.suggestions = [
    {
      allocations: [
        {
          transport_demand: "TD-1",
          transport_demand_item: "DI-1",
          allocated_quantity: 2,
        },
      ],
    },
  ];
  result.allocations = new Map();
  result.ensure_details = async (names) => assert.equal(names[0], "TD-1");
  result.render_demand = () => {};
  result.evaluate_plan = async () => {};
  result.$root = { find: () => [{ scrollIntoView() {} }] };
  await result.use_suggestion({ currentTarget: {} });
  assert.equal(result.allocations.get("DI-1").allocated_quantity, 2);
  assert.equal(result.allocations.get("DI-1").delivery_order, "TD-1");
});
