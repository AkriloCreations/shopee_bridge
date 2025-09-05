"""Logistics / shipping service layer (Shopee -> ERPNext).

Contains thin business helpers around Shopee logistics endpoints plus
ERPNext attachment + status mapping stubs. All Shopee HTTP interactions
delegate to `clients` (signed requests) while persistence & document
creation are intentionally mocked / simplified for incremental rollout.

Functions here MUST remain idempotent where possible (e.g., attaching
labels). Real creation / mutation logic should later replace TODO blocks.
"""

from __future__ import annotations

from typing import Any, Dict
import hashlib
import time
import frappe

from .. import clients

# Shopee API paths
CHANNEL_LIST_PATH = "/api/v2/logistics/get_channel_list"
SHIPPING_PARAMETER_PATH = "/api/v2/logistics/get_shipping_parameter"
SHIP_ORDER_PATH = "/api/v2/logistics/ship_order"
TRACKING_NUMBER_PATH = "/api/v2/logistics/get_tracking_number"
SHIPPING_DOCUMENT_PARAMETER_PATH = "/api/v2/logistics/get_shipping_document_parameter"
GET_SHIPPING_DOCUMENT_PATH = "/api/v2/logistics/get_shipping_document"
DOWNLOAD_SHIPPING_DOCUMENT_PATH = "/api/v2/logistics/download_shipping_document"


def _log(event: str, data: Dict[str, Any]):  # central logging (best-effort)
	try:
		frappe.logger().info(f"[Shopee][logistics] {event} {data}")
	except Exception:  # pragma: no cover
		pass


def get_shipping_parameter(order_sn: str) -> Dict[str, Any]:
	"""Return shipping parameter info for an order.

	Wraps Shopee endpoint `/get_shipping_parameter`.
	"""
	try:
		resp = clients.http_get(SHIPPING_PARAMETER_PATH, {"order_sn": order_sn})
		return resp.get("response") or resp
	except Exception as e:
		_log("get_shipping_parameter_error", {"order_sn": order_sn, "error": str(e)})
		return {"error": str(e)}


def ship_order(order_sn: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
	"""Submit ship order request.

	Args:
		order_sn: Shopee order serial.
		method: Chosen shipping method / channel code.
		params: Additional parameters (dropoff / pickup details, etc.).

	Returns Shopee API response (or error dict).
	"""
	payload = {
		"order_sn": order_sn,
		"shipping_method": method,
		**(params or {}),
	}
	try:
		resp = clients.http_post(SHIP_ORDER_PATH, json=payload)
		return resp.get("response") or resp
	except Exception as e:
		_log("ship_order_error", {"order_sn": order_sn, "error": str(e)})
		return {"error": str(e)}


def get_tracking_number(order_sn: str) -> str:
	"""Fetch tracking number (if allocated)."""
	try:
		resp = clients.http_get(TRACKING_NUMBER_PATH, {"order_sn": order_sn})
		data = resp.get("response") or resp
		return data.get("tracking_number") or data.get("tracking_no") or ""
	except Exception as e:
		_log("get_tracking_number_error", {"order_sn": order_sn, "error": str(e)})
		return ""


def get_shipping_document_parameter(order_sn: str) -> Dict[str, Any]:
	"""Fetch document parameter specification prior to requesting document."""
	try:
		resp = clients.http_get(SHIPPING_DOCUMENT_PARAMETER_PATH, {"order_sn": order_sn})
		return resp.get("response") or resp
	except Exception as e:
		_log("get_shipping_document_parameter_error", {"order_sn": order_sn, "error": str(e)})
		return {"error": str(e)}


def get_shipping_document(order_sn: str) -> Dict[str, Any]:
	"""Request creation of shipping document; returns meta incl. doc_id.

	Shopee responses often provide a result list; we normalize a plausible shape.
	"""
	try:
		resp = clients.http_post(GET_SHIPPING_DOCUMENT_PATH, json={"order_sn_list": [order_sn]})
		data = resp.get("response") or resp
		# Normalize: prefer first document info
		docs = data.get("result_list") or data.get("documents") or []
		first = docs[0] if docs else {}
		return first or data
	except Exception as e:
		_log("get_shipping_document_error", {"order_sn": order_sn, "error": str(e)})
		return {"error": str(e)}


def download_shipping_document(doc_id: str) -> bytes:
	"""Download the raw shipping document (PDF bytes).

	NOTE: Real implementation would detect base64 / URL fields in Shopee response.
	For now returns mock PDF bytes when API interaction not implemented.
	"""
	try:
		# Placeholder: some APIs might require POST with document_id
		resp = clients.http_get(DOWNLOAD_SHIPPING_DOCUMENT_PATH, {"document_id": doc_id})
		data = resp.get("response") or resp
		if isinstance(data, dict) and data.get("pdf_content_base64"):
			import base64
			return base64.b64decode(data["pdf_content_base64"])  # pragma: no cover
		# Mock fallback
		return b"%PDF-1.4\n% Shopee Mock Label\n"
	except Exception as e:
		_log("download_shipping_document_error", {"doc_id": doc_id, "error": str(e)})
		return b""


def attach_shipping_label(dn_name: str, pdf_bytes: bytes, filename: str) -> None:
	"""Attach label to Delivery Note if not already attached (idempotent).

	Strategy:
		- Compute SHA1 hash of bytes.
		- Look for existing File with same attached_to_doctype/name & filename & hash stored in description.
		- If exists, skip; else insert new File (private) with content.
	"""
	if not pdf_bytes:
		return
	sha1 = hashlib.sha1(pdf_bytes).hexdigest()  # noqa: S324 (integrity / idempotency only)
	existing = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Delivery Note",
			"attached_to_name": dn_name,
			"file_name": filename,
		},
		fields=["name", "description"],
	)
	for row in existing:
		if row.get("description") == sha1:
			_log("attach_label_skip", {"dn": dn_name, "filename": filename})
			return
	try:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"attached_to_doctype": "Delivery Note",
				"attached_to_name": dn_name,
				"file_name": filename,
				"content": pdf_bytes,
				"is_private": 1,
				"description": sha1,
			}
		)
		file_doc.insert(ignore_permissions=True)
		_log("attach_label_ok", {"dn": dn_name, "filename": filename, "hash": sha1[:8]})
	except Exception as e:  # pragma: no cover
		_log("attach_label_error", {"dn": dn_name, "error": str(e)})


def update_tracking_status(dn_name: str, status_payload: Dict[str, Any]) -> bool:
	"""Update local tracking status for a Delivery Note (stub).

	Returns True if (mock) update accepted.
	"""
	# TODO: Map payload to custom fields / status updates on DN.
	_log("update_tracking_status", {"dn": dn_name, "payload": list(status_payload.keys())})
	return True


def sync_shipping_status(updated_since_minutes: int = 30) -> Dict[str, Any]:
	"""Pull recent shipping status updates (stub pipeline).

	Placeholder logic simply returns an empty summary ready for extension.
	"""
	now = int(time.time())
	window_from = now - updated_since_minutes * 60
	summary = {
		"window_from": window_from,
		"window_to": now,
		"minutes": updated_since_minutes,
		"updates_found": 0,
		"updates_processed": 0,
		"errors": [],
	}
	# TODO: implement call to e.g. /logistics/get_tracking_info if available; iterate updates.
	return summary


__all__ = [
	"get_shipping_parameter",
	"ship_order",
	"get_tracking_number",
	"get_shipping_document_parameter",
	"get_shipping_document",
	"download_shipping_document",
	"attach_shipping_label",
	"update_tracking_status",
	"sync_shipping_status",
]

