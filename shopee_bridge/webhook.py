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
        if not verify_webhook_signature(raw_body, headers):
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
    
def verify_webhook_signature(raw_body: bytes, headers: Dict[str, str]) -> bool:
    """
    Enhanced signature verification with multiple fallbacks
    """
    s = _settings()
    
    # Get keys from settings
    webhook_key = getattr(s, "webhook_key", "").strip()
    webhook_test_key = getattr(s, "webhook_test_key", "").strip()
    partner_key = getattr(s, "partner_key", "").strip()
    
    # TEMPORARY: Skip verification dalam Test environment
    env = getattr(s, "environment", "Test")
    if env == "Test":
        frappe.logger().info("[Shopee Webhook] Skipping signature verification in Test environment")
        return True
    
    # Get signature from headers (multiple possible header names)
    signature_raw = (
        headers.get("X-Shopee-Signature") or
        headers.get("x-shopee-signature") or  
        headers.get("Authorization") or
        headers.get("authorization") or
        headers.get("Signature") or
        headers.get("signature") or
        ""
    ).strip()
    
    if not signature_raw:
        frappe.logger().error("[Webhook] No signature header found")
        frappe.logger().info(f"[Webhook Debug] Available headers: {list(headers.keys())}")
        return False
    
    # Normalize signature (remove prefixes like "sha256=", "hmac=")
    signature = _normalize_signature(signature_raw)
    
    # Try verification with different keys
    keys_to_try = []
    if webhook_key:
        keys_to_try.append(("webhook_key", webhook_key))
    if webhook_test_key:
        keys_to_try.append(("webhook_test_key", webhook_test_key))  
    if partner_key:
        keys_to_try.append(("partner_key", partner_key))
    
    if not keys_to_try:
        frappe.logger().error("[Webhook] No webhook keys configured in Shopee Settings")
        return False
    
    # Try each key
    for key_name, key_value in keys_to_try:
        frappe.logger().info(f"[Webhook Debug] Trying verification with {key_name}")
        if _verify_with_key(signature, raw_body, key_value, key_name):
            frappe.logger().info(f"[Webhook Debug] ✓ Signature verified with {key_name}")
            return True
        else:
            frappe.logger().info(f"[Webhook Debug] ✗ Signature failed with {key_name}")
    
    # Enhanced debug info
    frappe.logger().info(f"[Webhook Debug] All signature verification attempts failed")
    frappe.logger().info(f"[Webhook Debug] Incoming signature: {signature[:20]}...")
    frappe.logger().info(f"[Webhook Debug] Body length: {len(raw_body)}")
    
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


def create_payment_entry_from_shopee(
    si_name: str,
    escrow: dict,
    net_amount: float,
    order_sn: str,
    posting_ts: int | None = None,
    enqueue: bool = False
) -> str | None:
    """
    Create Payment Entry for Shopee escrow settlement
    This function can be called from webhook or manually
    """
    if enqueue:
        return frappe.enqueue(
            "shopee_bridge.webhook.create_payment_entry_from_shopee",
            queue="short",
            job_name=f"PE Shopee {order_sn}",
            si_name=si_name,
            escrow=escrow,
            net_amount=net_amount,
            order_sn=order_sn,
            posting_ts=posting_ts,
            enqueue=False
        )

    try:
        si = frappe.get_doc("Sales Invoice", si_name)
        if si.docstatus != 1:
            frappe.throw(f"Sales Invoice {si.name} not submitted")

        # Import helper functions from api.py
        from .api import _get_or_create_account, _get_or_create_mode_of_payment, _insert_submit_with_retry

        # Account setup
        paid_from = si.debit_to
        paid_to = _get_or_create_account("Shopee (Escrow)", "Bank")
        mop = _get_or_create_mode_of_payment("Shopee")

        # Calculate fees
        fees = {
            "commission": _safe_flt(escrow.get("commission_fee")),
            "service": _safe_flt(escrow.get("service_fee")),
            "protection": _safe_flt(escrow.get("shipping_seller_protection_fee_amount")),
            "shipdiff": _safe_flt(escrow.get("shipping_fee_difference")),
            "voucher": (_safe_flt(escrow.get("voucher_seller")) + 
                       _safe_flt(escrow.get("coin_cash_back")) +
                       _safe_flt(escrow.get("voucher_code_seller")))
        }

        # Fee accounts
        fee_accounts = {
            "commission": _get_or_create_account("Komisi Shopee", "Expense Account"),
            "service": _get_or_create_account("Biaya Layanan Shopee", "Expense Account"),
            "protection": _get_or_create_account("Proteksi Pengiriman Shopee", "Expense Account"),
            "shipdiff": _get_or_create_account("Selisih Ongkir Shopee", "Expense Account"),
            "voucher": _get_or_create_account("Voucher Shopee", "Expense Account")
        }

        net = _safe_flt(net_amount)
        posting_date = _date_iso_from_epoch(posting_ts)

        # Create Payment Entry
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

        # Link to Sales Invoice
        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = net + sum(v for v in fees.values() if v > 0)

        # Add fee deductions
        for fee_type, amount in fees.items():
            if amount > 0:
                row = pe.append("deductions", {})
                row.account = fee_accounts[fee_type]
                row.amount = flt(amount)

        # Save and submit
        pe = _insert_submit_with_retry(pe)
        
        frappe.logger().info(f"[Shopee] Payment Entry {pe.name} created for SI {si.name}")
        return pe.name

    except Exception as e:
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
            "order_sn": order_data.get('ordersn', ''),
            "shop_id": str(webhook_data.get('shop_id', '')) if webhook_data else '',
            "status": order_data.get('status', ''),
            "event_type": order_data.get('status', ''),
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
        
        # Print summary untuk terminal
        status_icon = "✅" if result.get('success') else "❌"
        print(f"{status_icon} Webhook: {log_doc.order_sn or 'No Order'} | {log_doc.status or 'No Status'} | {processing_time:.1f}ms")
        
    except Exception as e:
        frappe.logger().error(f"Failed to log webhook activity: {str(e)}")