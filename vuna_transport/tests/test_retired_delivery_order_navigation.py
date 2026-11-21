"""Standard navigation is maintained in JSON, not an after-migrate cleanup."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestRetiredDeliveryOrderNavigation(unittest.TestCase):
	def test_navigation_json_has_no_retired_names(self):
		paths = sorted(
			path
			for directory in (
				ROOT / "workspace_sidebar",
				ROOT / "vuna_transport" / "sidebar",
				ROOT / "vuna_transport" / "workspace",
			)
			for path in directory.rglob("*.json")
		)
		self.assertTrue(paths)
		for path in paths:
			with self.subTest(path=str(path.relative_to(ROOT))):
				content = path.read_text()
				json.loads(content)
				for retired in ("Delivery Order", "Open Delivery Demand", "Unallocated Delivery Orders"):
					self.assertNotIn(retired, content)

	def test_retired_navigation_cleanup_is_not_registered_or_defined(self):
		for filename in ("hooks.py", "install.py"):
			with self.subTest(filename=filename):
				self.assertNotIn("cleanup_retired_delivery_order_navigation", (ROOT / filename).read_text())
