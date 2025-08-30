# shopee_bridge/webhook.py

import frappe # pyright: ignore[reportMissingImports]
import hmac
import hashlib
import base64
import json
from typing import Dict, Any, Optional
from frappe.utils import flt, nowdate # pyright: ignore[reportMissingImports]

def _settings():
    return frappe.get_single("Shopee Settings")

def _extract_push_info(payload: dict) -> dict:
    """Ekstrak field penting dari format Shopee Push (root: code, shop_id, timestamp; data: ordersn, status, update_time)."""
    d = (payload or {}).get("data") or {}
    return {
        "order_sn": d.get("ordersn") or payload.get("order_sn") or "",
        "status": d.get("status") or payload.get("status") or "",
        "update_time": d.get("update_time") or payload.get("update_time"),
        "code": payload.get("code"),
        "shop_id": payload.get("shop_id"),
        "timestamp": payload.get("timestamp"),
        "completed_scenario": d.get("completed_scenario"),
    }

def _safe_flt(v, d=0.0):
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d
def _get_live_push_key() -> str:
    s = _settings()
    # sesuaikan nama field di Doctype Settings kamu
    return (getattr(s, "live_push_partner_key", "") or getattr(s, "webhook_key", "") or "").strip()

def _consteq(a: bytes, b: bytes) -> bool:
    try:
        import hmac
        return hmac.compare_digest(a, b)
    except Exception:
        return False

def _decode_sig_variants(sig_str: str) -> list[bytes]:
    import base64, binascii
    s = sig_str.strip()
    low = s.lower()
    for pref in ("sha256=", "hmac=", "signature="):
        if low.startswith(pref):
            s = s[len(pref):].strip()
            break
    outs = []
    # hex
    try: outs.append(binascii.unhexlify(s))
    except Exception: pass
    # base64
    try: outs.append(base64.b64decode(s, validate=False))
    except Exception: pass
    # base64url (+ padding)
    try:
        pad = '=' * (-len(s) % 4)
        outs.append(base64.urlsafe_b64decode(s + pad))
    except Exception: pass
    return [x for x in outs if x]

def _get_default_cost_center_for_si(si) -> str:
    """Cari Cost Center default (item SI → Accounts Settings → leaf company)."""
    for it in getattr(si, "items", []):
        if getattr(it, "cost_center", None):
            return it.cost_center
    cc = frappe.db.get_single_value("Accounts Settings", "default_cost_center")
    if cc: return cc
    cc = frappe.db.get_value("Cost Center", {"company": si.company, "is_group": 0}, "name")
    if cc: return cc
    frappe.throw("No Cost Center found. Set default in Accounts Settings or on Sales Invoice items.")

def _safe_int(v, d=0):
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

def _date_iso_from_epoch(ts: int | None) -> str:
    """Epoch detik → 'YYYY-MM-DD' (UTC baseline, cukup untuk tanggal dokumen)."""
    from datetime import datetime, timezone
    if not ts:
        return frappe.utils.nowdate()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()

@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "OPTIONS"])
def shopee_webhook():
    """Main Shopee webhook handler"""
    import time
    start_time = time.time()
    
    try:
        # Handle CORS preflight
        if frappe.request.method == "OPTIONS":
            frappe.local.response.headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Shopee-Signature"
            }
            return {"success": True, "message": "CORS handled"}
        
        # Get request data
        raw_body = frappe.request.get_data(as_text=False) or b""
        headers = dict(frappe.request.headers or {})
        
        # Parse webhook data
        webhook_data = None
        if raw_body:
            try:
                body_text = raw_body.decode('utf-8')
                webhook_data = json.loads(body_text)
            except Exception as e:
                processing_time = (time.time() - start_time) * 1000
                result = {"success": False, "error": "invalid_json", "details": str(e)}
                log_webhook_activity(None, headers, raw_body, result, processing_time)
                return result
        
        # Signature verification
        partner_key = getattr(_settings(), "partner_key", "").strip()
        url_path = frappe.request.path  # sesuai Shopee config
        if not verify_webhook_signature(url_path, raw_body, headers, partner_key):
            processing_time = (time.time() - start_time) * 1000
            result = {"success": False, "error": "invalid_signature"}
            log_webhook_activity(webhook_data, headers, raw_body, result, processing_time)
            return result
        
        # Process webhook event
        if webhook_data and isinstance(webhook_data, dict):
            result = process_webhook_event(webhook_data)
        else:
            result = {
                "success": True,
                "message": "Webhook received but no data to process",
                "timestamp": frappe.utils.now()
            }
        
        # Log activity
        processing_time = (time.time() - start_time) * 1000
        log_webhook_activity(webhook_data, headers, raw_body, result, processing_time)
        
        return result
        
    except Exception as e:
        processing_time = (time.time() - start_time) * 1000
        result = {"success": False, "error": "server_error", "details": str(e)}
        log_webhook_activity(webhook_data if 'webhook_data' in locals() else None, 
                           headers if 'headers' in locals() else {}, 
                           raw_body if 'raw_body' in locals() else b"", 
                           result, processing_time)
        
        frappe.log_error(frappe.get_traceback(), "Shopee Webhook Critical Error")
        return result
    
def verify_webhook_signature(url: str, raw_body: bytes, headers: dict, partner_key_unused: str) -> bool:
    """
    Shopee Push v2:
      Signature = HMAC-SHA256( LIVE_PUSH_PARTNER_KEY, RAW_BODY )
      Header   = Authorization  (umumnya hex lowercase; antisipasi base64/base64url)
    NOTE: 'url' TIDAK dipakai dalam perhitungan signature untuk push.
    """
    try:
        incoming = (headers.get("Authorization") or headers.get("authorization") or "").strip()
        if not incoming:
            frappe.logger().error("[Shopee Webhook] No signature header found")
            return False

        key = _get_live_push_key()
        if not key:
            frappe.logger().error("[Shopee Webhook] Live Push Partner Key not configured")
            return False

        # coba body apa adanya, dan varian rstrip CR/LF
        bodies = [raw_body]
        if raw_body.endswith((b"\r", b"\n")):
            bodies.append(raw_body.rstrip(b"\r\n"))

        incoming_candidates = _decode_sig_variants(incoming)

        for b in bodies:
            digest = hmac.new(key.encode("utf-8"), b, hashlib.sha256).digest()

            # match semua kandidat (hex/base64/base64url)
            for inc in incoming_candidates:
                if inc and _consteq(digest, inc):
                    frappe.logger().info("[Shopee Webhook] ✓ Signature verified")
                    return True

            # juga coba perbandingan langsung hex lowercase (format paling sering)
            if _consteq(digest.hex().encode(), incoming.lower().encode()):
                frappe.logger().info("[Shopee Webhook] ✓ Signature verified (hex)")
                return True

        # debug singkat
        try:
            calc_hex = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
            calc_hex_trim = hmac.new(key.encode("utf-8"), raw_body.rstrip(b"\r\n"), hashlib.sha256).hexdigest()
            frappe.logger().warning(
                f"[Shopee Webhook] ✗ Invalid signature; got={incoming[:16]}..., "
                f"calc={calc_hex[:16]}..., calc_trim={calc_hex_trim[:16]}..., len={len(raw_body)}"
            )
        except Exception:
            pass

        return False

    except Exception as e:
        frappe.logger().error(f"[Shopee Webhook] Signature verification error: {e}")
        return False

def _normalize_signature(sig_raw: str) -> str:
    """Remove common prefixes from signature"""
    sig = sig_raw.strip()
    lower_sig = sig.lower()
    
    # Remove common prefixes
    prefixes = ["sha256=", "hmac=", "signature=", "sha256 ", "hmac ", "signature "]
    for prefix in prefixes:
        if lower_sig.startswith(prefix):
            sig = sig[len(prefix):].strip()
            break
    
    return sig


def _verify_with_key(signature: str, raw_body: bytes, key: str, key_name: str) -> bool:
    """Try verification with a specific key using multiple formats"""
    key_bytes = key.encode('utf-8')
    
    # Calculate HMAC
    calculated = hmac.new(key_bytes, raw_body, hashlib.sha256).digest()
    
    # Try different formats
    formats_to_try = [
        ("hex", calculated.hex()),
        ("hex_lower", calculated.hex().lower()),  
        ("hex_upper", calculated.hex().upper()),
        ("base64", base64.b64encode(calculated).decode().strip()),
        ("base64_no_padding", base64.b64encode(calculated).decode().strip().rstrip("=")),
        ("base64url", base64.urlsafe_b64encode(calculated).decode().strip()),
        ("base64url_no_padding", base64.urlsafe_b64encode(calculated).decode().strip().rstrip("=")),
    ]
    
    for format_name, calculated_sig in formats_to_try:
        if hmac.compare_digest(signature, calculated_sig):
            frappe.logger().info(f"[Webhook] Signature verified using {key_name} ({format_name})")
            return True
    
    # Try with stripped newlines (some webhook senders add these)
    if raw_body.endswith(b'\n') or raw_body.endswith(b'\r\n'):
        stripped_body = raw_body.rstrip(b'\r\n')
        calculated_stripped = hmac.new(key_bytes, stripped_body, hashlib.sha256).digest()
        
        for format_name, _ in formats_to_try:
            if format_name == "hex":
                calc_sig = calculated_stripped.hex()
            elif format_name == "hex_lower":
                calc_sig = calculated_stripped.hex().lower()
            elif format_name == "hex_upper":
                calc_sig = calculated_stripped.hex().upper()
            elif format_name == "base64":
                calc_sig = base64.b64encode(calculated_stripped).decode().strip()
            elif format_name == "base64_no_padding":
                calc_sig = base64.b64encode(calculated_stripped).decode().strip().rstrip("=")
            elif format_name == "base64url":
                calc_sig = base64.urlsafe_b64encode(calculated_stripped).decode().strip()
            elif format_name == "base64url_no_padding":
                calc_sig = base64.urlsafe_b64encode(calculated_stripped).decode().strip().rstrip("=")
            
            if hmac.compare_digest(signature, calc_sig):
                frappe.logger().info(f"[Webhook] Signature verified using {key_name} ({format_name}, stripped)")
                return True
    
    return False


def process_webhook_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """Process webhook event untuk 2 format:
       1) Shopee Push v2 (tanpa 'event', pakai root+data)
       2) Event-based lama (punya 'event', 'order_sn')
    """
    # 1) Format Shopee Push v2 (punya root 'data' dan 'ordersn' di dalamnya)
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and "ordersn" in data["data"]:
        info = _extract_push_info(data)
        frappe.logger().info(
            f"[Shopee Push] ordersn={info['order_sn']} status={info['status']} code={info['code']} shop_id={info['shop_id']}"
        )

        # Di sini kamu bisa memetakan status push -> handler spesifik kalau mau
        # Untuk sekarang: log dulu sebagai 'push_logged'
        return {
            "success": True,
            "message": "push_logged",
            "order_sn": info["order_sn"],
            "status": info["status"],
            "code": info["code"],
            "shop_id": info["shop_id"],
            "update_time": info["update_time"],
            "timestamp": info["timestamp"],
            "completed_scenario": info["completed_scenario"],
        }

    # 2) Fallback: event-based payload (seperti versi lama kamu)
    event = (data.get("event") or "").strip().lower()
    order_sn = data.get("order_sn", "")

    frappe.logger().info(f"[Shopee Webhook] Processing event: {event or '—'} for order: {order_sn or '—'}")

    if event == "order_status_update":
        return handle_order_status_update(data)
    elif event in ["payment_update", "escrow_settled", "payout"]:
        return handle_payment_update(data)
    elif event == "order_created":
        return handle_order_created(data)
    else:
        return {
            "success": True,
            "message": f"Event '{event or 'unknown'}' logged",
            "event_type": event or "unknown",
            "order_sn": order_sn
        }

def handle_order_status_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle order status update events"""
    order_sn = data.get("order_sn", "")
    order_status = data.get("order_status", "")
    
    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}
    
    frappe.logger().info(f"[Shopee] Order status update: {order_sn} -> {order_status}")
    
    # Check if order exists in our system
    so_exists = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    si_exists = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    result = {
        "success": True,
        "message": "order_status_processed",
        "order_sn": order_sn,
        "new_status": order_status,
        "found_sales_order": bool(so_exists),
        "found_sales_invoice": bool(si_exists)
    }
    
    # TODO: Add status update logic here if needed
    if so_exists or si_exists:
        frappe.logger().info(f"[Shopee] Order {order_sn} found in system - status updated to {order_status}")
        result["action"] = "status_updated_in_system"
    else:
        frappe.logger().info(f"[Shopee] Order {order_sn} not found in system - status logged only")
        result["action"] = "status_logged_only"
    
    return result


def handle_payment_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle payment/escrow/payout events"""
    order_sn = data.get("order_sn", "")
    event_type = data.get("event", "")
    
    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}
    
    # Find related Sales Invoice
    si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
    
    if not si_name:
        frappe.logger().info(f"[Shopee] No Sales Invoice found for order {order_sn}")
        return {
            "success": True,
            "message": "no_invoice_found",
            "order_sn": order_sn,
            "event_type": event_type
        }
    
    # Extract payment amounts
    escrow_amount = _safe_flt(data.get("escrow_amount"))
    payout_amount = _safe_flt(data.get("payout_amount"))
    net_amount = escrow_amount or payout_amount
    
    # Extract fees
    commission_fee = _safe_flt(data.get("commission_fee"))
    service_fee = _safe_flt(data.get("service_fee"))
    
    frappe.logger().info(f"[Shopee] Payment event for SI {si_name}")
    frappe.logger().info(f"[Shopee] Net amount: {net_amount}, Commission: {commission_fee}, Service: {service_fee}")
    
    result = {
        "success": True,
        "message": "payment_event_processed",
        "event_type": event_type,
        "order_sn": order_sn,
        "sales_invoice": si_name,
        "amounts": {
            "net_amount": net_amount,
            "escrow_amount": escrow_amount,
            "payout_amount": payout_amount,
            "commission_fee": commission_fee,
            "service_fee": service_fee
        }
    }
    
    # Create Payment Entry if needed
    if net_amount > 0:
        try:
            posting_ts = _safe_int(data.get("payout_time") or data.get("update_time"))
            
            # Import payment function from api.py or finance.py
            pe_job = create_payment_entry_from_shopee(
                si_name=si_name,
                escrow=data,
                net_amount=net_amount,
                order_sn=order_sn,
                posting_ts=posting_ts,
                enqueue=True
            )
            
            result["payment_entry_job"] = pe_job
            result["note"] = "Payment Entry creation enqueued"
            frappe.logger().info(f"[Shopee] Payment Entry job created for {si_name}")
            
        except Exception as e:
            result["payment_error"] = str(e)
            frappe.log_error(f"Payment Entry creation failed for {order_sn}: {str(e)}", "Shopee Payment")
    
    return result


def handle_order_created(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle new order creation events"""
    order_sn = data.get("order_sn", "")
    
    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}
    
    # Check if order already exists
    existing_so = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    existing_si = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    if existing_so or existing_si:
        frappe.logger().info(f"[Shopee] Order {order_sn} already exists in system")
        return {
            "success": True,
            "message": "order_already_exists",
            "order_sn": order_sn,
            "existing_so": bool(existing_so),
            "existing_si": bool(existing_si)
        }
    
    frappe.logger().info(f"[Shopee] New order creation webhook: {order_sn}")
    
    result = {
        "success": True,
        "message": "new_order_logged",
        "order_sn": order_sn,
        "note": "Order processing can be implemented here"
    }
    
    # TODO: Implement order creation
    try:
        # Import from api.py
        from .api import _process_order
        
        job_id = frappe.enqueue(
            "_process_order", 
            queue="short",
            timeout=300,
            order_sn=order_sn
        )
        result["order_processing_job"] = job_id
        result["action"] = "order_processing_enqueued"
        
    except Exception as e:
        result["order_error"] = str(e)
        frappe.logger().warning(f"[Shopee] Order processing failed: {str(e)}")
    
    return result

def _normalize_escrow_payload(payload: dict) -> dict:
    """Normalisasi payload escrow Shopee (flat / response.order_income)."""
    root = (payload or {}).get("response") or (payload or {})
    oi = root.get("order_income") or {}

    payout_amount = flt(root.get("payout_amount") or oi.get("payout_amount"))
    escrow_amount = flt(
        oi.get("escrow_amount_after_adjustment")
        or oi.get("escrow_amount")
        or root.get("escrow_amount")
    )
    net_amount = payout_amount or escrow_amount

    data = {
        "net_amount": net_amount,
        "escrow_amount": escrow_amount,
        "payout_amount": payout_amount,
        "commission_fee": flt(oi.get("commission_fee")),
        "service_fee": flt(oi.get("service_fee")) + flt(oi.get("seller_transaction_fee")) + flt(oi.get("credit_card_transaction_fee")),
        "shipping_seller_protection_fee_amount": flt(oi.get("delivery_seller_protection_fee_premium_amount")),
        # shipdiff ± (reverse shipping - rebate)
        "shipping_fee_difference": flt(oi.get("reverse_shipping_fee")) - flt(oi.get("shopee_shipping_rebate")),
        "voucher_seller": flt(oi.get("voucher_from_seller")),
        "coin_cash_back": flt(oi.get("coins")),
        "voucher_code_seller": 0.0,
        "payout_time": root.get("payout_time") or root.get("update_time"),
    }
    return data

def create_payment_entry_from_shopee(
    si_name: str,
    escrow: dict,
    net_amount: float,
    order_sn: str,
    posting_ts: int | None = None,
    enqueue: bool = False
) -> str | None:
    """
    Buat Payment Entry settlement Shopee:
    - allocate = NET + total_deductions (== GROSS after rounding) → SI = Paid
    - deductions = (GROSS - NET) (breakdown dari payload + satu baris 'Selisih Biaya Shopee' bila perlu)
    - isi Cost Center (header & deductions)
    - Reference Date wajib
    - anti-duplikat PE per SI
    - semua angka dibulatkan ke presisi PE → Difference Amount = 0
    """
    norm = _normalize_escrow_payload(escrow or {})
    net = flt(norm.get("net_amount") or net_amount)
    posting_ts = posting_ts or norm.get("payout_time")

    if enqueue:
        return frappe.enqueue(
            "shopee_bridge.webhook.create_payment_entry_from_shopee",
            queue="short",
            job_name=f"PE Shopee {order_sn}",
            si_name=si_name,
            escrow=escrow,
            net_amount=float(net),
            order_sn=order_sn,
            posting_ts=posting_ts,
            enqueue=False
        )

    try:
        si = frappe.get_doc("Sales Invoice", si_name)
        if si.docstatus != 1:
            frappe.throw(f"Sales Invoice {si.name} not submitted")

        # Anti-duplikat PE untuk SI ini
        pe_ref = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si.name}
        )
        if pe_ref:
            return frappe.db.get_value("Payment Entry Reference", pe_ref, "parent")

        # Helpers
        from .api import _get_or_create_mode_of_payment, _insert_submit_with_retry

        paid_from = si.debit_to
        paid_to = _get_or_create_bank_account("Shopee (Escrow)")
        mop = _get_or_create_mode_of_payment("Shopee")

        # Gross vs Net
        gross = flt(si.grand_total)
        expected_total_fees_raw = max(0, gross - net)

        # Breakdown fees dari payload (raw, belum dibulatkan)
        fees_raw = {
            "commission": flt(norm.get("commission_fee")),
            "service": flt(norm.get("service_fee")),
            "protection": flt(norm.get("shipping_seller_protection_fee_amount")),
            "shipdiff": flt(norm.get("shipping_fee_difference")),
            "voucher": flt(norm.get("voucher_seller")) + flt(norm.get("coin_cash_back")) + flt(norm.get("voucher_code_seller")),
        }
        payload_total_fees_raw = sum(v for v in fees_raw.values() if v > 0)
        diff_fee_raw = expected_total_fees_raw - payload_total_fees_raw  # bisa +/- karena rounding/kelengkapan payload

        fee_accounts = {
            "commission": _get_or_create_expense_account("Komisi Shopee"),
            "service": _get_or_create_expense_account("Biaya Layanan Shopee"),
            "protection": _get_or_create_expense_account("Proteksi Pengiriman Shopee"),
            "shipdiff": _get_or_create_expense_account("Selisih Ongkir Shopee"),
            "voucher": _get_or_create_expense_account("Voucher Shopee"),
        }
        diff_account = _get_or_create_expense_account("Selisih Biaya Shopee")

        posting_date = _date_iso_from_epoch(posting_ts)
        ref_date = posting_date
        default_cc = _get_default_cost_center_for_si(si)

        pe = frappe.new_doc("Payment Entry")
        pe.company = si.company
        pe.payment_type = "Receive"
        pe.mode_of_payment = mop
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.posting_date = posting_date
        pe.reference_no = order_sn
        pe.reference_date = ref_date
        pe.cost_center = default_cc

        precision = _pe_precision(pe)

        # Uang yang diterima (ke escrow) = NET (dibulatkan ke presisi PE)
        pe.paid_from = paid_from
        pe.paid_to = paid_to
        pe.paid_amount = flt(net, precision)
        pe.received_amount = flt(net, precision)

        # Build deductions dengan presisi PE
        total_deductions = 0.0
        for k, v in fees_raw.items():
            v = flt(v, precision)
            if v > 0:
                row = pe.append("deductions", {})
                row.account = fee_accounts[k]
                row.amount = v
                row.cost_center = default_cc
                total_deductions += v

        # Tambahkan 1 baris selisih supaya balance pas di presisi PE
        diff_fee = flt(diff_fee_raw, precision)
        if abs(diff_fee) >= (1 / (10 ** max(precision, 0))):
            row = pe.append("deductions", {})
            row.account = diff_account
            row.amount = diff_fee
            row.cost_center = default_cc
            total_deductions += diff_fee

        # allocated_amount harus = paid_amount + total_deductions (dibulatkan) → sama dengan GROSS setelah rounding
        allocated = flt(pe.paid_amount + total_deductions, precision)

        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = allocated

        # (Opsional) Log kalau alokasi ≠ gross setelah rounding → hanya warning
        gross_r = flt(gross, precision)
        if allocated != gross_r:
            frappe.logger().warning(
                f"[Shopee PE] allocated({allocated}) != gross({gross_r}) @precision {precision} for SI {si.name}"
            )

        pe = _insert_submit_with_retry(pe)
        return pe.name

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Payment Entry Error")
        raise
def _get_or_create_expense_account(account_name: str) -> str:
    """Pastikan akun Expense untuk deductions ada & valid."""
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    cur = frappe.db.get_value("Company", company, "default_currency") or "IDR"

    acc_name = frappe.db.get_value("Account", {"company": company, "account_name": account_name}, "name")
    if acc_name:
        acc = frappe.get_doc("Account", acc_name)
        changed = False
        if acc.is_group: acc.is_group = 0; changed = True
        if acc.root_type != "Expense": acc.root_type = "Expense"; changed = True
        if acc.account_type != "Expense Account": acc.account_type = "Expense Account"; changed = True
        if getattr(acc, "account_currency", None) and acc.account_currency != cur:
            acc.account_currency = cur; changed = True
        if acc.disabled: acc.disabled = 0; changed = True
        if changed: acc.save(ignore_permissions=True)
        return acc.name

    parent = (
        frappe.db.get_value("Account", {"company": company, "account_name": "Indirect Expenses", "is_group": 1}, "name")
        or frappe.db.get_value("Account", {"company": company, "account_name": "Direct Expenses", "is_group": 1}, "name")
        or frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 1}, "name")
    )
    acc = frappe.get_doc({
        "doctype": "Account",
        "company": company,
        "account_name": account_name,
        "parent_account": parent,
        "is_group": 0,
        "root_type": "Expense",
        "account_type": "Expense Account",
        "account_currency": cur,
    })
    acc.insert(ignore_permissions=True)
    return acc.name

def _get_or_create_bank_account(account_name: str) -> str:
    """Pastikan akun escrow Bank ada & valid."""
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    cur = frappe.db.get_value("Company", company, "default_currency") or "IDR"

    acc_name = frappe.db.get_value("Account", {"company": company, "account_name": account_name}, "name")
    if acc_name:
        acc = frappe.get_doc("Account", acc_name)
        changed = False
        if acc.is_group: acc.is_group = 0; changed = True
        if acc.root_type != "Asset": acc.root_type = "Asset"; changed = True
        if acc.account_type != "Bank": acc.account_type = "Bank"; changed = True
        if getattr(acc, "account_currency", None) and acc.account_currency != cur:
            acc.account_currency = cur; changed = True
        if acc.disabled: acc.disabled = 0; changed = True
        if changed: acc.save(ignore_permissions=True)
        return acc.name

    parent = (
        frappe.db.get_value("Account", {"company": company, "account_type": "Bank", "is_group": 1}, "name")
        or frappe.db.get_value("Account", {"company": company, "root_type": "Asset", "is_group": 1}, "name")
    )
    acc = frappe.get_doc({
        "doctype": "Account",
        "company": company,
        "account_name": account_name,
        "parent_account": parent,
        "is_group": 0,
        "root_type": "Asset",
        "account_type": "Bank",
        "account_currency": cur,
    })
    acc.insert(ignore_permissions=True)
    return acc.name

# =============================================================================
# DEVELOPMENT & TEST FUNCTIONS
# =============================================================================

@frappe.whitelist()
def test_webhook():
    """Test webhook dengan sample data - bisa dipanggil dari Console"""
    try:
        # Sample Shopee webhook data
        test_events = [
            {
                "event": "payment_update",
                "order_sn": f"TEST-PAYMENT-{frappe.utils.now_datetime().timestamp():.0f}",
                "escrow_amount": 150000,
                "commission_fee": 15000,
                "service_fee": 5000,
                "update_time": int(frappe.utils.now_datetime().timestamp())
            },
            {
                "event": "order_status_update",
                "order_sn": f"TEST-STATUS-{frappe.utils.now_datetime().timestamp():.0f}",
                "order_status": "SHIPPED",
                "update_time": int(frappe.utils.now_datetime().timestamp())
            },
            {
                "event": "order_created",
                "order_sn": f"TEST-ORDER-{frappe.utils.now_datetime().timestamp():.0f}",
                "order_status": "TO_SHIP",
                "create_time": int(frappe.utils.now_datetime().timestamp())
            }
        ]
        
        results = []
        for test_data in test_events:
            frappe.logger().info(f"[Test Webhook] Testing event: {test_data['event']}")
            result = process_webhook_event(test_data)
            results.append({
                "test_data": test_data,
                "result": result
            })
        
        return {
            "success": True,
            "message": "Webhook testing completed",
            "test_results": results,
            "webhook_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.shopee_webhook",
            "note": "You can test this URL with curl or Postman"
        }
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Test Webhook Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def webhook_info():
    """Get webhook configuration info"""
    try:
        s = _settings()
        
        return {
            "success": True,
            "webhook_configuration": {
                "main_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.shopee_webhook",
                "test_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.test_webhook",
                "info_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.webhook_info"
            },
            "shopee_settings": {
                "environment": getattr(s, "environment", "Test"),
                "partner_id": getattr(s, "partner_id", ""),
                "shop_id": getattr(s, "shop_id", ""),
                "has_access_token": bool(getattr(s, "access_token", "")),
                "has_partner_key": bool(getattr(s, "partner_key", ""))
            },
            "development_notes": [
                "Signature verification currently bypassed in Test environment",
                "Use test_webhook() function to simulate webhook calls",
                "Check Error Log for webhook processing details",
                "Monitor logs with: docker logs -f erpnext_app | grep Webhook"
            ]
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def health_check():
    """Simple health check endpoint"""
    return {
        "success": True,
        "message": "Shopee webhook module is healthy",
        "timestamp": frappe.utils.now(),
        "url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.health_check"
    }

def log_webhook_activity(webhook_data, headers, raw_body, result, processing_time, source="Shopee Live"):
    """Log webhook activity to database"""
    try:
        order_data = webhook_data.get('data', {}) if webhook_data else {}
        event_type = ""
        if webhook_data:
            # event-based atau push (pakai status)
            event_type = (webhook_data.get('event') or "") or (order_data.get('status') or "")

        log_doc = frappe.get_doc({
            "doctype": "Shopee Webhook Log",
            "timestamp": frappe.utils.now(),
            "order_sn": (order_data.get('ordersn') or (webhook_data.get('order_sn') if webhook_data else "")) or "",
            "shop_id": str(webhook_data.get('shop_id', '')) if webhook_data else '',
            "status": order_data.get('status', ''),
            "event_type": event_type,
            "raw_data": json.dumps(webhook_data, indent=2) if webhook_data else (raw_body.decode(errors="replace") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)),
            "headers": json.dumps(headers, indent=2),
            "response_status": "Success" if result.get('success') else "Error",
            "error_message": result.get('error', '') if not result.get('success') else '',
            "processing_time": processing_time,
            "source": source,
            "ip_address": frappe.request.environ.get('REMOTE_ADDR', 'Unknown')
        })

        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        status_icon = "✅" if result.get('success') else "❌"
        print(f"{status_icon} Webhook: {log_doc.order_sn or 'No Order'} | "
              f"{log_doc.event_type or 'No Event'} | {processing_time:.1f}ms")

    except Exception as e:
        frappe.logger().error(f"Failed to log webhook activity: {str(e)}")

@frappe.whitelist(allow_guest=True)
def repair_shopee_payment_entries(limit: int = 200):
    """Perbaiki Payment Entry Shopee lama yang bikin SI 'Partly Paid'."""
    fixed, skipped, errors = 0, 0, 0

    sinvs = frappe.get_all(
        "Sales Invoice",
        filters={"docstatus": 1, "outstanding_amount": [">", 0], "custom_shopee_order_sn": ["!=", ""]},
        fields=["name", "grand_total", "outstanding_amount", "customer", "company", "custom_shopee_order_sn"],
        limit=limit,
        order_by="modified desc",
    )

    for si_row in sinvs:
        try:
            ref = frappe.get_all(
                "Payment Entry Reference",
                filters={"reference_doctype": "Sales Invoice", "reference_name": si_row.name},
                fields=["parent"],
                limit=1,
            )
            if not ref:
                skipped += 1
                continue

            pe = frappe.get_doc("Payment Entry", ref[0].parent)
            if pe.docstatus != 1:
                skipped += 1
                continue

            # kalau sudah allocate = gross, skip
            alloc = 0
            for r in pe.references:
                if r.reference_doctype == "Sales Invoice" and r.reference_name == si_row.name:
                    alloc = flt(r.allocated_amount); break
            if abs(alloc - flt(si_row.grand_total)) < 0.01:
                skipped += 1
                continue

            # cancel & amend
            pe.cancel()
            new_pe = frappe.copy_doc(pe)
            new_pe.amended_from = pe.name
            new_pe.docstatus = 0

            # set allocate = GROSS
            gross = flt(si_row.grand_total)
            net = flt(new_pe.paid_amount)

            for r in new_pe.references:
                if r.reference_doctype == "Sales Invoice" and r.reference_name == si_row.name:
                    r.allocated_amount = gross

            # pastikan deductions total = gross - net
            need = round(gross - net, 2)
            have = round(sum(flt(d.amount) for d in new_pe.deductions), 2)
            diff = round(need - have, 2)
            if abs(diff) >= 0.01:
                si_doc = frappe.get_doc("Sales Invoice", si_row.name)
                cc = _get_default_cost_center_for_si(si_doc)
                acc = _get_or_create_expense_account("Selisih Biaya Shopee")
                row = new_pe.append("deductions", {})
                row.account = acc
                row.amount = flt(diff)
                row.cost_center = cc

            if not new_pe.reference_date:
                new_pe.reference_date = new_pe.posting_date

            from .api import _insert_submit_with_retry
            _insert_submit_with_retry(new_pe)
            fixed += 1

        except Exception as e:
            frappe.log_error(f"Repair PE for {si_row.name} failed: {e}", "Shopee Repair PE")
            errors += 1

    return {"fixed": fixed, "skipped": skipped, "errors": errors}

def _pe_precision(pe) -> int:
    # presisi angka uang pada Payment Entry (ikut company/currency)
    try:
        return pe.precision("paid_amount") or 2
    except Exception:
        return 2

@frappe.whitelist(allow_guest=True, methods=["POST"])
def dbg_verify_signature():
    """
    Kirim body mentah + Authorization header, endpoint ini hanya mem-verifikasi signature
    TANPA memproses data. Gunakan untuk debug.
    """
    raw_body = frappe.request.get_data(as_text=False) or b""
    headers = dict(frappe.request.headers or {})
    ok = verify_webhook_signature(frappe.request.path, raw_body, headers, partner_key_unused="")
    return {"ok": bool(ok), "len": len(raw_body)}