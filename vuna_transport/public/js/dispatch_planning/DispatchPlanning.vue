<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";

const mount = ref(null);
let controller;

onMounted(() => {
	controller = new vuna_transport.DispatchPlanning.Controller({
		page: { main: $(mount.value) },
	});
	frappe.dispatch_planning_controller = controller;
	controller.refresh();
});

onBeforeUnmount(() => {
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
