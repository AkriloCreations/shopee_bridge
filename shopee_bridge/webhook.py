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

def _safe_flt(v, d=0.0):
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

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
        raw_body = frappe.request.get_data() or b""
        headers = dict(frappe.request.headers or {})
        
        # Fix 417 error by handling Expect header
        frappe.local.response.headers = frappe.local.response.headers or {}
        frappe.local.response.headers["Expect"] = ""
        
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
    
def verify_webhook_signature(url: str, raw_body: bytes, headers: dict, partner_key: str) -> bool:
    """
    Shopee Webhook Signature Verification (Push Mechanism v2)
    Docs: https://open.shopee.com
    - Signature = HMAC-SHA256(partner_key, url + '|' + request_body).hexdigest()
    - Shopee sends signature in 'Authorization' header.
    """
    try:
        # Ambil signature dari header
        incoming_sig = (
            headers.get("Authorization")
            or headers.get("authorization")
            or ""
        ).strip()

        if not incoming_sig:
            frappe.logger().error("[Shopee Webhook] No signature header found")
            frappe.logger().info(f"[Shopee Webhook Debug] Headers available: {list(headers.keys())}")
            return False

        # Base string = url|raw_body (raw_body harus persis sama dengan yang Shopee kirim)
        body_str = raw_body.decode("utf-8")
        base_string = f"{url}|{body_str}"

        # Hitung HMAC-SHA256 pakai partner_key
        digest = hmac.new(
            partner_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        # Debug log untuk perbandingan
        frappe.logger().info(
            f"[Shopee Webhook Debug] url={url}, len={len(raw_body)}, "
            f"incoming={incoming_sig[:16]}..., calc={digest[:16]}..."
        )

        if hmac.compare_digest(incoming_sig, digest):
            frappe.logger().info("[Shopee Webhook] ✓ Signature verified successfully")
            return True
        else:
            frappe.logger().warning("[Shopee Webhook] ✗ Invalid signature")
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
    """Process webhook event based on type"""
    event = data.get("event", "").strip().lower()
    order_sn = data.get("order_sn", "")
    
    frappe.logger().info(f"[Shopee Webhook] Processing event: {event} for order: {order_sn}")
    
    # Route to specific handlers
    if event == "order_status_update":
        return handle_order_status_update(data)
    elif event in ["payment_update", "escrow_settled", "payout"]:
        return handle_payment_update(data)
    elif event == "order_created":
        return handle_order_created(data)
    else:
        frappe.logger().info(f"[Shopee Webhook] Unhandled event type: {event}")
        return {
            "success": True,
            "message": f"Event '{event}' logged but not processed",
            "event_type": event,
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
    """
    Normalisasi berbagai bentuk payload escrow Shopee (flat vs. response.order_income).
    Mengembalikan dict dengan key yang dipakai kode kita saat bikin Payment Entry.
    """
    # payload bisa: {"response": {...}} atau langsung {...}
    root = (payload or {}).get("response") or (payload or {})
    oi = root.get("order_income") or {}
    bpi = root.get("buyer_payment_info") or {}

    # Net amount yg kita terima:
    # - prioritas payout_amount (kalau Shopee sudah settle), 
    # - fallback escrow_amount_after_adjustment, 
    # - lalu escrow_amount.
    payout_amount = flt(root.get("payout_amount") or oi.get("payout_amount"))
    escrow_amount = flt(
        oi.get("escrow_amount_after_adjustment")
        or oi.get("escrow_amount")
        or root.get("escrow_amount")
    )
    net_amount = payout_amount or escrow_amount

    # Fee yang jadi beban seller:
    commission_fee = flt(oi.get("commission_fee"))
    service_fee = flt(oi.get("service_fee"))
    # Shopee sering memecah fee transaksi:
    seller_txn_fee = flt(oi.get("seller_transaction_fee"))
    cc_txn_fee = flt(oi.get("credit_card_transaction_fee"))

    # Proteksi & ongkir selisih:
    protection_fee = flt(oi.get("delivery_seller_protection_fee_premium_amount"))
    # reverse_shipping_fee = biaya pengembalian ke seller; shopee_shipping_rebate = reimbursenya
    ship_diff = flt(oi.get("reverse_shipping_fee")) - flt(oi.get("shopee_shipping_rebate"))

    # Voucher yang jadi beban seller (bukan voucher Shopee)
    voucher_seller = flt(oi.get("voucher_from_seller"))
    # coin cashback yang jadi beban seller → biasanya 0; kalau mau, ambil dari oi.get("coins")
    coin_cash_back = flt(oi.get("coins"))  # treat as seller-side if policy kamu begitu
    voucher_code_seller = 0.0  # tidak ada di payload contoh

    # Timestamp untuk posting
    payout_time = root.get("payout_time") or root.get("update_time")

    normalized = {
        "net_amount": net_amount,                       # ← inilah yang dipakai utk PE.paid_amount
        "escrow_amount": escrow_amount,
        "payout_amount": payout_amount,
        "commission_fee": commission_fee,
        "service_fee": service_fee + seller_txn_fee + cc_txn_fee,  # gabungkan fee transaksi ke service
        "shipping_seller_protection_fee_amount": protection_fee,
        "shipping_fee_difference": ship_diff,
        "voucher_seller": voucher_seller,
        "coin_cash_back": coin_cash_back,
        "voucher_code_seller": voucher_code_seller,
        "payout_time": payout_time,
    }
    return normalized


def create_payment_entry_from_shopee(
    si_name: str,
    escrow: dict,
    net_amount: float,
    order_sn: str,
    posting_ts: int | None = None,
    enqueue: bool = False
) -> str | None:
    """
    Create Payment Entry for Shopee escrow settlement (support payload baru dengan response.order_income).
    """

    # === NEW: normalisasi payload ===
    norm = _normalize_escrow_payload(escrow or {})
    # override net_amount & posting_ts pakai hasil normalisasi kalau ada
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

        # === NEW: anti-duplikat Payment Entry untuk SI yang sama ===
        pe_exists = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si.name}
        )
        if pe_exists:
            frappe.logger().info(f"[Shopee] Skip PE; reference already exists for SI {si.name}")
            return frappe.db.get_value("Payment Entry Reference", pe_exists, "parent")

        # Import helpers…
        from .api import _get_or_create_account, _get_or_create_mode_of_payment, _insert_submit_with_retry

        paid_from = si.debit_to
        paid_to = _get_or_create_account("Shopee (Escrow)", "Bank")
        mop = _get_or_create_mode_of_payment("Shopee")

        # === NEW: gunakan nilai fee dari 'norm' (sudah bersih) ===
        fees = {
            "commission": flt(norm.get("commission_fee")),
            "service": flt(norm.get("service_fee")),
            "protection": flt(norm.get("shipping_seller_protection_fee_amount")),
            "shipdiff": flt(norm.get("shipping_fee_difference")),
            "voucher": flt(norm.get("voucher_seller")) + flt(norm.get("coin_cash_back")) + flt(norm.get("voucher_code_seller")),
        }

        fee_accounts = {
            "commission": _get_or_create_account("Komisi Shopee", "Expense Account"),
            "service": _get_or_create_account("Biaya Layanan Shopee", "Expense Account"),
            "protection": _get_or_create_account("Proteksi Pengiriman Shopee", "Expense Account"),
            "shipdiff": _get_or_create_account("Selisih Ongkir Shopee", "Expense Account"),
            "voucher": _get_or_create_account("Voucher Shopee", "Expense Account"),
        }

        posting_date = _date_iso_from_epoch(posting_ts)

        pe = frappe.new_doc("Payment Entry")
        pe.company = si.company
        pe.payment_type = "Receive"
        pe.mode_of_payment = mop
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.posting_date = posting_date
        pe.reference_no = order_sn
        pe.paid_from = paid_from
        pe.paid_to = paid_to
        pe.paid_amount = flt(net)
        pe.received_amount = flt(net)

        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = flt(net)  # ⬅️ allocated = net (fee ditaruh di deductions)

        for fee_type, amount in fees.items():
            if flt(amount) > 0:
                row = pe.append("deductions", {})
                row.account = fee_accounts[fee_type]
                row.amount = flt(amount)

        pe = _insert_submit_with_retry(pe)
        frappe.logger().info(f"[Shopee] Payment Entry {pe.name} created for SI {si.name}")
        return pe.name

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Payment Entry Error")
        raise


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
        # Extract order info
        order_data = webhook_data.get('data', {}) if webhook_data else {}
        
        log_doc = frappe.get_doc({
            "doctype": "Shopee Webhook Log",
            "timestamp": frappe.utils.now(),
            "order_sn": order_data.get('ordersn', '') or webhook_data.get('order_sn', '') if webhook_data else '',
            "shop_id": str(webhook_data.get('shop_id', '')) if webhook_data else '',
            "status": order_data.get('status', ''),
            "event_type": webhook_data.get('event', ''),   # <<-- fix di sini
            "raw_data": json.dumps(webhook_data, indent=2) if webhook_data else str(raw_body),
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
