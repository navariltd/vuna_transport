<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

const mount = ref(null);
let controller;
let active = true;

onMounted(async () => {
	let context;
	try {
		const response = await frappe.call({
			method: "vuna_transport.services.planning.dispatch_planning_service.get_customer_planning_context",
		});
		context = response.message;
	} catch (error) {
		if (active) $(mount.value).text(__("Unable to load Dispatch Planning. Please refresh."));
		return;
	}
	if (!active) return;
	controller = new vuna_transport.DispatchPlanning.Controller({
		page: { main: $(mount.value) },
		customer_context: context,
	});
	frappe.dispatch_planning_controller = controller;
	controller.refresh();
});

onBeforeUnmount(() => {
	active = false;
	if (controller) {
		controller.destroy?.();
		if (frappe.dispatch_planning_controller === controller) {
			frappe.dispatch_planning_controller = null;
		}
	}
});
</script>

<template>
	<div ref="mount"></div>
</template>
