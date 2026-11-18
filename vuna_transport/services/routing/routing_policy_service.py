"""Server-side routing permissions and conservative, atomic usage limits."""

import frappe
from frappe import _
from frappe.utils import nowdate
from redis.exceptions import RedisError

from vuna_transport.services.shared.security_service import require_authenticated

DEFAULT_ROLES = {"System Manager", "Transport Manager", "Transport Dispatcher", "Transport Planner"}
LIMITS = {
	"routing_max_locations": (100, 200),
	"routing_daily_request_limit": (100, 10000),
	"routing_daily_shipment_limit": (1000, 100000),
}

# Reserve both counters together. Failed attempts remain counted deliberately;
# HTTP rollback must not restore a quota after Google may have received a call.
RESERVE_SCRIPT = """
local requests = tonumber(redis.call('HGET', KEYS[1], 'requests') or '0')
local shipments = tonumber(redis.call('HGET', KEYS[1], 'shipments') or '0')
if requests + tonumber(ARGV[1]) > tonumber(ARGV[3]) then return -1 end
if shipments + tonumber(ARGV[2]) > tonumber(ARGV[4]) then return -2 end
redis.call('HINCRBY', KEYS[1], 'requests', ARGV[1])
redis.call('HINCRBY', KEYS[1], 'shipments', ARGV[2])
redis.call('EXPIRE', KEYS[1], 172800)
return 1
"""


def get_limit(settings, field):
	default, maximum = LIMITS[field]
	raw = settings.get(field)
	if raw in (None, ""):
		return default
	try:
		value = int(raw)
		if isinstance(raw, bool) or float(raw) != value or not 1 <= value <= maximum:
			raise ValueError
		return value
	except (TypeError, ValueError, OverflowError):
		pass
	frappe.throw(_("Routing limits must be positive whole numbers within the supported range."))


def allowed_role(settings):
	roles = {row.role for row in settings.get("routing_allowed_roles") or [] if row.role}
	return bool(set(frappe.get_roles()) & (roles or DEFAULT_ROLES))


def require_routing_role(settings):
	if not allowed_role(settings):
		frappe.throw(
			_("Your role is not allowed to request Google routes in Transport Settings."),
			frappe.PermissionError,
		)


def reserve_usage(settings, *, requests, shipments=0):
	if requests < 1 or shipments < 0:
		raise ValueError("Invalid routing usage reservation")
	request_limit = get_limit(settings, "routing_daily_request_limit")
	shipment_limit = get_limit(settings, "routing_daily_shipment_limit")
	failed = False
	try:
		key = frappe.cache.make_key("vuna_transport:routing:usage:" + nowdate())
		result = frappe.cache.eval(RESERVE_SCRIPT, 1, key, requests, shipments, request_limit, shipment_limit)
	except RedisError:
		failed = True
	if failed:
		frappe.throw(_("Routing usage limits could not be checked. No Google request was sent."))
	if result != 1:
		frappe.throw(
			_(
				"The daily Google routing request or optimization shipment limit has been reached. Contact your administrator."
			)
		)


@frappe.whitelist()
def get_routing_context():
	require_authenticated()
	settings = frappe.get_cached_doc("Transport Settings")
	can_preview = bool(
		settings.enabled and settings.get("routing_provider") == "Google" and allowed_role(settings)
	)
	return {
		"can_plan": allowed_role(settings),
		"can_preview": can_preview,
		"can_optimize": bool(can_preview and settings.get("enable_google_optimization")),
	}
