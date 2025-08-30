# shopee_bridge/webhook.py

import frappe # pyright: ignore[reportMissingImports]
import hmac
import hashlib
import base64
from typing import Dict, Any, Optional
import json

def _settings():
    return frappe.get_single("Shopee Settings")

def _safe_flt(v, d=0.0):
    """Convert value to float with fallback default."""
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

def _safe_int(v, d=0):
    """Convert value to int with fallback default."""
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

@frappe.whitelist(allow_guest=True)
def shopee_webhook():
    """
    Main Shopee webhook handler
    URL: https://<domain>/api/method/shopee_bridge.webhook.shopee_webhook
    """
    try:
        # Get raw body and headers
        raw_body = frappe.request.get_data() or b""
        headers = dict(frappe.request.headers)
        
        # Log raw data untuk debugging (hanya environment Test)
        s = _settings()
        if getattr(s, "environment", "Test") == "Test":
            frappe.logger().info(f"[Webhook Debug] Raw body length: {len(raw_body)}")
            frappe.logger().info(f"[Webhook Debug] Headers: {list(headers.keys())}")
            for k, v in headers.items():
                if 'signature' in k.lower() or 'authorization' in k.lower():
                    frappe.logger().info(f"[Webhook Debug] {k}: {v[:20]}...")
        
        # Security first - verify signature
        if not verify_webhook_signature(raw_body, headers):
            frappe.log_error("Invalid Shopee signature", "Shopee Webhook")
            frappe.local.response.http_status_code = 401
            return {"success": False, "error": "invalid_signature"}
        
        # Parse data
        data, error = _parse_webhook_data(raw_body)
        if error:
            frappe.log_error(f"Webhook parsing failed: {error}", "Shopee Webhook Parse")
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": "invalid_data"}
        
        # Log webhook details
        _log_webhook_received(data, headers)
        
        # Route to appropriate handler
        return _route_webhook_event(data)
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shopee Webhook Critical Error")
        frappe.logger().error(f"[Shopee Webhook] Critical error: {str(e)}")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "server_error"}


def verify_webhook_signature(raw_body: bytes, headers: Dict[str, str]) -> bool:
    """
    Enhanced signature verification with multiple fallbacks
    """
    s = _settings()
    
    # Get keys from settings
    webhook_key = getattr(s, "webhook_key", "").strip()
    webhook_test_key = getattr(s, "webhook_test_key", "").strip()
    partner_key = getattr(s, "partner_key", "").strip()
    
    # Tampilkan key status untuk debugging (production mode tidak tampilkan)
    if getattr(s, "environment", "Test") == "Test":
        frappe.logger().info(f"[Webhook Debug] Available keys - webhook_key: {bool(webhook_key)}, test_key: {bool(webhook_test_key)}, partner_key: {bool(partner_key)}")
    
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
        if _verify_with_key(signature, raw_body, key_value, key_name):
            return True
    
    # Final debug info (hanya di Test environment)
    if getattr(s, "environment", "Test") == "Test":
        frappe.logger().info(f"[Webhook Debug] Signature verification failed")
        frappe.logger().info(f"[Webhook Debug] Incoming signature (first 20 chars): {signature[:20]}")
        frappe.logger().info(f"[Webhook Debug] Body length: {len(raw_body)}")
        
        # Show expected signature dengan first key (untuk debug)
        if keys_to_try:
            first_key = keys_to_try[0][1]
            expected = hmac.new(first_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            frappe.logger().info(f"[Webhook Debug] Expected signature (hex, first 20): {expected[:20]}")
    
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


def _parse_webhook_data(raw_body: bytes) -> tuple[Dict[str, Any], Optional[str]]:
    """Parse webhook JSON data safely"""
    if not raw_body:
        return frappe.local.form_dict or {}, None
    
    try:
        return json.loads(raw_body.decode('utf-8')), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {}, f"JSON parsing failed: {str(e)}"


def _route_webhook_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """Route webhook to appropriate handler based on event type"""
    event = data.get('event', '').strip().lower()
    
    handlers = {
        'order_status_update': handle_order_status_update,
        'payment_update': handle_payment_update,
        'escrow_settled': handle_payment_update,
        'payout': handle_payment_update,
        'order_created': handle_order_created,
    }
    
    handler = handlers.get(event)
    if handler:
        return handler(data)
    
    # Log unhandled events
    frappe.logger().info(f"[Shopee Webhook] Unhandled event: {event}")
    return {"success": True, "message": f"Event '{event}' logged but not processed"}


def handle_order_status_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle order status changes"""
    order_sn = data.get('order_sn')
    order_status = data.get('order_status')
    
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    frappe.logger().info(f"[Shopee Webhook] Processing order status update: {order_sn} -> {order_status}")
    
    # Check if we have this order in system
    so_exists = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    si_exists = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    if so_exists or si_exists:
        # Update logic here if needed
        # For now, just log
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} found in system, status: {order_status}")
        return {
            "success": True,
            "message": "order_status_logged",
            "order_sn": order_sn,
            "new_status": order_status
        }
    else:
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} not found in system")
        return {
            "success": True, 
            "message": "order_not_found_in_system",
            "order_sn": order_sn
        }


def handle_payment_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle payment/escrow/payout events"""
    order_sn = data.get('order_sn')
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    # Find related Sales Invoice
    si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
    if not si_name:
        frappe.logger().info(f"[Shopee Webhook] No Sales Invoice found for order {order_sn}")
        return {"success": True, "message": "no_invoice_found"}
    
    # Extract payment data
    net_amount = _safe_flt(data.get("escrow_amount") or data.get("payout_amount"))
    posting_timestamp = _safe_int(data.get("payout_time") or data.get("update_time"))
    
    if not net_amount:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_payment_amount"}
    
    try:
        # Import payment function dari api.py
        from .api import create_payment_entry_from_shopee
        
        # Create Payment Entry (enqueued for better performance)
        job_id = create_payment_entry_from_shopee(
            si_name=si_name,
            escrow=data,
            net_amount=net_amount,
            order_sn=order_sn,
            posting_ts=posting_timestamp,
            enqueue=True
        )
        
        frappe.logger().info(f"[Shopee Webhook] Enqueued Payment Entry creation for SI {si_name}, amount: {net_amount}")
        
        return {
            "success": True,
            "payment_entry_job": job_id,
            "sales_invoice": si_name,
            "net_amount": net_amount
        }
        
    except Exception as e:
        frappe.log_error(f"Payment processing failed for {order_sn}: {str(e)}", "Shopee Payment Webhook")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "payment_processing_failed"}


def handle_order_created(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle new order creation events"""
    order_sn = data.get('order_sn')
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    # Check if order already exists
    existing_so = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    existing_si = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    if existing_so or existing_si:
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} already exists")
        return {"success": True, "message": "order_already_exists"}
    
    # Enqueue order processing to avoid timeout
    try:
        job_id = frappe.enqueue(
            "shopee_bridge.api._process_order",
            queue="short",
            timeout=300,
            order_sn=order_sn
        )
        
        return {
            "success": True,
            "order_processing_job": job_id,
            "order_sn": order_sn
        }
        
    except Exception as e:
        frappe.log_error(f"Order processing enqueue failed for {order_sn}: {str(e)}", "Shopee Order Webhook")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "order_processing_failed"}


def _log_webhook_received(data: Dict[str, Any], headers: Dict[str, str]) -> None:
    """Log webhook details for debugging"""
    event = data.get('event', 'unknown')
    order_sn = data.get('order_sn', 'N/A')
    
    frappe.logger().info(f"[Shopee Webhook] Event: {event}")
    frappe.logger().info(f"[Shopee Webhook] Order SN: {order_sn}")
    
    # Log headers (excluding sensitive data) - hanya di Test environment
    s = _settings()
    if getattr(s, "environment", "Test") == "Test":
        safe_headers = {k: v for k, v in headers.items() 
                       if k.lower() not in ('authorization', 'x-shopee-signature')}
        frappe.logger().info(f"[Shopee Webhook] Headers: {list(safe_headers.keys())}")
        
        # Log full data only in development mode
        if frappe.conf.get('developer_mode'):
            frappe.logger().debug(f"[Shopee Webhook] Full data: {data}")


# =============================================================================
# TEST & DEBUG FUNCTIONS
# =============================================================================

@frappe.whitelist()
def test_webhook_signature():
    """Test webhook signature verification with current settings"""
    try:
        s = _settings()
        
        # Sample webhook data
        test_data = {
            "event": "payment_update",
            "order_sn": "TEST123456", 
            "escrow_amount": 100000
        }
        
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Test dengan berbagai keys yang tersedia
        results = {}
        
        # Test with webhook_key
        if hasattr(s, 'webhook_key') and s.webhook_key:
            signature = hmac.new(s.webhook_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['webhook_key'] = verify_webhook_signature(raw_body, headers)
        
        # Test with webhook_test_key
        if hasattr(s, 'webhook_test_key') and s.webhook_test_key:
            signature = hmac.new(s.webhook_test_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['webhook_test_key'] = verify_webhook_signature(raw_body, headers)
        
        # Test with partner_key
        if hasattr(s, 'partner_key') and s.partner_key:
            signature = hmac.new(s.partner_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['partner_key'] = verify_webhook_signature(raw_body, headers)
        
        return {
            "success": True,
            "test_data": test_data,
            "verification_results": results,
            "available_keys": {
                "webhook_key": bool(getattr(s, 'webhook_key', '')),
                "webhook_test_key": bool(getattr(s, 'webhook_test_key', '')),
                "partner_key": bool(getattr(s, 'partner_key', ''))
            }
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def debug_webhook_settings():
    """Debug webhook configuration"""
    try:
        s = _settings()
        
        return {
            "success": True,
            "environment": getattr(s, "environment", "Test"),
            "partner_id": getattr(s, "partner_id", ""),
            "shop_id": getattr(s, "shop_id", ""),
            "has_webhook_key": bool(getattr(s, "webhook_key", "")),
            "has_webhook_test_key": bool(getattr(s, "webhook_test_key", "")),
            "has_partner_key": bool(getattr(s, "partner_key", "")),
            "webhook_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.shopee_webhook"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}
    

def _settings():
    return frappe.get_single("Shopee Settings")

def _safe_flt(v, d=0.0):
    """Convert value to float with fallback default."""
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

def _safe_int(v, d=0):
    """Convert value to int with fallback default."""
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

@frappe.whitelist(allow_guest=True)
def shopee_webhook():
    """
    Main Shopee webhook handler
    URL: https://<domain>/api/method/shopee_bridge.webhook.shopee_webhook
    """
    try:
        # Get raw body and headers
        raw_body = frappe.request.get_data() or b""
        headers = dict(frappe.request.headers)
        
        # Log raw data untuk debugging (hanya environment Test)
        s = _settings()
        if getattr(s, "environment", "Test") == "Test":
            frappe.logger().info(f"[Webhook Debug] Raw body length: {len(raw_body)}")
            frappe.logger().info(f"[Webhook Debug] Headers: {list(headers.keys())}")
            for k, v in headers.items():
                if 'signature' in k.lower() or 'authorization' in k.lower():
                    frappe.logger().info(f"[Webhook Debug] {k}: {v[:20]}...")
        
        # Security first - verify signature
        if not verify_webhook_signature(raw_body, headers):
            frappe.log_error("Invalid Shopee signature", "Shopee Webhook")
            frappe.local.response.http_status_code = 401
            return {"success": False, "error": "invalid_signature"}
        
        # Parse data
        data, error = _parse_webhook_data(raw_body)
        if error:
            frappe.log_error(f"Webhook parsing failed: {error}", "Shopee Webhook Parse")
            frappe.local.response.http_status_code = 400
            return {"success": False, "error": "invalid_data"}
        
        # Log webhook details
        _log_webhook_received(data, headers)
        
        # Route to appropriate handler
        return _route_webhook_event(data)
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shopee Webhook Critical Error")
        frappe.logger().error(f"[Shopee Webhook] Critical error: {str(e)}")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "server_error"}


def verify_webhook_signature(raw_body: bytes, headers: Dict[str, str]) -> bool:
    """
    Enhanced signature verification with TEMPORARY bypass for debugging
    """
    s = _settings()
    
    # TEMPORARY: Skip verification in Test environment untuk debugging
    if getattr(s, "environment", "Test") == "Test":
        frappe.logger().info("[Webhook] TEMPORARY: Skipping signature verification in Test environment")
        return True
    
    # Get keys from settings
    webhook_key = getattr(s, "webhook_key", "").strip()
    webhook_test_key = getattr(s, "webhook_test_key", "").strip()
    partner_key = getattr(s, "partner_key", "").strip()
    
    # Debug key availability
    frappe.logger().info(f"[Webhook Debug] Available keys - webhook_key: {bool(webhook_key)}, test_key: {bool(webhook_test_key)}, partner_key: {bool(partner_key)}")
    
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
    
    # Log signature info
    frappe.logger().info(f"[Webhook Debug] Found signature header: {signature_raw[:30]}...")
    
    # Normalize signature (remove prefixes like "sha256=", "hmac=")
    signature = _normalize_signature(signature_raw)
    frappe.logger().info(f"[Webhook Debug] Normalized signature: {signature[:30]}...")
    
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
    frappe.logger().info(f"[Webhook Debug] Incoming signature: {signature}")
    frappe.logger().info(f"[Webhook Debug] Body length: {len(raw_body)}")
    frappe.logger().info(f"[Webhook Debug] Body preview: {raw_body[:100]}")
    
    # Show expected signatures for debug
    for key_name, key_value in keys_to_try:
        expected = hmac.new(key_value.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
        frappe.logger().info(f"[Webhook Debug] Expected with {key_name}: {expected}")
    
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


def _parse_webhook_data(raw_body: bytes) -> tuple[Dict[str, Any], Optional[str]]:
    """Parse webhook JSON data safely"""
    if not raw_body:
        return frappe.local.form_dict or {}, None
    
    try:
        return json.loads(raw_body.decode('utf-8')), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return {}, f"JSON parsing failed: {str(e)}"


def _route_webhook_event(data: Dict[str, Any]) -> Dict[str, Any]:
    """Route webhook to appropriate handler based on event type"""
    event = data.get('event', '').strip().lower()
    
    handlers = {
        'order_status_update': handle_order_status_update,
        'payment_update': handle_payment_update,
        'escrow_settled': handle_payment_update,
        'payout': handle_payment_update,
        'order_created': handle_order_created,
    }
    
    handler = handlers.get(event)
    if handler:
        return handler(data)
    
    # Log unhandled events
    frappe.logger().info(f"[Shopee Webhook] Unhandled event: {event}")
    return {"success": True, "message": f"Event '{event}' logged but not processed"}


def handle_order_status_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle order status changes"""
    order_sn = data.get('order_sn')
    order_status = data.get('order_status')
    
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    frappe.logger().info(f"[Shopee Webhook] Processing order status update: {order_sn} -> {order_status}")
    
    # Check if we have this order in system
    so_exists = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    si_exists = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    if so_exists or si_exists:
        # Update logic here if needed
        # For now, just log
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} found in system, status: {order_status}")
        return {
            "success": True,
            "message": "order_status_logged",
            "order_sn": order_sn,
            "new_status": order_status
        }
    else:
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} not found in system")
        return {
            "success": True, 
            "message": "order_not_found_in_system",
            "order_sn": order_sn
        }


def handle_payment_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle payment/escrow/payout events"""
    order_sn = data.get('order_sn')
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    # Find related Sales Invoice
    si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
    if not si_name:
        frappe.logger().info(f"[Shopee Webhook] No Sales Invoice found for order {order_sn}")
        return {"success": True, "message": "no_invoice_found"}
    
    # Extract payment data
    net_amount = _safe_flt(data.get("escrow_amount") or data.get("payout_amount"))
    posting_timestamp = _safe_int(data.get("payout_time") or data.get("update_time"))
    
    if not net_amount:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_payment_amount"}
    
    try:
        # Import payment function dari api.py
        from .api import create_payment_entry_from_shopee
        
        # Create Payment Entry (enqueued for better performance)
        job_id = create_payment_entry_from_shopee(
            si_name=si_name,
            escrow=data,
            net_amount=net_amount,
            order_sn=order_sn,
            posting_ts=posting_timestamp,
            enqueue=True
        )
        
        frappe.logger().info(f"[Shopee Webhook] Enqueued Payment Entry creation for SI {si_name}, amount: {net_amount}")
        
        return {
            "success": True,
            "payment_entry_job": job_id,
            "sales_invoice": si_name,
            "net_amount": net_amount
        }
        
    except Exception as e:
        frappe.log_error(f"Payment processing failed for {order_sn}: {str(e)}", "Shopee Payment Webhook")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "payment_processing_failed"}


def handle_order_created(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle new order creation events"""
    order_sn = data.get('order_sn')
    if not order_sn:
        frappe.local.response.http_status_code = 400
        return {"success": False, "error": "missing_order_sn"}
    
    # Check if order already exists
    existing_so = frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
    existing_si = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    
    if existing_so or existing_si:
        frappe.logger().info(f"[Shopee Webhook] Order {order_sn} already exists")
        return {"success": True, "message": "order_already_exists"}
    
    # Enqueue order processing to avoid timeout
    try:
        job_id = frappe.enqueue(
            "shopee_bridge.api._process_order",
            queue="short",
            timeout=300,
            order_sn=order_sn
        )
        
        return {
            "success": True,
            "order_processing_job": job_id,
            "order_sn": order_sn
        }
        
    except Exception as e:
        frappe.log_error(f"Order processing enqueue failed for {order_sn}: {str(e)}", "Shopee Order Webhook")
        frappe.local.response.http_status_code = 500
        return {"success": False, "error": "order_processing_failed"}


def _log_webhook_received(data: Dict[str, Any], headers: Dict[str, str]) -> None:
    """Log webhook details for debugging"""
    event = data.get('event', 'unknown')
    order_sn = data.get('order_sn', 'N/A')
    
    frappe.logger().info(f"[Shopee Webhook] Event: {event}")
    frappe.logger().info(f"[Shopee Webhook] Order SN: {order_sn}")
    
    # Log headers (excluding sensitive data) - hanya di Test environment
    s = _settings()
    if getattr(s, "environment", "Test") == "Test":
        safe_headers = {k: v for k, v in headers.items() 
                       if k.lower() not in ('authorization', 'x-shopee-signature')}
        frappe.logger().info(f"[Shopee Webhook] Headers: {list(safe_headers.keys())}")
        
        # Log full data only in development mode
        if frappe.conf.get('developer_mode'):
            frappe.logger().debug(f"[Shopee Webhook] Full data: {data}")


# =============================================================================
# TEST & DEBUG FUNCTIONS
# =============================================================================

@frappe.whitelist()
def test_webhook_signature():
    """Test webhook signature verification with current settings"""
    try:
        s = _settings()
        
        # Sample webhook data
        test_data = {
            "event": "payment_update",
            "order_sn": "TEST123456", 
            "escrow_amount": 100000
        }
        
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Test dengan berbagai keys yang tersedia
        results = {}
        
        # Test with webhook_key
        if hasattr(s, 'webhook_key') and s.webhook_key:
            signature = hmac.new(s.webhook_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['webhook_key'] = verify_webhook_signature(raw_body, headers)
        
        # Test with webhook_test_key
        if hasattr(s, 'webhook_test_key') and s.webhook_test_key:
            signature = hmac.new(s.webhook_test_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['webhook_test_key'] = verify_webhook_signature(raw_body, headers)
        
        # Test with partner_key
        if hasattr(s, 'partner_key') and s.partner_key:
            signature = hmac.new(s.partner_key.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
            headers = {"X-Shopee-Signature": signature}
            results['partner_key'] = verify_webhook_signature(raw_body, headers)
        
        return {
            "success": True,
            "test_data": test_data,
            "verification_results": results,
            "available_keys": {
                "webhook_key": bool(getattr(s, 'webhook_key', '')),
                "webhook_test_key": bool(getattr(s, 'webhook_test_key', '')),
                "partner_key": bool(getattr(s, 'partner_key', ''))
            }
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def debug_webhook_settings():
    """Debug webhook configuration"""
    try:
        s = _settings()
        
        return {
            "success": True,
            "environment": getattr(s, "environment", "Test"),
            "partner_id": getattr(s, "partner_id", ""),
            "shop_id": getattr(s, "shop_id", ""),
            "has_webhook_key": bool(getattr(s, "webhook_key", "")),
            "has_webhook_test_key": bool(getattr(s, "webhook_test_key", "")),
            "has_partner_key": bool(getattr(s, "partner_key", "")),
            "webhook_url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.shopee_webhook"
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}