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
    root = (payload or {}).get("response") or (payload or {})
    oi = root.get("order_income") or {}

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

    commission_fee = flt(oi.get("commission_fee"))
    service_fee = flt(oi.get("service_fee")) + flt(oi.get("seller_transaction_fee")) + flt(oi.get("credit_card_transaction_fee"))
    protection_fee = flt(oi.get("delivery_seller_protection_fee_premium_amount"))
    shipping_fee_difference = reverse_shipping_fee - shipping_rebate
    voucher_seller = flt(oi.get("voucher_from_seller"))
    coin_cash_back = flt(oi.get("coins"))
    voucher_code_seller = flt(oi.get("voucher_code_seller"))

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
        "coin_cash_back": coin_cash_back,
        "voucher_code_seller": voucher_code_seller,
        "payout_time": payout_time,
        "is_refund": is_refund,
        "return_to_seller_amount": return_to_seller,
        "reverse_shipping_fee": reverse_shipping_fee,
        "shipping_rebate": shipping_rebate,
    }

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

    if net <= 0:  # NEW
        # Untuk kasus refund / negative net: buat Journal Entry yang mencatat biaya/deduction
        try:
            norm2 = norm
            # set flag di SI jika ada
            try:
                si_tmp = frappe.get_doc("Sales Invoice", si_name)
                if hasattr(si_tmp, "custom_shopee_refund_sn"):
                    si_tmp.custom_shopee_refund_sn = order_sn
                    si_tmp.save(ignore_permissions=True)
                    frappe.db.commit()
            except Exception:
                pass

            je_name = create_refund_journal_from_shopee(si_name, norm2, order_sn)
            return je_name
        except Exception:
            # jika gagal, tetap skip pembuatan PE
            return None  # NEW
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

        # Tambahan: lock baris SI untuk mencegah race antar worker membuat PE bersamaan
        try:
            frappe.db.sql("select name from `tabSales Invoice` where name=%s for update", si.name)
        except Exception:
            pass

        # Cek lagi setelah lock
        pe_ref2 = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si.name}
        )
        if pe_ref2:
            return frappe.db.get_value("Payment Entry Reference", pe_ref2, "parent")

        # Cek Payment Entry existing berdasarkan reference_no (order_sn) + party sebagai fallback idempotensi
        existing_pe = frappe.db.get_value(
            "Payment Entry",
            {"reference_no": order_sn, "party_type": "Customer", "party": si.customer, "docstatus": ["!=", 2]},
            "name"
        )
        if existing_pe:
            # Pastikan referensi anak belum ada → kalau belum, jangan duplikat; biarkan manual fix jika perlu
            return existing_pe

        # Helpers
        from .api import _get_or_create_mode_of_payment, _insert_submit_with_retry

        paid_from = si.debit_to
        paid_to = _get_or_create_bank_account("Shopee (Escrow)")
        mop = _get_or_create_mode_of_payment("Shopee")

        # Gross vs Net
        gross = flt(si.grand_total)
        expected_total_fees_raw = max(0, gross - net)

        # Breakdown fees dari payload (raw, belum dibulatkan) - semua komponen utama Shopee
        fees_raw = {
            "commission": flt(norm.get("commission_fee")),
            "service": flt(norm.get("service_fee")),
            "protection": flt(norm.get("shipping_seller_protection_fee_amount")),
            "shipdiff": flt(norm.get("shipping_fee_difference")),
            "voucher_seller": flt(norm.get("voucher_seller")),
            "voucher_shopee": flt(norm.get("voucher_from_shopee")),
            "coin_cash_back": flt(norm.get("coin_cash_back")),
            "credit_card": flt(norm.get("credit_card_transaction_fee")),
            "voucher_code_seller": flt(norm.get("voucher_code_seller")),
        }
        payload_total_fees_raw = sum(v for v in fees_raw.values() if v > 0)
        diff_fee_raw = expected_total_fees_raw - payload_total_fees_raw  # bisa +/- karena rounding/kelengkapan payload

        fee_accounts = {
            "commission": _get_or_create_expense_account("Komisi Shopee"),
            "service": _get_or_create_expense_account("Biaya Layanan Shopee"),
            "protection": _get_or_create_expense_account("Proteksi Pengiriman Shopee"),
            "shipdiff": _get_or_create_expense_account("Selisih Ongkir Shopee"),
            "voucher_seller": _get_or_create_expense_account("Voucher Seller Shopee"),
            "voucher_shopee": _get_or_create_expense_account("Voucher Shopee"),
            "coin_cash_back": _get_or_create_expense_account("Coin Cashback Shopee"),
            "credit_card": _get_or_create_expense_account("Biaya Kartu Kredit Shopee"),
            "voucher_code_seller": _get_or_create_expense_account("Voucher Kode Seller Shopee"),
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

        rounded_net = flt(net, precision)
        if rounded_net <= 0:
            frappe.logger().info(f"[Shopee PE] Skip: rounded net {net} -> 0 @precision {precision} untuk {order_sn}")
            return None
        
        # Uang yang diterima (ke escrow) = NET (dibulatkan ke presisi PE)
        pe.paid_from = paid_from
        pe.paid_to = paid_to
        pe.paid_amount = flt(net, precision)
        pe.received_amount = flt(net, precision)

        # === Rebuild deductions agar TEPAT = gross_r - paid_amount (tidak overshoot) ===
        gross_r = flt(gross, precision)
        if pe.paid_amount > gross_r:
            frappe.logger().warning(
                f"[Shopee PE] Net ({pe.paid_amount}) > Gross ({gross_r}) SI {si.name}, skip PE"
            )
            return None

        remaining = flt(gross_r - pe.paid_amount, precision)
        total_deductions = 0.0
        if remaining <= 0:
            # Invoice sudah net / tidak perlu deductions (kemungkinan invoice net mode)
            allocated = pe.paid_amount
        else:
            # Urutan prioritas: semua komponen fee Shopee
            ordered_keys = [
                "commission", "service", "protection", "shipdiff",
                "voucher_seller", "voucher_shopee", "coin_cash_back", "credit_card", "voucher_code_seller"
            ]
            for k in ordered_keys:
                raw_v = flt(fees_raw.get(k), precision)
                if raw_v <= 0 or remaining <= 0:
                    continue
                use_v = raw_v if raw_v <= remaining else remaining
                if use_v > 0:
                    row = pe.append("deductions", {})
                    row.account = fee_accounts[k]
                    row.amount = use_v
                    row.cost_center = default_cc
                    total_deductions += use_v
                    remaining = flt(remaining - use_v, precision)

            # Tambahkan diff row untuk sisa jika ada
            if remaining > 0:
                row = pe.append("deductions", {})
                row.account = diff_account
                row.amount = remaining
                row.cost_center = default_cc
                total_deductions += remaining
                remaining = 0.0

            allocated = flt(pe.paid_amount + total_deductions, precision)

        # Guard: jangan alokasikan lebih besar dari grand_total
        if allocated > gross_r:
            allocated = gross_r

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

        try:
            pe = _insert_submit_with_retry(pe)
            return pe.name
        except Exception as e:
            # Tangani duplikat nama karena race condition naming series
            if "Duplicate entry" in str(e):
                # Cari PE terbaru dengan SI reference
                pe_name = frappe.db.get_value(
                    "Payment Entry Reference",
                    {"reference_doctype": "Sales Invoice", "reference_name": si.name},
                    "parent"
                ) or frappe.db.get_value(
                    "Payment Entry",
                    {"reference_no": order_sn, "party": si.customer, "docstatus": ["!=", 2]},
                    "name"
                )
                if pe_name:
                    return pe_name
            raise

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
