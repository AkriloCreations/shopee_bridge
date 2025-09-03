# shopee_bridge/webhook.py

import frappe # pyright: ignore[reportMissingImports]
import hmac
import hashlib
import base64
import json
import datetime
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

def _find_so_by_sn(order_sn: str) -> Dict[str, Any]:
    """Find Sales Order by order_sn, checking both po_no and custom_shopee_order_sn fields."""
    if not order_sn:
        return {"exists": False}
        
    # Check po_no
    so_po = frappe.db.get_value("Sales Order", {"po_no": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_po:
        return {"exists": True, "name": so_po.name, "po_no": so_po.po_no, "custom_shopee_order_sn": so_po.custom_shopee_order_sn, "modified": so_po.modified, "match_field": "po_no"}

    # Check custom_shopee_order_sn
    so_custom = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_custom:
        return {"exists": True, "name": so_custom.name, "po_no": so_custom.po_no, "custom_shopee_order_sn": so_custom.custom_shopee_order_sn, "modified": so_custom.modified, "match_field": "custom_shopee_order_sn"}

    # Check purchase_order_number (if present)
    so_purchase = frappe.db.get_value("Sales Order", {"purchase_order_number": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_purchase:
        return {"exists": True, "name": so_purchase.name, "po_no": so_purchase.po_no, "custom_shopee_order_sn": so_purchase.custom_shopee_order_sn, "modified": so_purchase.modified, "match_field": "purchase_order_number"}

    return {"exists": False}

def _find_si_by_sn(order_sn: str) -> Dict[str, Any]:
    """Find Sales Invoice by order_sn, checking both po_no and custom_shopee_order_sn fields."""
    if not order_sn:
        return {"exists": False}
        
    # Check po_no
    si_po = frappe.db.get_value("Sales Invoice", {"po_no": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_po:
        return {"exists": True, "name": si_po.name, "po_no": si_po.po_no, "custom_shopee_order_sn": si_po.custom_shopee_order_sn, "modified": si_po.modified, "match_field": "po_no"}

    # Check custom_shopee_order_sn
    si_custom = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_custom:
        return {"exists": True, "name": si_custom.name, "po_no": si_custom.po_no, "custom_shopee_order_sn": si_custom.custom_shopee_order_sn, "modified": si_custom.modified, "match_field": "custom_shopee_order_sn"}

    # Check purchase_order_number (if present)
    si_purchase = frappe.db.get_value("Sales Invoice", {"purchase_order_number": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_purchase:
        return {"exists": True, "name": si_purchase.name, "po_no": si_purchase.po_no, "custom_shopee_order_sn": si_purchase.custom_shopee_order_sn, "modified": si_purchase.modified, "match_field": "purchase_order_number"}

    return {"exists": False}

def _date_iso_from_epoch(ts: int | None) -> str:
    """Epoch detik → 'YYYY-MM-DD' (UTC baseline, cukup untuk tanggal dokumen)."""
    from datetime import datetime, timezone
    if not ts:
        return frappe.utils.nowdate()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()

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
        from .api import _process_order
        
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

def _normalize_escrow_payload(payload: dict) -> dict:
    """Normalisasi payload escrow Shopee (flat / response.order_income) + flag refund."""
    frappe.logger().info(f"[Shopee Escrow Debug] Raw payload: {payload}")
    root = (payload or {}).get("response") or (payload or {})
    oi = root.get("order_income") or {}
    frappe.logger().info(f"[Shopee Escrow Debug] Order income: {oi}")

    payout_amount = flt(root.get("payout_amount") or oi.get("payout_amount"))
    escrow_amount = flt(
        oi.get("escrow_amount_after_adjustment")
        or oi.get("escrow_amount")
        or root.get("escrow_amount")
    )
    refund_amount = flt(root.get("refund_amount") or oi.get("refund_amount") or oi.get("refund_to_buyer_amount"))
    reverse_shipping_fee = flt(oi.get("reverse_shipping_fee"))
    shipping_rebate = flt(oi.get("shopee_shipping_rebate"))
    return_to_seller = flt(oi.get("return_to_seller_amount"))

    # net default + kurangi refund bila ada
    net_amount = (payout_amount or escrow_amount)
    if refund_amount > 0:
        net_amount = flt(net_amount - refund_amount)

    commission_fee = flt(oi.get("commission_fee") or oi.get("commission"))
    service_fee = flt(oi.get("service_fee") or oi.get("transaction_fee") or oi.get("seller_transaction_fee")) + flt(oi.get("credit_card_transaction_fee") or oi.get("credit_card_fee"))
    protection_fee = flt(oi.get("delivery_seller_protection_fee_premium_amount") or oi.get("protection_fee") or oi.get("shipping_seller_protection_fee"))
    shipping_fee_difference = reverse_shipping_fee - shipping_rebate
    voucher_seller = flt(oi.get("voucher_from_seller") or oi.get("voucher_seller"))
    voucher_shopee = flt(oi.get("voucher_from_shopee") or oi.get("voucher_shopee"))
    coin_cash_back = flt(oi.get("coins") or oi.get("coin_cash_back"))
    voucher_code_seller = flt(oi.get("voucher_code_seller"))
    credit_card_fee = flt(oi.get("credit_card_transaction_fee") or oi.get("credit_card_fee"))
    final_shipping_fee = flt(oi.get("final_shipping_fee"))  # can be negative; use abs later when creating PE

    payout_time = root.get("payout_time") or root.get("update_time")
    is_refund = (refund_amount > 0) or (net_amount <= 0)

    return {
        "net_amount": net_amount,
        "escrow_amount": escrow_amount,
        "payout_amount": payout_amount,
        "refund_amount": refund_amount,
        "commission_fee": commission_fee,
        "service_fee": service_fee,
        "shipping_seller_protection_fee_amount": protection_fee,
        "shipping_fee_difference": shipping_fee_difference,
        "voucher_seller": voucher_seller,
        "voucher_from_shopee": voucher_shopee,
        "coin_cash_back": coin_cash_back,
        "voucher_code_seller": voucher_code_seller,
        "credit_card_transaction_fee": credit_card_fee,
    "final_shipping_fee": final_shipping_fee,
        "payout_time": payout_time,
        "is_refund": is_refund,
        "return_to_seller_amount": return_to_seller,
        "reverse_shipping_fee": reverse_shipping_fee,
        "shipping_rebate": shipping_rebate,
    }

def create_payment_entry_from_shopee(si_name: str, escrow: dict, net_amount: float,
                                     order_sn: str, posting_ts: int | None = None, enqueue: bool = True):
    """Create a Payment Entry reflecting Shopee escrow payout and fees.

    Logic:
      - paid_amount = Sales Invoice grand_total
      - received_amount = escrow net (payout after fees)
      - deductions rows = individual Shopee fees (commission, service, etc.)
      - reference_date & posting_date = payout date (uang masuk)
    """
    # Defensive: SI must exist. If missing and we have order_sn, try to build via complete_order_to_si.
    if not si_name:
        try:
            frappe.logger().warning(f"[PE Debug] {order_sn}: si_name missing → attempting auto SI creation via complete_order_to_si")
            from .api import complete_order_to_si  # local import to avoid cycles at module import
            res = complete_order_to_si(order_sn)
            if isinstance(res, dict):
                si_name = res.get("sales_invoice") or si_name
                if not si_name and res.get("status") == "already_invoiced":
                    si_name = res.get("sales_invoice")
        except Exception as auto_e:
            frappe.logger().error(f"[PE Debug] {order_sn}: auto create SI failed: {auto_e}")
    if not si_name:
        frappe.logger().error(f"[PE Debug] {order_sn}: abort PE, SI still missing after attempt")
        return None
    # Pre-initialize to avoid UnboundLocalError if an early reference happens on failure paths
    esc_n = {}
    try:
        si = frappe.get_doc("Sales Invoice", si_name)

        # Accept either raw escrow payload (with nested response/order_income) OR an already-normalized dict
        if escrow and ("net_amount" in escrow and any(k in escrow for k in ("commission_fee", "service_fee"))):
            esc_n = escrow  # already normalized
        else:
            esc_n = _normalize_escrow_payload(escrow) or {}
        actual_net = flt(esc_n.get("net_amount") or esc_n.get("payout_amount") or net_amount)
        if actual_net <= 0:
            # Fallback: create Journal Entry to clear SI if negative escrow/net (refund/return)
            try:
                abs_amount = abs(actual_net)
                receivable = (frappe.db.get_value("Company", si.company, "default_receivable_account") or
                              getattr(si, "debit_to", None) or
                              frappe.db.get_value("Account", {"company": si.company, "account_type": "Receivable"}, "name"))
                bank_acc = _get_or_create_bank_account("Shopee (Escrow)")
                if not receivable or not bank_acc:
                    frappe.logger().error(f"[PE Debug] {order_sn}: missing accounts for JE fallback receivable={receivable} bank={bank_acc}")
                    return None
                je = frappe.new_doc("Journal Entry")
                je.voucher_type = "Credit Note"
                je.posting_date = nowdate()
                je.company = si.company
                je.remark = f"Shopee Refund/Return for {order_sn} (Auto-created)"
                # Credit Shopee bank, debit receivable
                je.append("accounts", {
                    "account": bank_acc,
                    "credit_in_account_currency": abs_amount,
                    "reference_type": "Sales Invoice",
                    "reference_name": si.name,
                })
                je.append("accounts", {
                    "account": receivable,
                    "debit_in_account_currency": abs_amount,
                    "reference_type": "Sales Invoice",
                    "reference_name": si.name,
                })
                je.insert(ignore_permissions=True)
                je.submit()
                frappe.logger().info(f"[PE Debug] Created JE {je.name} for negative escrow/net {actual_net} on {order_sn}")
                return je.name
            except Exception as je_err:
                frappe.log_error(f"JE fallback failed for {order_sn}: {je_err}", "Shopee PE Fallback JE")
            frappe.logger().info(f"[PE Debug] {order_sn}: skip PE (actual_net={actual_net})")
            return None

        # Determine payout timestamp (tanggal uang masuk). Prioritization:
        # release_time > complete_time > payout_time (normalized/raw) > provided posting_ts > update_time
        raw = escrow if isinstance(escrow, dict) else {}
        raw_release = raw.get("release_time") or raw.get("escrow_release_time")
        raw_complete = raw.get("complete_time")
        raw_payout = raw.get("payout_time") or esc_n.get("payout_time")
        raw_update = raw.get("update_time") or esc_n.get("update_time")
        order_provided = posting_ts
        ordered = [raw_release, raw_complete, raw_payout, order_provided, raw_update]
        payout_ts = None
        for cand in ordered:
            try:
                if cand and int(cand) > 0:
                    payout_ts = int(cand)
                    break
            except Exception:
                continue
        debug_times = {
            "release_time": raw_release,
            "complete_time": raw_complete,
            "payout_time": raw_payout,
            "arg_posting_ts": order_provided,
            "update_time": raw_update,
            "chosen": payout_ts,
        }
        frappe.logger().info(f"[PE DateDebug] {order_sn}: times={debug_times}")
        if payout_ts:
            try:
                posting_date = datetime.datetime.fromtimestamp(payout_ts).date().isoformat()
            except Exception:
                posting_date = nowdate()
        else:
            # Fallback: gunakan tanggal Sales Invoice (permintaan user) kalau tidak ada payout ts
            si_fallback = str(getattr(si, "posting_date", "") or "")
            if not si_fallback:
                posting_date = nowdate()
                frappe.logger().info(f"[PE DateDebug] {order_sn}: fallback to today (SI missing posting_date)")
            else:
                posting_date = si_fallback
                frappe.logger().info(f"[PE DateDebug] {order_sn}: using SI posting_date fallback {posting_date}")
        if posting_date > nowdate():
            posting_date = nowdate()

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.company = si.company
        pe.posting_date = posting_date
        pe.reference_no = order_sn
        # Ensure reference_date ALWAYS matches payout posting_date
        pe.reference_date = posting_date
        pe.remarks = f"Shopee Order {order_sn} Payment (Auto-created)"

        receivable = (frappe.db.get_value("Company", si.company, "default_receivable_account") or
                       getattr(si, "debit_to", None) or
                       frappe.db.get_value("Account", {"company": si.company, "account_type": "Receivable"}, "name"))
        bank_acc = _get_or_create_bank_account("Shopee (Escrow)")
        if not receivable or not bank_acc:
            frappe.logger().error(f"[PE Debug] {order_sn}: missing accounts receivable={receivable} bank={bank_acc}")
            return None
        pe.paid_from = receivable
        pe.paid_to = bank_acc
        pe.mode_of_payment = "Shopee"

        gross_amount = flt(si.grand_total)
        pe.paid_amount = gross_amount
        pe.received_amount = actual_net

        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = gross_amount

        # Gather deductions
        components = [
            ("commission_fee", "Komisi Shopee"),
            ("service_fee", "Biaya Layanan Shopee"),
            ("shipping_seller_protection_fee_amount", "Proteksi Pengiriman Shopee"),
            ("shipping_fee_difference", "Selisih Ongkir Shopee"),
            ("voucher_seller", "Voucher Shopee (Seller)"),
            ("coin_cash_back", "Coin Cashback Shopee"),
        ]
        try:
            deductions_cc = _get_default_cost_center_for_si(si)
        except Exception:
            deductions_cc = None
        for key, label in components:
            amt = flt(esc_n.get(key) or 0)
            if amt > 0:
                acc = _get_or_create_expense_account(label)
                if not acc:
                    continue
                row = pe.append("deductions", {})
                row.account = acc
                row.amount = amt
                row.description = f"{label} - {order_sn}"
                if deductions_cc and hasattr(row, "cost_center"):
                    row.cost_center = deductions_cc

        # Explicit shipping fee (final_shipping_fee) – Shopee sometimes sends negative value meaning cost to seller
        try:
            fsf = esc_n.get("final_shipping_fee")
            if fsf is not None and flt(fsf) != 0:
                shipping_cost = abs(flt(fsf))  # treat as positive deduction
                acc_ship = _get_or_create_expense_account("Biaya Ongkir Shopee")
                if acc_ship and shipping_cost > 0:
                    row = pe.append("deductions", {})
                    row.account = acc_ship
                    row.amount = shipping_cost
                    row.description = f"Biaya Ongkir Shopee (final_shipping_fee) - {order_sn}"
                    if deductions_cc and hasattr(row, "cost_center"):
                        row.cost_center = deductions_cc
        except Exception:
            pass

        expected = flt(gross_amount - actual_net)
        total_deduct = sum(flt(d.amount) for d in pe.deductions)
        diff = flt(expected - total_deduct)
        if abs(diff) > 1:
            acc = _get_or_create_expense_account("Biaya Shopee Lainnya")
            if acc:
                row = pe.append("deductions", {})
                row.account = acc
                row.amount = diff
                row.description = f"Penyesuaian Biaya Shopee - {order_sn}"
                if deductions_cc and hasattr(row, "cost_center"):
                    row.cost_center = deductions_cc
                total_deduct += diff

        calc_received = flt(gross_amount - total_deduct)
        if abs(calc_received - actual_net) > 1:
            frappe.logger().warning(f"[PE Debug] {order_sn}: mismatch calc_received={calc_received} net={actual_net}")

        pe.insert(ignore_permissions=True)
        pe.submit()
        frappe.logger().info(f"[PE Debug] Created PE {pe.name} for {order_sn} gross={gross_amount} net={actual_net} deductions={total_deduct}")
        return pe.name
    except Exception as e:
        try:
            esc_dump = frappe.as_json(escrow)[:500]
        except Exception:
            esc_dump = str(escrow)[:500]
        frappe.log_error(f"PE creation failed {order_sn}: {e}\nEscrow: {esc_dump}", "Shopee PE Error")
        return None

def _get_or_create_expense_account(account_name: str) -> str:
    """Create expense account jika belum ada."""
    # Clean account name (max 140 chars)
    clean_name = account_name.strip()[:140]
    
    # Get company dan cek existing by account_name + company (not by full name only)
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    existing = frappe.db.get_value("Account", {"account_name": clean_name, "company": company}, "name")
    if existing:
        return existing
    
    # Cari parent expense account
    parent_account = None
    expense_parents = [
        "Indirect Expenses",
        "Marketing Expenses", 
        "Selling Expenses",
        "Expenses"
    ]
    
    for parent in expense_parents:
        if frappe.db.exists("Account", {"account_name": parent, "company": company}):
            parent_account = frappe.db.get_value("Account", 
                {"account_name": parent, "company": company}, "name")
            break
    
    if not parent_account:
        # Try broader fallbacks before throwing to reduce noisy errors:
        # 1. Any group account with root_type Expense
        if not parent_account:
            any_expense_group = frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 1}, "name")
            if any_expense_group:
                parent_account = any_expense_group
        # 2. Company default expense account (if a custom field exists) – ignore if missing
        # 3. As last resort: pick first non-group Expense type account's parent
        if not parent_account:
            leaf_expense = frappe.db.get_list("Account", filters={"company": company, "account_type": "Expense", "is_group": 0}, fields=["parent_account"], limit=1)
            if leaf_expense and leaf_expense[0].get("parent_account"):
                parent_account = leaf_expense[0].get("parent_account")

    if not parent_account:
        # Instead of throwing (which spammed logs), just log once and abort creation
        frappe.logger().error(f"[Shopee Bridge] Unable to locate parent expense account for '{clean_name}'. Please create an Expense group (e.g. 'Indirect Expenses').")
        return None

    # Create account
    try:
        account = frappe.new_doc("Account")
        account.account_name = clean_name
        account.parent_account = parent_account
        account.account_type = "Indirect Expense"
        # Force root_type for safety if field exists
        if hasattr(account, "root_type"):
            account.root_type = "Expense"
        account.company = company
        account.is_group = 0
        account.insert(ignore_permissions=True)
        return account.name
    except Exception as e:
        # Handle duplicate race condition gracefully
        dup_existing = frappe.db.get_value("Account", {"account_name": clean_name, "company": company}, "name")
        if dup_existing:
            frappe.logger().info(f"[Shopee Bridge] Detected existing expense account after race: {dup_existing}")
            return dup_existing
        frappe.log_error(f"Failed to create expense account {clean_name}: {e}", "Account Creation")
        return None
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
    
    # Fallback: cari parent asset account yang ada
    if not parent:
        # Cari semua asset group accounts
        asset_groups = frappe.db.get_list("Account", 
            filters={"company": company, "root_type": "Asset", "is_group": 1},
            fields=["name"], limit=1
        )
        if asset_groups:
            parent = asset_groups[0].name
        else:
            # Ultimate fallback: cari root asset account
            root_asset = frappe.db.get_value("Account", 
                {"company": company, "root_type": "Asset", "parent_account": ["is", "not set"]}, 
                "name"
            )
            if root_asset:
                parent = root_asset
    
    if not parent:
        frappe.logger().error(f"No suitable parent account found for bank account in company {company}. Please set up your Chart of Accounts properly.")
        return None
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
    try:
        acc.insert(ignore_permissions=True)
        return acc.name
    except Exception as e:
        frappe.logger().error(f"Failed to insert bank account {account_name}: {e}")
        # Try with a different name if duplicate
        if "Duplicate entry" in str(e):
            alt_name = f"{account_name} ({frappe.utils.random_string(4)})"
            frappe.logger().info(f"Trying alternative bank name: {alt_name}")
            try:
                acc.account_name = alt_name
                acc.insert(ignore_permissions=True)
                return acc.name
            except Exception as e2:
                frappe.logger().error(f"Failed to insert bank with alternative name {alt_name}: {e2}")
        frappe.throw(f"Cannot create bank account {account_name}: {str(e)}")


def create_refund_journal_from_shopee(si_name: str, norm_payload: dict, order_sn: str) -> str | None:
    """Create a Credit Note to record refund when net <= 0 or payout negative.

    The CN will post as return against the original SI, reducing its outstanding.

    Returns: CN name or None
    """
    try:
        si = frappe.get_doc("Sales Invoice", si_name)
    except Exception:
        return None

    refund_amount = flt(norm_payload.get("refund_amount") or 0)
    net = flt(norm_payload.get("net_amount") or 0)
    escrow = flt(norm_payload.get("escrow_amount") or 0)
    amount = refund_amount if refund_amount > 0 else max(0.0, escrow - net)
    if amount <= 0:
        amount = abs(net) or 0.0
    if amount <= 0:
        return None

    # Check if CN already exists
    cn_exists = frappe.db.exists("Sales Invoice", {"return_against": si_name, "is_return": 1})
    if cn_exists:
        return cn_exists

    try:
        cn = frappe.new_doc("Sales Invoice")
        cn.customer = si.customer
        cn.posting_date = frappe.utils.nowdate()
        cn.set_posting_time = 1
        cn.company = si.company
        cn.currency = si.currency
        cn.update_stock = 0
        cn.is_return = 1
        cn.return_against = si_name
        try:
            cn.custom_shopee_refund_sn = order_sn
        except Exception:
            pass
        base_po = f"{order_sn}-RET"
        cn.po_no = base_po if not frappe.db.exists("Sales Invoice", {"po_no": base_po}) else f"{base_po}-{frappe.utils.random_string(4)}"

        # Copy items from original SI with negative qty
        for item in si.items:
            cn_item = cn.append("items", {})
            cn_item.item_code = item.item_code
            cn_item.qty = -1 * flt(item.qty or 0)
            cn_item.rate = item.rate
            if item.warehouse:
                cn_item.warehouse = item.warehouse

        # Add Shopee fees as tax/charge rows if any
        extra_fees = [
            ("commission_fee", "Komisi Shopee"),
            ("service_fee", "Biaya Layanan Shopee"),
            ("shipping_seller_protection_fee_amount", "Proteksi Pengiriman Shopee"),
            ("voucher_seller", "Voucher Seller Shopee"),
            ("coin_cash_back", "Coin Cashback Shopee"),
        ]
        for key, name in extra_fees:
            fee = flt(norm_payload.get(key))
            if fee > 0:
                account = _get_or_create_expense_account(name)
                tax_row = cn.append("taxes", {})
                tax_row.charge_type = "Actual"
                tax_row.account_head = account
                tax_row.tax_amount = -abs(fee)

        # Ensure update_outstanding_for_self is unchecked
        if hasattr(cn, "update_outstanding_for_self"):
            cn.update_outstanding_for_self = 1

        cn.insert(ignore_permissions=True)
        cn.submit()
        frappe.db.commit()
        frappe.logger().info(f"[Shopee] Created Credit Note {cn.name} for refund {order_sn} against {si_name}")
        return cn.name
    except Exception as e:
        frappe.log_error(f"Failed to create refund CN for {order_sn}: {e}", "Shopee Refund CN")
        return None

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
                row.charge_type = "Actual"
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
    Debug signature Shopee Push:
    - Baca header Authorization / X-Shopee-Signature
    - Hitung HMAC SHA256 untuk 4 kandidat:
        raw, raw(rstrip CRLF), url|raw, url|raw(rstrip)
    - Kembalikan ringkasan agar kelihatan beda di mana
    """
    import base64, hashlib, hmac, binascii

    def first(s, n=16):
        return (s or "")[:n]

    raw_body = frappe.request.get_data(as_text=False) or b""
    headers = dict(frappe.request.headers or {})
    url_path = frappe.request.path

    incoming = (
        headers.get("Authorization")
        or headers.get("authorization")
        or headers.get("X-Shopee-Signature")
        or headers.get("x-shopee-signature")
        or ""
    ).strip()

    # sumber key
    push_key = _get_live_push_key() or (getattr(_settings(), "partner_key", "") or "").strip()
    key_src = "live_push_partner_key" if _get_live_push_key() else ("partner_key_fallback" if push_key else "none")

    if not incoming or not push_key:
        return {
            "ok": False,
            "reason": "missing_header_or_key",
            "have_header": bool(incoming),
            "have_key": bool(push_key),
            "key_src": key_src,
            "len": len(raw_body),
        }

    # decode signature kandidat (hex/base64/base64url)
    incoming_bytes = []
    s = incoming
    # strip prefix
    low = s.lower()
    for pref in ("sha256=", "hmac=", "signature="):
        if low.startswith(pref):
            s = s[len(pref):].strip()
            break
    # hex
    try:
        incoming_bytes.append(binascii.unhexlify(s))
    except Exception:
        pass
    # base64
    try:
        incoming_bytes.append(base64.b64decode(s, validate=False))
    except Exception:
        pass
    # base64url
    try:
        pad = "=" * (-len(s) % 4)
        incoming_bytes.append(base64.urlsafe_b64decode(s + pad))
    except Exception:
        pass

    key = push_key.encode("utf-8")
    raw = raw_body
    raw_trim = raw_body.rstrip(b"\r\n") if raw_body.endswith((b"\r", b"\n")) else raw_body

    # kandidat base string
    bases = [
        ("raw", raw),
        ("raw_trim", raw_trim),
        ("url|raw", f"{url_path}|".encode("utf-8") + raw),
        ("url|raw_trim", f"{url_path}|".encode("utf-8") + raw_trim),
    ]

    results = {}
    ok = False
    for name, base in bases:
        dig = hmac.new(key, base, hashlib.sha256).digest()
        hex_ = dig.hex()
        b64_ = base64.b64encode(dig).decode()

        # bandingkan ke setiap varian incoming_bytes
        match_decoded = any(hmac.compare_digest(dig, inc) for inc in incoming_bytes if inc)
        match_hex = hmac.compare_digest(hex_.encode(), incoming.lower().encode())

        results[name] = {
            "calc_hex_first16": first(hex_),
            "calc_b64_first16": first(b64_),
            "len_base": len(base),
            "match_decoded": bool(match_decoded),
            "match_hex": bool(match_hex),
        }
        ok = ok or match_decoded or match_hex

    return {
        "ok": bool(ok),
        "len": len(raw_body),
        "used_header": "Authorization" if ("Authorization" in headers or "authorization" in headers) else (
            "X-Shopee-Signature" if ("X-Shopee-Signature" in headers or "x-shopee-signature" in headers) else "none"
        ),
        "key_src": key_src,
        "incoming_first16": first(incoming),
        "bodies": {
            "has_trailing_crlf": raw_body.endswith((b'\r', b'\n')),
            "raw_len": len(raw),
            "raw_trim_len": len(raw_trim),
        },
        "compare": results,
    }

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
    from frappe.utils import flt
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

