"""Preserve customer delivery behaviour and default transfers to manual generation."""

from vuna_transport.install import ensure_transport_demand_sources


def execute():
	ensure_transport_demand_sources()
