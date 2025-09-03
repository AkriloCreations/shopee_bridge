# shopee_bridge/webhook.py

import frappe # pyright: ignore[reportMissingImports]
import hmac
import hashlib
import json
from typing import Dict, Any, Optional
from frappe.utils import flt # pyright: ignore[reportMissingImports]
from .finance import _find_si_by_sn, _find_so_by_sn, _normalize_escrow_payload, create_payment_entry_from_shopee
from .utils import _extract_push_info # pyright: ignore[reportMissingImports]
from .utils import _settings, _safe_flt, _date_iso_from_epoch

def _get_live_push_key() -> str:
    s = _settings()
    # sesuaikan nama field di Doctype Settings kamu
    return (getattr(s, "live_push_partner_key", "") or getattr(s, "webhook_key", "") or "").strip()

def _safe_int(v, d=0):
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d
    
@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "OPTIONS"])
def shopee_webhook():
    """Main Shopee webhook handler (uses live_push_partner_key for signature verification)."""
    import time
    start_time = time.time()

    try:
        # Log request basics immediately
        frappe.logger().info(f"""[Shopee Webhook] ===== BEGIN REQUEST =====
            Timestamp: {frappe.utils.now()}
            Method: {frappe.request.method}
            Path: {frappe.request.path}
            Headers: {json.dumps(dict(frappe.request.headers), indent=2)}
        """)

        # Handle CORS preflight
        if frappe.request.method == "OPTIONS":
            frappe.local.response.headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Shopee-Signature"
            }
            frappe.logger().info("[Shopee Webhook] Handled CORS preflight request")
            return {"success": True, "message": "CORS handled"}

        # Get request data (raw bytes, no decoding)
        raw_body = frappe.request.get_data(as_text=False) or b""
        headers = dict(frappe.request.headers or {})
        
        # Early provisional log (minimal valid fields) – updated later or supplemented by final log entries
        try:
            env = (getattr(_settings(), "environment", "Test") or "Test").lower()
            source = "Shopee Live" if env == "production" else "Shopee Test"
            log_doc = frappe.get_doc({
                "doctype": "Shopee Webhook Log",
                "timestamp": frappe.utils.now(),
                # order_sn/status unknown yet
                "raw_data": raw_body.decode('utf-8', errors='replace') if raw_body else '',
                "headers": json.dumps(headers, indent=2),
                "response_status": "Error",  # provisional until processed
                "source": source,
                "ip_address": frappe.request.environ.get('REMOTE_ADDR', 'Unknown')
            })
            log_doc.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as early_log_err:
            frappe.logger().warning(f"[Shopee Webhook] Early log insert failed: {early_log_err}")

        # Log incoming request details
        frappe.logger().info(f"""[Shopee Webhook] Incoming request:
            URL Path: {frappe.request.path}
            Method: {frappe.request.method}
            Client IP: {frappe.request.environ.get('REMOTE_ADDR', 'Unknown')}
            Content-Length: {len(raw_body)}
            Raw headers: {json.dumps(headers, indent=2)}
            Raw body preview: {raw_body[:200].decode('utf-8', errors='replace') if raw_body else 'empty'}...
            Log Entry: {log_doc.name}
        """)
        
    # Parse webhook data (best effort)
        webhook_data = None
        if raw_body:
            try:
                webhook_data = json.loads(raw_body.decode("utf-8"))
                frappe.logger().debug(f"[Shopee Webhook] Parsed webhook data: {json.dumps(webhook_data, indent=2)}")
            except Exception as e:
                processing_time = (time.time() - start_time) * 1000
                err_msg = str(e)
                frappe.logger().error(f"[Shopee Webhook] JSON parse error: {err_msg}, raw body: {raw_body[:500].decode('utf-8', errors='replace')}...")
                result = {
                    "success": True,  # Return success even for JSON errors
                    "message": "Webhook received",
                    "internal_error": f"JSON parse error: {err_msg}",
                    "timestamp": frappe.utils.now()
                }
                log_webhook_activity(None, headers, raw_body, result, processing_time)
                return result

        # Try both live and test push keys for verification (dynamic URL)
        s = _settings()
        live_push_key = (getattr(s, "live_push_partner_key", "") or getattr(s, "webhook_key", "") or "").strip()
        test_push_key = (getattr(s, "webhook_test_key", "") or "").strip()

        # Build full request URL exactly as received (scheme + host + path)
        def _build_full_url():
            env = frappe.request.environ or {}
            # Prefer forwarded proto/host when behind proxy
            scheme = (env.get("HTTP_X_FORWARDED_PROTO") or env.get("X_FORWARDED_PROTO") or env.get("wsgi.url_scheme") or getattr(frappe.request, "scheme", "https") or "https").split(",")[0].strip()
            host = (env.get("HTTP_X_FORWARDED_HOST") or env.get("X_FORWARDED_HOST") or env.get("HTTP_HOST") or getattr(frappe.request, "host", "") or frappe.request.host).split(",")[0].strip()
            path = frappe.request.path
            return f"{scheme}://{host}{path}"
        full_url = _build_full_url()
        frappe.logger().debug(f"[Shopee Webhook] Computed full URL for signature: {full_url}")

        skip_signature = False
        env_mode = (getattr(s, "environment", "Test") or "Test").lower()
        if env_mode != "production" and not live_push_key:
            # Allow skipping signature in non-production if no key is configured
            skip_signature = True
            frappe.logger().info("[Shopee Webhook] Skipping signature verification (non-production & no live key)")

        verified = True if skip_signature else False
        if not skip_signature:
            frappe.logger().debug(
                f"[Shopee Webhook] Attempting signature verification (env={env_mode}) live_key={'yes' if live_push_key else 'no'} test_key={'yes' if test_push_key else 'no'}"
            )
            # Primary key first
            if live_push_key:
                verified = verify_webhook_signature(full_url, raw_body, headers, live_push_key)
            # Fallback to test key if primary failed
            if not verified and test_push_key:
                verified = verify_webhook_signature(full_url, raw_body, headers, test_push_key)

        if not verified:
            processing_time = (time.time() - start_time) * 1000
            result = {"success": False, "error": "invalid_signature"}
            log_webhook_activity(webhook_data, headers, raw_body, result, processing_time)
            return result

        # Process webhook event
        if webhook_data and isinstance(webhook_data, dict):
            try:
                # Extract essential info first
                info = _extract_push_info(webhook_data)
                frappe.logger().debug(f"[Shopee Webhook] Extracted info: {json.dumps(info, indent=2)}")
                # Update provisional log with identifiers
                try:
                    if 'log_doc' in locals():
                        log_doc.order_sn = info.get('order_sn') or ''
                        log_doc.shop_id = str(info.get('shop_id') or '')
                        log_doc.event_type = info.get('status') or info.get('completed_scenario') or ''
                        mapped = _map_status_for_select(info.get('status'))
                        if mapped:
                            log_doc.status = mapped
                        log_doc.response_status = 'Success'
                        log_doc.save(ignore_permissions=True)
                        frappe.db.commit()
                except Exception as upd_err:
                    frappe.logger().warning(f"[Shopee Webhook] Failed to update provisional log: {upd_err}")
                
                # Return 200 OK even if processing fails
                result = {
                    "success": True,
                    "message": "Webhook received",
                    "order_sn": info.get("order_sn"),
                    "status": info.get("status"),
                    "timestamp": frappe.utils.now()
                }
                
                # Process in background if needed
                frappe.enqueue(
                    "shopee_bridge.webhook.process_webhook_event",
                    queue="short",
                    webhook_data=webhook_data,
                    now=True
                )
            except Exception as e:
                frappe.logger().error(f"[Shopee Webhook] Processing error (non-fatal): {str(e)}")
                result = {
                    "success": True,  # Still return success to Shopee
                    "message": "Webhook queued for processing",
                    "timestamp": frappe.utils.now()
                }
        else:
            result = {
                "success": True, 
                "message": "Webhook received but no data to process", 
                "timestamp": frappe.utils.now()
            }

        # Log activity
        processing_time = (time.time() - start_time) * 1000
        # Update existing log instead of duplicating
        if 'log_doc' in locals():
            log_webhook_activity(webhook_data, headers, raw_body, result, processing_time, existing_docname=log_doc.name)
        else:
            log_webhook_activity(webhook_data, headers, raw_body, result, processing_time)
        return result

    except Exception as e:
        processing_time = (time.time() - start_time) * 1000
        error_msg = str(e)
        traceback = frappe.get_traceback()
        
        # Log the error
        frappe.logger().error(f"""[Shopee Webhook] Critical error:
            Error: {error_msg}
            Traceback: {traceback}
            Processing time: {processing_time:.2f}ms
        """)
        
        # Update log entry if it exists
        if 'log_doc' in locals():
            try:
                log_doc.status = 'Error'
                log_doc.error_message = error_msg
                log_doc.stack_trace = traceback
                log_doc.processing_time = processing_time
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
            except Exception as log_err:
                frappe.logger().error(f"[Shopee Webhook] Failed to update log entry: {str(log_err)}")
        
        result = {
            "success": False,
            "error": "server_error",
            "details": error_msg,
            "timestamp": frappe.utils.now()
        }
        
        if 'log_doc' in locals():
            log_webhook_activity(webhook_data if 'webhook_data' in locals() else None,
                                 headers if 'headers' in locals() else {},
                                 raw_body if 'raw_body' in locals() else b"",
                                 result, processing_time, existing_docname=log_doc.name)
        else:
            log_webhook_activity(webhook_data if 'webhook_data' in locals() else None,
                                 headers if 'headers' in locals() else {},
                                 raw_body if 'raw_body' in locals() else b"",
                                 result, processing_time)
                           
        frappe.log_error(traceback, "Shopee Webhook Critical Error")
        return result

def verify_webhook_signature(url: str, raw_body: bytes, headers: dict, push_key: str) -> bool:
    """Verify Shopee Push v2 signature using HMAC-SHA256 (hex digest).

    Shopee spec:
      base_string = full_url + '|' + raw_body (raw bytes, not re-serialized)
      signature   = hex( HMAC_SHA256(partner_key, base_string) )
      Compare against Authorization header value (case sensitive hex)
    """
    try:
        if not push_key:
            frappe.logger().error("[Shopee Webhook] Missing partner push key for verification")
            return False

        # Case-insensitive header lookup
        auth = None
        for k, v in headers.items():
            if k.lower() == "authorization":
                auth = (v or "").strip()
                break
        if not auth:
            frappe.logger().warning("[Shopee Webhook] Authorization header absent")
            return False

        # Base string: url|body (use raw bytes to avoid encoding drift)
        # Build as bytes directly to avoid intermediate UTF-8 decoding errors
        base_bytes = url.encode("utf-8") + b"|" + (raw_body or b"")
        calc_hex = hmac.new(push_key.encode("utf-8"), base_bytes, hashlib.sha256).hexdigest()

        if hmac.compare_digest(calc_hex, auth):
            frappe.logger().info("[Shopee Webhook] ✓ Signature verified")
            return True

        # Mismatch – log concise diff (truncate body to protect logs)
        body_preview = (raw_body[:120].decode('utf-8', errors='replace') + ('...' if len(raw_body) > 120 else '')) if raw_body else ''
        frappe.logger().warning(
            "[Shopee Webhook] ✗ Signature mismatch | auth=%s calc=%s url=%s body_len=%s body_preview=%s" % (
                auth[:16], calc_hex[:16], url, len(raw_body), body_preview
            )
        )
        return False
    except Exception as e:
        frappe.logger().error(f"[Shopee Webhook] Signature verification error: {e}")
        return False

def test_webhook_signature(body: str | bytes, key: str) -> str:
    """Test utility to generate webhook signature for a given body and key.
    
    Args:
        body: Request body (string or bytes)
        key: Partner key to use for signing
        
    Returns:
        str: Calculated signature in hex format
    """
    # Use current site URL so generated signature matches current environment
    try:
        base = frappe.utils.get_url()
    except Exception:
        base = "https://example.com"  # fallback for contexts without request
    url = f"{base}/api/method/shopee_bridge.webhook.shopee_webhook"
    if isinstance(body, str):
        body = body.encode("utf-8")
    key_bytes = key.encode("utf-8")
    base_string = f"{url}|{body.decode('utf-8')}"
    return hmac.new(key_bytes, base_string.encode("utf-8"), hashlib.sha256).hexdigest()


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
    """Handle payment/escrow/payout events (refund aware + soft-skip)."""
    # dukung push v2 & event lama
    order_sn = data.get("order_sn", "") or (data.get("data", {}) or {}).get("ordersn", "")
    event_type = data.get("event", "") or (data.get("data", {}) or {}).get("status", "")

    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}

    si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
    if not si_name:
        frappe.logger().info(f"[Shopee] No Sales Invoice found for order {order_sn}")
        return {"success": True, "message": "no_invoice_found", "order_sn": order_sn, "event_type": event_type}

    norm = _normalize_escrow_payload(data)
    net_amount = flt(norm.get("net_amount"))
    escrow_amount = flt(norm.get("escrow_amount"))
    payout_amount = flt(norm.get("payout_amount"))
    refund_amount = flt(norm.get("refund_amount"))
    is_refund = bool(norm.get("is_refund"))
    commission_fee = flt(norm.get("commission_fee"))
    service_fee = flt(norm.get("service_fee"))

    frappe.logger().info(
        f"[Shopee] Payment event for SI {si_name} | net={net_amount} escrow={escrow_amount} payout={payout_amount} refund={refund_amount} is_refund={is_refund}"
    )

    # SOFT-SKIP jika net tidak ada / refund
    if net_amount <= 0:
        note = "refund_or_not_settled" if is_refund or refund_amount > 0 else "not_settled_yet"
        frappe.logger().info(f"[Shopee] Skip enqueue PE: net_amount<=0 ({note}) for order {order_sn}")
        return {
            "success": True,
            "message": "payment_event_logged_no_net",
            "order_sn": order_sn,
            "event_type": event_type,
            "amounts": {
                "net_amount": net_amount,
                "escrow_amount": escrow_amount,
                "payout_amount": payout_amount,
                "refund_amount": refund_amount,
                "commission_fee": commission_fee,
                "service_fee": service_fee
            },
            "note": f"skip_create_pe:{note}"
        }

    # net > 0 → enqueue pembuatan Payment Entry
    try:
        posting_ts = _safe_int(norm.get("payout_time") or data.get("payout_time") or data.get("update_time"))
        pe_job = create_payment_entry_from_shopee(
            si_name=si_name,
            escrow=data,
            net_amount=net_amount,
            order_sn=order_sn,
            posting_ts=posting_ts,
            enqueue=True
        )
        return {
            "success": True,
            "message": "payment_event_processed",
            "event_type": event_type,
            "order_sn": order_sn,
            "sales_invoice": si_name,
            "payment_entry_job": pe_job,
            "note": "Payment Entry creation enqueued",
        }
    except Exception as e:
        frappe.log_error(f"Payment Entry creation failed for {order_sn}: {str(e)}", "Shopee Payment")
        return {"success": False, "error": str(e), "order_sn": order_sn, "event_type": event_type}


def handle_order_created(data: Dict[str, Any]) -> Dict[str, Any]:
    """Handle new order creation events with duplicate prevention"""
    order_sn = data.get("order_sn", "")
    
    if not order_sn:
        return {"success": False, "error": "missing_order_sn"}
    
    # Check existing orders with detailed info
    existing_so = _find_so_by_sn(order_sn)
    existing_si = _find_si_by_sn(order_sn)
    
    if existing_so["exists"] or existing_si["exists"]:
        frappe.logger().info(
            f"[Shopee] Order {order_sn} already exists: " +
            f"SO={existing_so.get('name', '')}({existing_so.get('match_field', '')}), " +
            f"SI={existing_si.get('name', '')}({existing_si.get('match_field', '')})"
        )
        return {
            "success": True,
            "message": "order_already_exists",
            "order_sn": order_sn,
            "existing_so": existing_so,
            "existing_si": existing_si,
            "note": "Order exists, skipping creation"
        }
    
    frappe.logger().info(f"[Shopee] New order creation webhook: {order_sn}")
    
    result = {
        "success": True,
        "message": "new_order_logged",
        "order_sn": order_sn
    }
    
    try:
        # Import from api.py
        from .orders import _process_order
        
        job_id = frappe.enqueue(
            "_process_order",
            queue="short",
            timeout=300,
            order_sn=order_sn,
            # Add flag to ensure both fields are set
            set_both_refs=True  
        )
        result["order_processing_job"] = job_id
        result["action"] = "order_processing_enqueued"
    except Exception as e:
        result["order_error"] = str(e)
        frappe.logger().warning(f"[Shopee] Order processing failed: {str(e)}")
    
    return result

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
    try:
        s = _settings()
        has_live_push = bool(getattr(s, "live_push_partner_key", "") or getattr(s, "webhook_key", ""))

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
                "has_partner_key": bool(getattr(s, "partner_key", "")),
                "has_live_push_partner_key": has_live_push,  # NEW
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

def _map_status_for_select(raw_status: str | None) -> str | None:
    """Map raw Shopee status to allowed Select options in DocType.
    Allowed: PROCESSED, READY_TO_SHIP, SHIPPED, CANCELLED
    Fallback: None (leave blank) if not mappable."""
    if not raw_status:
        return None
    s = str(raw_status).upper()
    mapping = {
        'UNPAID': 'PROCESSED',  # treat UNPAID as received/processed
        'TO_SHIP': 'READY_TO_SHIP',
        'READY_TO_SHIP': 'READY_TO_SHIP',
        'PROCESSED': 'PROCESSED',
        'COMPLETED': 'PROCESSED',
        'SHIPPED': 'SHIPPED',
        'CANCELLED': 'CANCELLED',
        'CANCELED': 'CANCELLED'
    }
    return mapping.get(s)


def log_webhook_activity(webhook_data, headers, raw_body, result, processing_time, source="Shopee Live", existing_docname: str | None = None):
    """Log webhook activity to database"""
    try:
        order_data = webhook_data.get('data', {}) if webhook_data else {}
        event_type = ""
        if webhook_data:
            # event-based atau push (pakai status)
            event_type = (webhook_data.get('event') or "") or (order_data.get('status') or "")
        if existing_docname:
            try:
                log_doc = frappe.get_doc("Shopee Webhook Log", existing_docname)
                log_doc.order_sn = (order_data.get('ordersn') or (webhook_data.get('order_sn') if webhook_data else "") or log_doc.get('order_sn')) if webhook_data else log_doc.get('order_sn')
                log_doc.shop_id = str(webhook_data.get('shop_id', '')) if webhook_data else log_doc.get('shop_id')
                mapped_status = _map_status_for_select(order_data.get('status'))
                if mapped_status:
                    log_doc.status = mapped_status
                log_doc.event_type = event_type or log_doc.get('event_type')
                # Replace raw_data only if we have parsed data (preserve original otherwise)
                if webhook_data:
                    log_doc.raw_data = json.dumps(webhook_data, indent=2)
                log_doc.headers = json.dumps(headers, indent=2)
                log_doc.response_status = "Success" if result.get('success') else ("Error" if result.get('error') else "Failed")
                log_doc.error_message = result.get('error', '') if not result.get('success') else ''
                log_doc.processing_time = processing_time
                log_doc.source = source
                log_doc.save(ignore_permissions=True)
                frappe.db.commit()
                frappe.logger().info(f"[Shopee Webhook] Log updated order={log_doc.order_sn or '-'} event={log_doc.event_type or '-'}")
                return
            except Exception as upd_err:
                frappe.logger().warning(f"[Shopee Webhook] Could not update existing log {existing_docname}: {upd_err}; creating new entry")

        # Create new log if no existing docname provided or update failed
        doc_values = {
            "doctype": "Shopee Webhook Log",
            "timestamp": frappe.utils.now(),
            "order_sn": (order_data.get('ordersn') or (webhook_data.get('order_sn') if webhook_data else "")) or "",
            "shop_id": str(webhook_data.get('shop_id', '')) if webhook_data else '',
            "status": _map_status_for_select(order_data.get('status')) or '',
            "event_type": event_type,
            "raw_data": json.dumps(webhook_data, indent=2) if webhook_data else (raw_body.decode(errors="replace") if isinstance(raw_body, (bytes, bytearray)) else str(raw_body)),
            "headers": json.dumps(headers, indent=2),
            "response_status": "Success" if result.get('success') else ("Error" if result.get('error') else "Failed"),
            "error_message": result.get('error', '') if not result.get('success') else '',
            "processing_time": processing_time,
            "source": source,
            "ip_address": frappe.request.environ.get('REMOTE_ADDR', 'Unknown')
        }
        new_log = frappe.get_doc(doc_values)
        new_log.insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.logger().info(
            f"[Shopee Webhook] Log saved order={new_log.order_sn or '-'} event={new_log.event_type or '-'} time={processing_time:.1f}ms status={new_log.response_status}"
        )

    except Exception as e:
        frappe.logger().error(f"Failed to log webhook activity: {str(e)}")

def _pe_precision(pe) -> int:
    # presisi angka uang pada Payment Entry (ikut company/currency)
    try:
        return pe.precision("paid_amount") or 2
    except Exception:
        return 2

@frappe.whitelist()
def get_shopee_return_list(page_no: int = 1, page_size: int = 20,
                           create_time_from: int = None, create_time_to: int = None,
                           update_time_from: int = None, update_time_to: int = None,
                           status: str = None, negotiation_status: str = None,
                           seller_proof_status: str = None, seller_compensation_status: str = None):
    """
    Call Shopee API /api/v2/returns/get_return_list and return the result.
    """
    import time
    s = frappe.get_single("Shopee Settings")
    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    shop_id = s.shop_id
    access_token = s.access_token
    path = "/api/v2/returns/get_return_list"
    ts = int(time.time())

    # Build signature base string
    base_string = f"{partner_id}{path}{ts}{access_token}{shop_id}"
    sign = hmac.new(partner_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()

    params = {
        "partner_id": partner_id,
        "timestamp": ts,
        "access_token": access_token,
        "shop_id": shop_id,
        "sign": sign,
        "page_no": int(page_no),
        "page_size": int(page_size),
    }
    # Optional filters
    if create_time_from: params["create_time_from"] = int(create_time_from)
    if create_time_to: params["create_time_to"] = int(create_time_to)
    if update_time_from: params["update_time_from"] = int(update_time_from)
    if update_time_to: params["update_time_to"] = int(update_time_to)
    if status: params["status"] = status
    if negotiation_status: params["negotiation_status"] = negotiation_status

    if seller_proof_status: params["seller_proof_status"] = seller_proof_status
    if seller_compensation_status: params["seller_compensation_status"] = seller_compensation_status

    # Use requests.get for Shopee GET API
    import requests
    url = f"{s.environment == 'Production' and 'https://partner.shopeemobile.com' or 'https://partner.test-stable.shopeemobile.com'}{path}"
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
        else:
            data = {"error": "HTTP", "message": resp.text}
        return data
    except Exception as e:
        frappe.log_error(f"get_return_list failed: {e}", "Shopee Return List")
        return {"error": "exception", "message": str(e)}

@frappe.whitelist()
def get_shopee_return_detail(return_sn: str):
    """
    Call Shopee API /api/v2/returns/get_return_detail for a specific return_sn.
    """
    import time
    s = frappe.get_single("Shopee Settings")
    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    shop_id = s.shop_id
    access_token = s.access_token
    path = "/api/v2/returns/get_return_detail"
    ts = int(time.time())

    # Build signature base string
    base_string = f"{partner_id}{path}{ts}{access_token}{shop_id}"
    sign = hmac.new(partner_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()

    params = {
        "partner_id": partner_id,
        "timestamp": ts,
        "access_token": access_token,
        "shop_id": shop_id,
        "sign": sign,
        "return_sn": return_sn,
    }

    import requests
    url = f"{s.environment == 'Production' and 'https://partner.shopeemobile.com' or 'https://partner.test-stable.shopeemobile.com'}{path}"
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.headers.get("content-type", "").startswith("application/json"):
            data = resp.json()
        else:
            data = {"error": "HTTP", "message": resp.text}
        return data
    except Exception as e:
        frappe.log_error(f"get_return_detail failed: {e}", "Shopee Return Detail")
        return {"error": "exception", "message": str(e)}

