# shopee_bridge/webhook.py

import frappe # pyright: ignore[reportMissingImports]
import hmac
import hashlib
import base64
import json
from typing import Dict, Any, Optional

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

@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "OPTIONS"])
def shopee_webhook():
    """
    Main Shopee webhook handler
    URL: https://<domain>/api/method/shopee_bridge.webhook.shopee_webhook
    """
    try:
        # Handle CORS preflight
        if frappe.request.method == "OPTIONS":
            frappe.local.response.headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Shopee-Signature"
            }
            return {"success": True}
        
        # Get request data
        raw_body = frappe.request.get_data() or b""
        headers = dict(frappe.request.headers or {})
        
        # Fix 417 error
        frappe.local.response.headers = frappe.local.response.headers or {}
        frappe.local.response.headers["Expect"] = ""
        
        # Log request
        frappe.logger().info(f"[Webhook] Received {frappe.request.method} request")
        frappe.logger().info(f"[Webhook] Body: {len(raw_body)} bytes")
        frappe.logger().info(f"[Webhook] Headers: {list(headers.keys())}")
        
        # Parse JSON data
        data = None
        if raw_body:
            try:
                data = json.loads(raw_body.decode('utf-8'))
                frappe.logger().info(f"[Webhook] Data: {data}")
            except Exception as e:
                frappe.logger().error(f"[Webhook] Parse error: {e}")
                return {"success": False, "error": "invalid_json"}
        
        # TEMPORARY: Skip signature verification
        frappe.logger().info("[Webhook] Processing without signature verification")
        
        # Process webhook
        if data:
            event = data.get("event", "").strip().lower()
            order_sn = data.get("order_sn", "")
            
            frappe.logger().info(f"[Webhook] Event: {event}, Order: {order_sn}")
            
            if event in ["payment_update", "escrow_settled", "payout"]:
                return handle_payment_event(data)
            elif event == "order_status_update":
                return handle_order_status(data)
            else:
                return {"success": True, "message": f"Event {event} logged"}
        
        return {"success": True, "message": "Webhook processed"}
        
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Shopee Webhook Error")
        return {"success": False, "error": str(e)}

def handle_payment_event(data):
    """Handle payment events"""
    order_sn = data.get("order_sn")
    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}
    
    amount = _safe_flt(data.get("escrow_amount") or data.get("payout_amount"))
    frappe.logger().info(f"[Payment] Order {order_sn}, Amount: {amount}")
    
    return {
        "success": True,
        "message": "payment_logged",
        "order_sn": order_sn,
        "amount": amount
    }

def handle_order_status(data):
    """Handle order status updates"""
    order_sn = data.get("order_sn")
    status = data.get("order_status")
    
    frappe.logger().info(f"[Status] Order {order_sn} -> {status}")
    
    return {
        "success": True,
        "message": "status_logged", 
        "order_sn": order_sn,
        "status": status
    }

@frappe.whitelist(allow_guest=True)
def webhook_test():
    """Test webhook"""
    return {
        "success": True,
        "message": "Webhook module working",
        "url": f"{frappe.utils.get_url()}/api/method/shopee_bridge.webhook.shopee_webhook"
    }
