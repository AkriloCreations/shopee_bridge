"""Order service layer (Shopee -> ERPNext mapping).

This module encapsulates business logic for pulling order data from Shopee and
upserting ERPNext sales documents. External interaction (HTTP, signing) is
delegated to `clients` and `auth`. Public API kept stable for jobs / API layer.

Current implementation is intentionally lightweight / partially mocked:
 - Uses real signed GET calls for list & detail endpoints.
 - ERPNext create/update logic is represented by TODO blocks returning mock names
   so the sync pipeline can be exercised incrementally without side effects.
 - Idempotency keyed by custom field `shopee_order_sn` (expected to exist on
   Sales Order / Sales Invoice / Delivery Note via custom field creation).
 - Prevents status downgrade by comparing `last_pushed_update_time` custom field
   before applying transitions (mocked logic placeholder).

Functions return JSON-serializable types for easy logging and testing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
import time
import math
import frappe
from frappe.utils import nowdate, now, get_datetime

from shopee_bridge import clients, helpers

ORDER_LIST_PATH = "/api/v2/order/get_order_list"
ORDER_DETAIL_PATH = "/api/v2/order/get_order_detail"


# --- Shopee Sync Log fallback ---
def _log_sync(event: str, data: Dict[str, Any]):
	try:
		frappe.logger().info(f"[Shopee][orders] {event} {data}")
	except Exception:
		pass


def _find_existing(doctype: str, custom_order_sn: str) -> str | None:
    try:
        name = frappe.db.get_value(doctype, {"custom_order_sn": custom_order_sn})
        return name
    except Exception:
        return None


def _get_last_pushed_update_time(doctype: str, name: str) -> int:
	try:
		ts = frappe.db.get_value(doctype, name, "last_pushed_update_time")
		return int(ts) if ts else 0
	except Exception:
		return 0


def get_order_list(time_from: int, time_to: int, status: str | None = None, page_size: int = 100) -> List[str]:
	"""Fetch list of order_sn within time window.

	Args:
		time_from: Unix epoch (seconds) inclusive lower bound.
		time_to: Unix epoch (seconds) inclusive upper bound.
		status: Optional Shopee order status filter.
		page_size: Page size (Shopee max typically 100).
		
	Returns:
		List of order_sn strings.
		
	Raises:
		ShopeeAPIError: On API errors
	"""
	
	if not helpers.is_valid_epoch(time_from) or not helpers.is_valid_epoch(time_to):
		frappe.log_error("Invalid time range for order list", "Shopee Order Sync")
		return []
	
	if time_from >= time_to:
		frappe.log_error("Invalid time range: time_from >= time_to", "Shopee Order Sync")
		return []
	
	params: Dict[str, Any] = {
		"time_range_field": "update_time",
		"time_from": int(time_from),
		"time_to": int(time_to),
		"page_size": min(max(int(page_size), 1), 100),
	}
	
	if status:
		params["order_status"] = status
	
	order_sns: List[str] = []
	
	try:
		for item in clients.paginate_get(ORDER_LIST_PATH, params, page_size=page_size):
			if sn := item.get("order_sn"):
				order_sns.append(sn)
	except clients.ShopeeAPIError as e:
		_log_sync("order_list_api_error", {"error": str(e), "status": e.status_code})
		frappe.log_error(f"Order list fetch failed: {e}", "Shopee Order Sync")
		return []
	except Exception as e:
		_log_sync("order_list_error", {"error": str(e)})
		frappe.log_error(f"Order list fetch failed: {e}", "Shopee Order Sync")
		return []
	
	return order_sns


def get_order_detail(order_sn_list: List[str]) -> List[Dict[str, Any]]:
	"""Fetch detailed order objects.

	Batches list into chunks to respect API size limits (assume <= 50 per call).
	"""
	results: List[Dict[str, Any]] = []
	if not order_sn_list:
		return results
	
	try:
		for result in clients.batch_request(ORDER_DETAIL_PATH, order_sn_list, batch_size=50):
			results.append(result)
	except Exception as e:
		_log_sync("order_detail_error", {"error": str(e)})
		frappe.log_error(f"Order detail fetch failed: {e}", "Shopee Order Sync")
	
	return results


def ensure_customer_and_addresses(order: Dict[str, Any]) -> Tuple[str, str]:
    """Ensure ERPNext Customer & Address for order.

    Customer name: SHP-<buyer_username>
    """
    raw_username = (order.get("buyer_username") or "").strip()
    base = raw_username or "UNKNOWN"
    safe_base = base.replace(" ", "_")[:140] or "UNKNOWN"
    customer_name = f"SHP-{safe_base}"
    address_name = f"ADDR-{order.get('order_sn') or order.get('custom_order_sn') or 'UNKNOWN'}"
    # Idempotent Customer
    if not frappe.db.exists("Customer", customer_name):
        try:
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_group": "Individual",
                "territory": "All Territories",
                "custom_order_sn": order.get("order_sn") or order.get("custom_order_sn"),
            }).insert(ignore_permissions=True)
            _log_sync("customer_created", {"customer": customer_name})
        except Exception as e:
            _log_sync("customer_create_error", {"customer": customer_name, "error": str(e)})
    # Idempotent Address
    if not frappe.db.exists("Address", address_name):
        try:
            addr = order.get("recipient_address", {})
            frappe.get_doc({
                "doctype": "Address",
                "address_title": address_name,
                "address_line1": addr.get("address_line1", "-"),
                "city": addr.get("city", "-"),
                "country": addr.get("country", "-"),
                "customer": customer_name,
                "custom_order_sn": order.get("order_sn") or order.get("custom_order_sn"),
            }).insert(ignore_permissions=True)
            _log_sync("address_created", {"address": address_name})
        except Exception as e:
            _log_sync("address_create_error", {"address": address_name, "error": str(e)})
    return customer_name, address_name


def upsert_sales_order(order: Dict[str, Any]) -> str:
    """Create or update Sales Order for Shopee order.

    Idempotency via custom field custom_order_sn.
    Returns Sales Order name.
    """
    custom_order_sn = order.get("order_sn") or order.get("custom_order_sn") or "UNKNOWN"
    existing = _find_existing("Sales Order", custom_order_sn)
    so_doc = None
    now_epoch = int(time.time())
    order_update_time = int(order.get("update_time", now_epoch))
    po_no = custom_order_sn
    # delivery_date: use cancel date if present, else None
    cancel_date = order.get("cancel_time") or order.get("cancel_date")
    delivery_date = int(cancel_date) if cancel_date else None
    if existing:
        last_pushed = _get_last_pushed_update_time("Sales Order", existing)
        if order_update_time < last_pushed:
            _log_sync("skip_status_downgrade", {"custom_order_sn": custom_order_sn, "existing": existing, "order_update_time": order_update_time, "last_pushed": last_pushed})
            return existing
        try:
            so_doc = frappe.get_doc("Sales Order", existing)
            so_doc.update({
                "customer": ensure_customer_and_addresses(order)[0],
                "transaction_date": nowdate(),
                "custom_order_sn": custom_order_sn,
                "po_no": po_no,
                "delivery_date": delivery_date,
                "last_pushed_update_time": order_update_time,
            })
            so_doc.save(ignore_permissions=True)
            _log_sync("sales_order_updated", {"custom_order_sn": custom_order_sn, "name": so_doc.name})
            return so_doc.name
        except Exception as e:
            _log_sync("sales_order_update_error", {"custom_order_sn": custom_order_sn, "error": str(e)})
            return existing
    # Create new Sales Order
    try:
        customer, address = ensure_customer_and_addresses(order)
        items = []
        for item in order.get("items", []):
            items.append({
                "item_code": item.get("item_code", "Shopee Item"),
                "item_name": item.get("item_name", "Shopee Item"),
                "qty": item.get("model_quantity_purchased", 1),
                "rate": item.get("item_price", 0),
            })
        if not items:
            items = [{
                "item_code": order.get("item_code", "Shopee Item"),
                "item_name": order.get("item_name", "Shopee Item"),
                "qty": order.get("item_quantity", 1),
                "rate": order.get("item_price", 0),
            }]
        so_doc = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": customer,
            "transaction_date": nowdate(),
            "custom_order_sn": custom_order_sn,
            "po_no": po_no,
            "delivery_date": delivery_date,
            "last_pushed_update_time": order_update_time,
            "items": items,
        })
        so_doc.insert(ignore_permissions=True)
        _log_sync("sales_order_created", {"custom_order_sn": custom_order_sn, "name": so_doc.name})
        return so_doc.name
    except Exception as e:
        _log_sync("sales_order_create_error", {"custom_order_sn": custom_order_sn, "error": str(e)})
        return f"SO-{custom_order_sn}"


def ensure_sales_invoice_for_paid(so_name: str, order: Dict[str, Any]) -> str:
	"""If order is paid, ensure a Sales Invoice exists (mocked)."""
	order_sn = order.get("order_sn") or "UNKNOWN"
	existing = _find_existing("Sales Invoice", order_sn)
	now_epoch = int(time.time())
	order_update_time = int(order.get("update_time", now_epoch))
	if existing:
		last_pushed = _get_last_pushed_update_time("Sales Invoice", existing)
		if order_update_time < last_pushed:
			_log_sync("skip_invoice_status_downgrade", {"order_sn": order_sn, "existing": existing, "order_update_time": order_update_time, "last_pushed": last_pushed})
			return existing
		try:
			si_doc = frappe.get_doc("Sales Invoice", existing)
			si_doc.update({
				"customer": ensure_customer_and_addresses(order)[0],
				"posting_date": nowdate(),
				"shopee_order_sn": order_sn,
				"last_pushed_update_time": order_update_time,
			})
			si_doc.save(ignore_permissions=True)
			_log_sync("sales_invoice_updated", {"order_sn": order_sn, "name": si_doc.name})
			return si_doc.name
		except Exception as e:
			_log_sync("sales_invoice_update_error", {"order_sn": order_sn, "error": str(e)})
			return existing
	# Create new Sales Invoice
	try:
		customer, address = ensure_customer_and_addresses(order)
		si_doc = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": customer,
			"posting_date": nowdate(),
			"shopee_order_sn": order_sn,
			"last_pushed_update_time": order_update_time,
			"items": [{
				"item_code": order.get("item_code", "Shopee Item"),
				"item_name": order.get("item_name", "Shopee Item"),
				"qty": order.get("item_quantity", 1),
				"rate": order.get("item_price", 0),
			}],
		})
		si_doc.insert(ignore_permissions=True)
		_log_sync("sales_invoice_created", {"order_sn": order_sn, "name": si_doc.name})
		return si_doc.name
	except Exception as e:
		_log_sync("sales_invoice_create_error", {"order_sn": order_sn, "error": str(e)})
		return f"SI-{so_name}"


def ensure_delivery_note_for_ready(so_or_si: str, order: Dict[str, Any]) -> str:
	"""If order status indicates ready to ship, ensure Delivery Note exists."""
	order_sn = order.get("order_sn") or "UNKNOWN"
	existing = _find_existing("Delivery Note", order_sn)
	now_epoch = int(time.time())
	order_update_time = int(order.get("update_time", now_epoch))
	if existing:
		last_pushed = _get_last_pushed_update_time("Delivery Note", existing)
		if order_update_time < last_pushed:
			_log_sync("skip_dn_status_downgrade", {"order_sn": order_sn, "existing": existing, "order_update_time": order_update_time, "last_pushed": last_pushed})
			return existing
		try:
			dn_doc = frappe.get_doc("Delivery Note", existing)
			dn_doc.update({
				"customer": ensure_customer_and_addresses(order)[0],
				"posting_date": nowdate(),
				"shopee_order_sn": order_sn,
				"last_pushed_update_time": order_update_time,
			})
			dn_doc.save(ignore_permissions=True)
			_log_sync("delivery_note_updated", {"order_sn": order_sn, "name": dn_doc.name})
			return dn_doc.name
		except Exception as e:
			_log_sync("delivery_note_update_error", {"order_sn": order_sn, "error": str(e)})
			return existing
	# Create new Delivery Note
	try:
		customer, address = ensure_customer_and_addresses(order)
		dn_doc = frappe.get_doc({
			"doctype": "Delivery Note",
			"customer": customer,
			"posting_date": nowdate(),
			"shopee_order_sn": order_sn,
			"last_pushed_update_time": order_update_time,
			"items": [{
				"item_code": order.get("item_code", "Shopee Item"),
				"item_name": order.get("item_name", "Shopee Item"),
				"qty": order.get("item_quantity", 1),
				"rate": order.get("item_price", 0),
			}],
		})
		dn_doc.insert(ignore_permissions=True)
		_log_sync("delivery_note_created", {"order_sn": order_sn, "name": dn_doc.name})
		return dn_doc.name
	except Exception as e:
		_log_sync("delivery_note_create_error", {"order_sn": order_sn, "error": str(e)})
		return f"DN-{so_or_si}"


def on_completed(order_sn: str) -> None:
	"""Hook invoked when order reaches completed state (placeholder)."""
	# TODO: perform finalization tasks (e.g., mark as fulfilled, trigger notifications)
	_log_sync("completed", {"order_sn": order_sn})


def sync_incremental_orders(updated_since_minutes: int = 15) -> Dict[str, Any]:
	"""High-level incremental sync pipeline.

	Steps:
		1. Calculate time window (now - minutes, now).
		2. Pull order_sn list.
		3. Pull detailed orders.
		4. Upsert ERPNext docs per order.
		5. Aggregate results & per-order errors.

	Returns summary dict.
	"""
	started = int(time.time())
	window_to = started
	window_from = window_to - (updated_since_minutes * 60)
	summary = {
		"window_from": window_from,
		"window_to": window_to,
		"minutes": updated_since_minutes,
		"orders_found": 0,
		"orders_processed": 0,
		"errors": [],
		"duration_s": 0,
	}
	try:
		order_sn_list = get_order_list(window_from, window_to, status=None)
		summary["orders_found"] = len(order_sn_list)
		details = get_order_detail(order_sn_list)
		for od in details:
			try:
				so = upsert_sales_order(od)
				status = (od.get("order_status") or "").lower()
				if status in {"paid", "ready_to_ship", "completed"}:
					si = ensure_sales_invoice_for_paid(so, od)
				else:
					si = None
				if status in {"ready_to_ship", "completed"}:
					dn = ensure_delivery_note_for_ready(si or so, od)  # prefer invoice if created
				else:
					dn = None
				if status == "completed":
					on_completed(od.get("order_sn"))
				summary["orders_processed"] += 1
				_log_sync("order_processed", {"order_sn": od.get("order_sn"), "so": so, "si": si, "dn": dn})
			except Exception as per_exc:  # record per-order error, continue
				err_msg = f"{od.get('order_sn')}: {per_exc}"[:500]
				summary["errors"].append(err_msg)
				frappe.log_error(message=err_msg, title="Shopee Order Sync Error")
	except Exception as exc:
		main_err = str(exc)
		summary["errors"].append(main_err)
		frappe.log_error(message=main_err, title="Shopee Order Sync Fatal")
	finally:
		summary["duration_s"] = round(time.time() - started, 2)
	return summary


def get_order_details(order_sn: str) -> Dict[str, Any]:
	"""Get detailed order information from Shopee.
	
	Args:
		order_sn: Order serial number
		
	Returns:
		Order details dictionary
		
	Raises:
		ShopeeAPIError: On API errors
	"""
	try:
		details = get_order_detail([order_sn])
		return details[0] if details else {}
	except Exception as e:
		frappe.log_error(f"Failed to get order details for {order_sn}: {e}", "Shopee Order Details")
		return {}


def get_escrow_details(order_sn: str) -> Dict[str, Any]:
	"""Get escrow details for an order.
	
	Args:
		order_sn: Order serial number
		
	Returns:
		Escrow details dictionary
	"""
	from shopee_bridge.services import finance
	return finance.get_escrow_detail("", "", 0, order_sn)


def fetch_and_log_escrow(site: str, host: str, access_token: str, shop_id: int, order_sn: str) -> dict:
	"""Fetch escrow detail and log it."""
	from shopee_bridge.services import finance
	escrow = finance.get_escrow_detail(host, access_token, shop_id, order_sn)
	finance.log_escrow(site, order_sn, escrow)
	return escrow


def sync_single_order(order_sn: str) -> Dict[str, Any]:
	"""Sync a single order from Shopee to ERPNext.
	
	Args:
		order_sn: Order serial number
		
	Returns:
		Sync result summary
	"""
	started = int(time.time())
	
	try:
		# Get order details
		order_data = get_order_details(order_sn)
		if not order_data:
			return {"success": False, "error": "Order not found", "order_sn": order_sn}
		
		# Create/update ERPNext documents
		so_name = upsert_sales_order(order_data)
		
		# Check order status for additional documents
		status = (order_data.get("order_status") or "").lower()
		result = {
			"success": True,
			"order_sn": order_sn,
			"sales_order": so_name,
			"status": status,
			"duration_s": round(time.time() - started, 2)
		}
		
		if status in {"paid", "ready_to_ship", "completed"}:
			try:
				from shopee_bridge.services import finance
				escrow = finance.get_escrow_detail("", "", 0, order_sn)
				if escrow and not escrow.get("error"):
					invoice = finance.patch_invoice_with_fees(escrow)
					result["sales_invoice"] = invoice
			except Exception as e:
				result["invoice_error"] = str(e)
		
		if status in {"ready_to_ship", "completed"}:
			try:
				dn = ensure_delivery_note_for_ready(so_name, order_data)
				result["delivery_note"] = dn
			except Exception as e:
				result["delivery_note_error"] = str(e)
		
		return result
		
	except Exception as e:
		frappe.log_error(f"Order sync failed for {order_sn}: {e}", "Shopee Order Sync")
		return {
			"success": False,
			"error": str(e),
			"order_sn": order_sn,
			"duration_s": round(time.time() - started, 2)
		}

