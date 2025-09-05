"""Pure, deterministic Shopee → ERPNext mapping helpers.

Design Goals:
 - ZERO side effects (no frappe / DB / network imports)
 - Stable deterministic output for identical input (idempotent)
 - Defensive against missing keys; never raises for KeyError
 - Return plain ``dict`` / ``list[dict]`` structures ready for higher layers
 - Keep money values unrounded (float friendly / Decimal-ready)

NOTE: Real business logic (accounts, cost centers, item code canonicalization,
UOM conversions, etc.) intentionally omitted or marked as TODO so this module
remains simple and fully unit-testable in isolation.

Example (abbreviated order payload) -> customer mapping::

    order = {
        "buyer_username": "alice88",
        "buyer_user_id": 12345,
        "recipient_address": {"name": "Alice A.", "city": "Jakarta"},
        "items": [
            {"item_name": "Blue Shirt", "item_sku": "bs 001", "model_quantity_purchased": 2, "item_price": 125000.0},
        ],
        "tax_amount": 5000.0,
        "shipping_fee": 10000.0,
    }
    customer_row = map_order_to_customer(order)
    # {'customer_name': 'Alice A.', 'customer_group': 'All Customer Groups', ...}

All outputs are JSON‑serializable.
"""

from __future__ import annotations

from typing import Any, Dict, List
import hashlib
import json
from datetime import datetime, timezone

__all__ = [
    "normalize_sku",
    "map_order_to_customer",
    "map_order_to_contact",
    "map_order_to_address",
    "map_order_items",
    "map_order_taxes",
    "map_escrow_to_fee_row",
    "map_tracking_status",
    "compute_payload_hash",
]


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def normalize_sku(s: str | None) -> str:
    """Normalize raw SKU / model identifiers.

    Rules:
      - None -> ""
      - Trim surrounding whitespace
      - Collapse internal whitespace to single space (then convert spaces to '-')
      - Uppercase final result

    This keeps comparisons consistent and supports idempotent item matching.
    """
    if not s:
        return ""
    cleaned = " ".join(s.strip().split())
    return cleaned.replace(" ", "-").upper()


def _get(obj: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    """Return first present non-empty-ish value among keys from obj.

    Empty string / None are skipped; 0 is considered valid.
    """
    for k in keys:
        if k in obj:
            val = obj.get(k)
            if val not in (None, ""):
                return val
    return default


# ---------------------------------------------------------------------------
# Customer / Contact / Address
# ---------------------------------------------------------------------------

def map_order_to_customer(order: Dict[str, Any]) -> Dict[str, Any]:
    """Build minimal Customer row dict.

    Fields:
      - customer_name: recipient name OR buyer_username OR fallback 'Shopee Buyer'
      - customer_group: default placeholder (TODO: external config)
      - territory: default placeholder (TODO: map from address country / region)
      - buyer_user_id: raw numeric/string id if available (for traceability)

    Deterministic: only reads provided order data.
    """
    addr = order.get("recipient_address") or {}
    name = _get(addr, "name", default="") or order.get("buyer_username") or "Shopee Buyer"
    return {
        "customer_name": str(name)[:140],
        "customer_group": "All Customer Groups",  # TODO: configurable default
        "territory": "All Territories",  # TODO: derive from country mapping
        "buyer_user_id": order.get("buyer_user_id") or order.get("buyer_id") or "",
    }


def map_order_to_contact(order: Dict[str, Any]) -> Dict[str, Any]:
    """Map order → Contact row fields.

    Splits name into first / last tokens (very naive; business refinement TBD).
    Absent fields default to empty string to simplify upsert diff checks.
    """
    addr = order.get("recipient_address") or {}
    raw_name = (addr.get("name") or order.get("buyer_username") or "").strip()
    first, last = (raw_name, "") if " " not in raw_name else (raw_name.split(" ", 1)[0], raw_name.split(" ", 1)[1])
    return {
        "first_name": first[:80],
        "last_name": last[:80],
        "email_id": order.get("buyer_email") or addr.get("email") or "",
        "mobile_no": addr.get("phone") or addr.get("mobile") or order.get("phone") or "",
    }


def map_order_to_address(order: Dict[str, Any], kind: str = "Shipping") -> Dict[str, Any]:
    """Map order → Address row.

    Args:
        order: Shopee order dict
        kind: 'Shipping' or 'Billing' etc.

    Returns dict with ERPNext Address fields.
    """
    addr = order.get("recipient_address") or {}
    line1 = _get(addr, "full_address", "address1", "address_line1", default="")
    line2 = _get(addr, "address2", "address_line2", default="")
    city = _get(addr, "city", "city_name", default="")
    state = _get(addr, "state", "state_name", "province", default="")
    pincode = _get(addr, "zipcode", "postal_code", "zip", default="")
    country = _get(addr, "country", "country_name", default="") or "Indonesia"  # TODO: locale mapping
    phone = _get(addr, "phone", "mobile", default="")
    title_base = addr.get("name") or order.get("buyer_username") or "Shopee"
    address_title = f"{title_base} - {kind}"[:140]
    return {
        "address_title": address_title,
        "address_line1": line1[:140] or "Unknown",
        "address_line2": line2[:140],
        "city": city[:140],
        "state": state[:140],
        "pincode": str(pincode)[:20],
        "country": country[:140],
        "phone": str(phone)[:40],
        "address_type": kind,
    }


# ---------------------------------------------------------------------------
# Items / Taxes / Fees
# ---------------------------------------------------------------------------

def map_order_items(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Shopee order items → ERPNext item child rows.

    Expected item sources (heuristics – Shopee variants differ by version):
      - SKU: one of (model_sku, item_sku, variation_sku, sku)
      - Name: prefer model_name / variation_name / item_name / product_name
      - Quantity: model_quantity_purchased / quantity / order_item_quantity / model_quantity
      - Rate: item_price / model_original_price / order_item_price / price

    Output per line keys:
      ``item_code, item_name, qty, uom, rate, amount, shopee_sku, variation_name``

    ``item_code`` is set to normalized SKU if available else item_name slug fallback.
    """
    items = order.get("items") or order.get("order_items") or []
    out: List[Dict[str, Any]] = []
    for idx, it in enumerate(items):
        sku_raw = _get(it, "model_sku", "item_sku", "variation_sku", "sku", default="")
        sku_norm = normalize_sku(sku_raw)
        variation_name = _get(it, "model_name", "variation_name", "item_variant", default="")
        name = _get(it, "item_name", "product_name", "name", default=variation_name or sku_norm or f"Item {idx+1}")
        qty = _get(it, "model_quantity_purchased", "quantity", "order_item_quantity", "model_quantity", default=1) or 1
        try:
            qty = float(qty)
        except Exception:
            qty = 1.0
        rate = _get(it, "item_price", "model_original_price", "order_item_price", "price", default=0) or 0
        try:
            rate_f = float(rate)
        except Exception:
            rate_f = 0.0
        amount = qty * rate_f
        # Item code heuristic: prefer normalized SKU; else uppercase slug of name
        item_code = sku_norm or normalize_sku(name)
        out.append(
            {
                "item_code": item_code[:140],
                "item_name": str(name)[:140],
                "qty": qty,
                "uom": "Nos",  # TODO: dynamic UOM mapping
                "rate": rate_f,
                "amount": amount,
                "shopee_sku": sku_norm,
                "variation_name": variation_name[:140],
            }
        )
    return out


def map_order_taxes(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive generic tax/charge rows from order monetary fields.

    Heuristics (only create rows where value is non-zero):
      - tax_amount -> account 'Shopee Tax' (charge_type = On Net Total)
      - shipping_fee (or estimated_shipping_fee) -> 'Shopee Shipping'

    Rate is left at 0 (unknown) and ``tax_amount`` stores absolute monetary value.
    Description includes original key for traceability.
    """
    rows: List[Dict[str, Any]] = []
    monetary_fields = {
        "tax_amount": "Shopee Tax",
        "shipping_fee": "Shopee Shipping",
        "estimated_shipping_fee": "Shopee Shipping",  # fallback if shipping_fee absent
    }
    for key, account in monetary_fields.items():
        val = order.get(key)
        try:
            val_f = float(val)
        except Exception:
            continue
        if not val_f:
            continue
        rows.append(
            {
                "charge_type": "On Net Total",
                "account_head": account,  # TODO: map to real GL account
                "rate": 0.0,
                "tax_amount": val_f,
                "description": f"{account} ({key})",
            }
        )
    return rows


def map_escrow_to_fee_row(escrow: Dict[str, Any], fee_account: str = "Total Fee Shopee") -> Dict[str, Any]:
    """Aggregate escrow fee components into a single negative fee item row.

    Accepted fee component keys (any present are summed):
      - total_fee (if Shopee already supplies composite)
      - commission_fee, service_fee, payment_fee, transaction_fee, logistics_fee, voucher_fee

    Returns item dict::

        {
          "item_code": "",
          "item_name": "Total Fee Shopee",
          "qty": 1,
          "uom": "Nos",
          "rate": -<abs(sum)>,
          "amount": -<abs(sum)>,
          "income_account": fee_account
        }

    If no fee values found, amount/rate will be 0 (still deterministic).
    """
    fee_keys = [
        "total_fee",
        "commission_fee",
        "service_fee",
        "payment_fee",
        "transaction_fee",
        "logistics_fee",
        "voucher_fee",
    ]
    total = 0.0
    found_any = False
    for k in fee_keys:
        if k in escrow and escrow.get(k) not in (None, ""):
            try:
                total += float(escrow.get(k) or 0)
                found_any = True
            except Exception:
                pass
    # Avoid double counting if total_fee present plus individual components.
    # Heuristic: if total_fee present we use ONLY that.
    if escrow.get("total_fee") not in (None, ""):
        try:
            total = float(escrow.get("total_fee"))
        except Exception:
            pass
    total_abs = abs(total) if found_any else 0.0
    negative = -total_abs
    return {
        "item_code": "",
        "item_name": "Total Fee Shopee",
        "qty": 1,
        "uom": "Nos",  # TODO: configurable
        "rate": negative,
        "amount": negative,
        "income_account": fee_account,  # TODO: map to expense/contra account
    }


# ---------------------------------------------------------------------------
# Logistics / Tracking
# ---------------------------------------------------------------------------

_DELIVERED_STATES = {"delivered", "completed", "success"}
_PICKUP_STATES = {"pending_pickup", "pickup_arranged", "picked_up", "ready_to_ship"}


def map_tracking_status(push_or_poll_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize logistics payload to a concise tracking status dict.

    Input payload keys are Shopee specific; we look at typical possibilities:
      - status / logistics_status / tracking_status
      - pickup_status / delivery_status (already normalized upstream)
      - time_of_delivery / delivered_time (epoch seconds) OR delivered_at ISO

    Output keys:
      - pickup_status (string)
      - delivery_status (string)
      - delivered_at (ISO 8601 UTC or empty string)
    """
    status = (
        push_or_poll_payload.get("logistics_status")
        or push_or_poll_payload.get("tracking_status")
        or push_or_poll_payload.get("status")
        or ""
    ).lower()
    pickup_status = push_or_poll_payload.get("pickup_status") or (status if status in _PICKUP_STATES else "")
    delivery_status = push_or_poll_payload.get("delivery_status") or (status if status not in _PICKUP_STATES else "")
    delivered_at = ""
    # Epoch second sources
    for key in ("time_of_delivery", "delivered_time", "complete_time"):
        if key in push_or_poll_payload and push_or_poll_payload.get(key):
            try:
                ts = int(push_or_poll_payload.get(key))
                delivered_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                break
            except Exception:  # pragma: no cover - defensive
                pass
    # Direct ISO string fallback
    if not delivered_at:
        iso_candidate = push_or_poll_payload.get("delivered_at")
        if isinstance(iso_candidate, str):
            delivered_at = iso_candidate
    # Infer delivered if status enumerated and no explicit timestamp
    if not delivered_at and status in _DELIVERED_STATES:
        # Leave empty; upstream may set precise time later
        delivered_at = ""
    return {
        "pickup_status": str(pickup_status),
        "delivery_status": str(delivery_status),
        "delivered_at": delivered_at,
    }


# ---------------------------------------------------------------------------
# Generic hashing helper
# ---------------------------------------------------------------------------

def compute_payload_hash(payload: Dict[str, Any] | str) -> str:
    """Return SHA1 hex digest of payload (stable ordering for dict input).

    Args:
        payload: dict or raw string

    For dict input uses ``json.dumps(sort_keys=True, separators=(",", ":"))``
    to ensure deterministic byte representation (no whitespace differences).
    A SHA1 (non-cryptographic here) is sufficient for idempotency / change
    detection, not for security.
    """
    if isinstance(payload, dict):
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    else:
        data = str(payload)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()  # noqa: S324 (idempotency/int checksum)

