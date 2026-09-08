import { createApp } from "vue";
import DispatchPlanning from "./DispatchPlanning.vue";

function setup_dispatch_planning_vue(wrapper) {
	const app = createApp(DispatchPlanning);
	app.mount(wrapper.get(0));
	return app;
}

frappe.ui.setup_dispatch_planning_vue = setup_dispatch_planning_vue;

export default setup_dispatch_planning_vue;
