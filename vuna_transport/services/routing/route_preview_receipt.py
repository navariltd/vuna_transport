"""Short-lived signed route receipts; browser-submitted distances are not trusted."""

import hashlib
import hmac
import json
import math
import time

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.verified_command import get_secret

PURPOSE = "vuna-transport-allocation-road-distance-v1:"
MAX_AGE = 1800


def _sign(payload):
	message = PURPOSE + json.dumps(payload, sort_keys=True, separators=(",", ":"))
	return hmac.new(get_secret().encode(), message.encode(), hashlib.sha256).hexdigest()


def issue_receipt(doc, manifest_fingerprint, stop_keys, distance_meters):
	payload = {
		"allocation": doc.name,
		"user": frappe.session.user,
		"modified": str(doc.modified),
		"manifest": manifest_fingerprint,
		"stops": stop_keys,
		"distance_km": distance_meters / 1000,
		"calculated_on": str(now_datetime()),
		"issued": time.time(),
	}
	return json.dumps({"payload": payload, "signature": _sign(payload)}, separators=(",", ":"))


def verify_receipt(token, doc, previous, manifest_fingerprint, stop_keys):
	valid = False
	try:
		if not isinstance(token, str) or len(token) > 32000:
			raise ValueError
		envelope = json.loads(token)
		payload = envelope["payload"]
		valid = (
			hmac.compare_digest(envelope["signature"], _sign(payload))
			and payload["allocation"] == doc.name
			and payload["user"] == frappe.session.user
			and previous is not None
			and payload["modified"] == str(previous.modified)
			and payload["manifest"] == manifest_fingerprint
			and payload["stops"] == stop_keys
			and 0 <= time.time() - payload["issued"] <= MAX_AGE
			and not isinstance(payload["distance_km"], bool)
			and math.isfinite(payload["distance_km"])
			and payload["distance_km"] >= 0
		)
	except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
		valid = False
	if not valid:
		frappe.throw(
			_("This road preview expired or its inputs changed. Preview the route again before saving.")
		)
	return payload
