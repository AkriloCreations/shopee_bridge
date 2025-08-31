import time, hmac, hashlib, requests, frappe, re, base64  # pyright: ignore[reportMissingImports]
from frappe.utils import get_url, flt, nowdate, cint, add_days, now, format_datetime, get_system_timezone, convert_utc_to_system_timezone, formatdate # pyright: ignore[reportMissingImports]
from datetime import datetime, timedelta, timezone
import json
from .webhook import create_payment_entry_from_shopee
from shopee_bridge.webhook import create_payment_entry_from_shopee, _get_or_create_bank_account

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# 1. Pindahkan LOCK_ERRORS ke bagian atas setelah import
LOCK_ERRORS = ("Lock wait timeout exceeded", "deadlock found")

def _settings():
    return frappe.get_single("Shopee Settings")

def _base():
    """Host Shopee sesuai Environment di Shopee Settings."""
    s = _settings()
    env = (getattr(s, "environment", None) or "Test").strip()
    if env == "Production":
        return "https://partner.shopeemobile.com"
    return "https://partner.test-stable.shopeemobile.com"

def _b64eq(a: str, b: str) -> bool:
    # perbandingan konstan
    return hmac.compare_digest(a, b)

def _safe_int(v, d=0):
    """Convert value to int with fallback default."""
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

def _safe_flt(v, d=0.0):
    """Convert value to float with fallback default."""
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

def _date_iso_from_epoch(ts: int | None) -> str:
    """Epoch detik → 'YYYY-MM-DD' (UTC baseline, cukup untuk tanggal dokumen)."""
    if not ts:
        return frappe.utils.nowdate()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()

def _insert_submit_with_retry(doc, max_tries=3, sleep_base=1.0):
    """
    Insert + submit dengan retry saat kena lock/deadlock.
    Gunakan utk SO/DN/SI.
    """
    last_err = None
    for i in range(max_tries):
        try:
            doc.insert(ignore_permissions=True)
            # commit kecil setelah insert agar kunci cepat lepas
            frappe.db.commit()
            doc.submit()
            frappe.db.commit()
            return doc
        except Exception as e:
            msg = str(e)
            last_err = e
            if any(k.lower() in msg.lower() for k in LOCK_ERRORS):
                frappe.db.rollback()
                time.sleep(sleep_base * (i + 1))  # backoff
                continue
            # error lain: lempar
            raise
    # kalau mentok retry, lempar error terakhir
    raise last_err

def _to_system_dt(ts: int | None):
    """Epoch detik -> datetime aware di system timezone (Frappe v15)."""
    if not ts:
        return None
    dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return convert_utc_to_system_timezone(dt_utc)

def _hum_epoch(ts: int | None):
    """Epoch -> string tanggal+jam sesuai system timezone user/site."""
    dt = _to_system_dt(ts)
    return format_datetime(dt) if dt else None

def _hum_date(ts: int | None):
    """Epoch -> string tanggal sesuai format user."""
    dt = _to_system_dt(ts)
    return formatdate(dt.date()) if dt else None


def _sign(key: str, s: str) -> str:
    return hmac.new((key or "").strip().encode(), s.encode(), hashlib.sha256).hexdigest()

def _call(path: str, partner_id: str, partner_key: str,
    shop_id: str | None = None, access_token: str | None = None,
    params: dict | None = None, timeout: int = 30):
    ts = int(time.time())

    payload = f"{partner_id}{path}{ts}{access_token or ''}{shop_id or ''}"
    sign = _sign(partner_key, payload)

    q = {
        "partner_id": partner_id,
        "timestamp": ts,
        "sign": sign,
    }
    if access_token:
        q["access_token"] = access_token
    if shop_id:
        q["shop_id"] = shop_id

    url = f"{_base()}{path}"

    # Heuristic: endpoint yang mengandung 'get_' → gunakan GET + querystring
    use_get = path.startswith("/api/v2/") and ("/get_" in path or path.endswith("/get"))
    try:
        if use_get:
            # gabungkan params ke querystring
            qp = dict(q)
            if params:
                # Konversi semua value ke str untuk aman di querystring
                for k, v in params.items():
                    qp[k] = str(v)
            r = requests.get(url, params=qp, timeout=timeout)
        else:
            # default: POST body JSON
            r = requests.post(
                url,
                params=q,
                json=(params or {}),
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
        else:
            data = {"error": "HTTP", "message": r.text}

        # Pastikan selalu dict
        if isinstance(data, list):
            data = {"response": {"_list_payload": data}}

        # FIX: Add detailed logging untuk debug
        if path == "/api/v2/shop/get_shop_info":
            frappe.logger().info(f"Shopee API {path} - Status: {r.status_code}")
            frappe.logger().info(f"Shopee API {path} - Response: {data}")
            frappe.logger().info(f"Shopee API {path} - URL: {url}")
            frappe.logger().info(f"Shopee API {path} - Params: {qp if use_get else q}")
        
        return data
    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Shopee API {path} request failed: {str(e)}", "Shopee API Call")
        return {"error": "REQUEST_ERROR", "message": str(e)}

@frappe.whitelist()
def connect_url(app_type: str = "shop"):
    """Bangun URL OAuth. app_type: 'shop' (Seller API) atau 'merchant' (CB/Merchant)."""
    from urllib.parse import quote
    s = frappe.get_single("Shopee Settings")

    path = "/api/v2/shop/auth_partner" if app_type == "shop" else "/api/v2/merchant/auth_partner"
    ts = int(time.time())

    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()

    # sign base string: partner_id + path + timestamp (NO access_token/shop_id for auth)
    sign = _sign(partner_key, f"{partner_id}{path}{ts}")

    # Multiple redirect options - choose the one that works best for your setup
    redirect_options = [
        "https://erp.managerio.ddns.net/app/shopee-settings",  # Direct to settings
        "https://erp.managerio.ddns.net/oauth-callback",       # Via callback page
        "https://erp.managerio.ddns.net/api/method/shopee_bridge.shopee_bridge.doctype.shopee_settings.api.oauth_callback_handler"  # Direct API
    ]
    
    # Use the first option by default, but make it configurable
    redirect = getattr(s, 'oauth_redirect_url', None) or redirect_options[0]

    # urutan param disusun seperti contoh resmi Shopee: partner_id, redirect, timestamp, sign
    url = (
        f"{_base()}{path}"
        f"?partner_id={partner_id}"
        f"&redirect={quote(redirect,safe='')}"
        f"&timestamp={ts}"
        f"&sign={sign}"
    )
    
    return {
        "url": url,
        "redirect_url": redirect,
        "partner_id": partner_id,
        "timestamp": ts,
        "signature": sign
    }

@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, shop_id=None, **kw):
    """Tukar code → access_token & refresh_token lalu simpan."""
    if not code:
        frappe.throw("Authorization code is required")
        
    s = _settings()
    
    # Use exchange_code function for consistency
    try:
        result = exchange_code(code, shop_id)
        return "Shopee connected successfully"
    except Exception as e:
        frappe.throw(f"OAuth callback failed: {str(e)}")

@frappe.whitelist()
def refresh_if_needed(force: int = 0):
    import time, requests, hmac, hashlib
    s = _settings()
    if not s.refresh_token:
        return {"status": "no_refresh_token"}

    safety  = int(getattr(s, "overlap_seconds", 600) or 300)
    now_ts  = int(time.time())
    if not force and s.token_expire_at and (int(s.token_expire_at) - now_ts) > safety:
        return {"status": "token_still_valid", "expires_in": int(s.token_expire_at) - now_ts}

    partner_id  = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    path = "/api/v2/auth/access_token/get"
    ts   = int(time.time())

    # make signature (hex lowercase)
    base_string = f"{partner_id}{path}{ts}".encode()
    digest = hmac.new(partner_key.encode(), base_string, hashlib.sha256).hexdigest()

    # IMPORTANT: put sign in QUERY via params=
    params = {
        "partner_id": partner_id,
        "timestamp": ts,
        "sign": digest,
    }

    body = {
        "partner_id": int(partner_id),
        "refresh_token": s.refresh_token,
    }
    if s.shop_id:
        body["shop_id"] = int(s.shop_id)
    elif getattr(s, "merchant_id", None):
        body["merchant_id"] = int(s.merchant_id)

    try:
        r = requests.post(
            f"{_base()}{path}",
            params=params,                     # <- querystring here
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if "application/json" not in (r.headers.get("content-type") or ""):
            frappe.log_error(f"Bad CT: {r.headers.get('content-type')} | {r.text}", "Shopee Token Refresh")
            return {"status": "error", "message": "Invalid response content-type"}
        data = r.json()
    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Refresh request failed: {e}", "Shopee Token Refresh")
        return {"status": "error", "message": str(e)}

    if data.get("error"):
        return {"status": "error", "message": data.get("message") or "Unknown", "request_id": data.get("request_id")}

    new_access  = data.get("access_token")
    new_refresh = data.get("refresh_token") or s.refresh_token
    expire_in   = int(data.get("expire_in") or 0)
    if not new_access or not expire_in:
        frappe.log_error(f"No token/expiry in resp: {data}", "Shopee Token Refresh")
        return {"status": "no_new_token"}

    s.access_token    = new_access
    s.refresh_token   = new_refresh
    s.token_expire_at = ts + expire_in
    s.last_success_update_time = now_ts
    s.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "refreshed", "expires_in": expire_in, "request_id": data.get("request_id")}

def _default_item_group() -> str:
    """
    Resolusi Item Group untuk sistem yang tidak punya default_item_group field.
    Fallback ke item group yang ada.
    """
    # Langsung fallback ke "Products" atau item group pertama
    if frappe.db.exists("Item Group", "Products"):
        return "Products"
    
    # Cari item group yang bukan group (leaf node) - ambil yang pertama
    names = frappe.db.get_list("Item Group", filters={"is_group": 0}, pluck="name", limit=1)
    if names:
        return names[0]
    
    # Ultimate fallback
    return "All Item Groups"


@frappe.whitelist()
def fix_item_codes_from_shopee(limit: int = 500, dry_run: int = 1):
    """
    Temukan item yang tampak pakai ID (angka panjang) tapi punya custom_model_sku,
    lalu rename ke SKU. dry_run=1 hanya preview.
    """
    rows = frappe.db.get_all(
        "Item",
        fields=["name", "item_code", "custom_model_sku", "custom_shopee_item_id", "custom_shopee_model_id"],
        limit=limit
    )
    changed = []
    for r in rows:
        code = r.item_code or ""
        sku = (r.custom_model_sku or "").strip()
        # heuristik: item_code cuma angka panjang dan tersedia SKU
        if sku and code.isdigit() and len(code) >= 8 and sku != code:
            changed.append({"from": r.name, "to": sku})
            if not dry_run:
                try:
                    frappe.rename_doc("Item", r.name, sku, merge=False, force=True)
                    frappe.db.commit()
                except Exception as e:
                    frappe.log_error(f"Rename {r.name} -> {sku} failed: {e}", "Fix Item Codes")

    return {"preview": dry_run == 1, "changes": changed}

def _ensure_item_exists(sku: str, it: dict, rate: float) -> str:
    """Fixed version of item creation."""
    if not sku:
        sku = f"SHP-UNKNOWN-{it.get('item_id', 'NOITEM')}"
    
    # Check if exists
    if frappe.db.exists("Item", sku):
        return sku
    
    # Create item name
    item_name = it.get("item_name") or it.get("model_name") or sku
    
    try:
        item = frappe.new_doc("Item")
        item.item_code = sku
        item.item_name = item_name[:140]
        item.item_group = "All Item Groups"
        # PERBAIKAN: Gunakan UOM yang lebih umum
        item.stock_uom = _get_default_stock_uom()  # ← Ganti ini
        item.is_stock_item = 1
        item.is_sales_item = 1
        item.maintain_stock = 1
        
        if rate > 0:
            item.standard_rate = rate
            item.valuation_rate = rate
        
        # Add Shopee-specific fields if they exist
        if hasattr(item, 'custom_shopee_item_id'):
            item.custom_shopee_item_id = str(it.get("item_id", ""))  # Changed here
        if hasattr(item, 'custom_shopee_model_id'):
            item.custom_shopee_model_id = str(it.get("model_id", ""))  # Changed here
        if hasattr(item, 'custom_model_sku'):
            item.custom_model_sku = it.get("model_sku", "")  # Changed here
        
        # Description
        desc_parts = []
        if it.get("item_name"):  # Changed here
            desc_parts.append(f"Product: {it.get('item_name')}")
        if it.get("model_name"):  # Changed here
            desc_parts.append(f"Variant: {it.get('model_name')}")
        if it.get("item_id"):  # Changed here
            desc_parts.append(f"Shopee Item ID: {it.get('item_id')}")
        if it.get("model_id"):  # Changed here
            desc_parts.append(f"Model ID: {it.get('model_id')}")
        
        item.description = " | ".join(desc_parts)[:500]
        
        # Set default warehouse in item defaults
        default_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        if default_warehouse:
            item.append("item_defaults", {
                "default_warehouse": default_warehouse,
                "company": frappe.db.get_single_value("Global Defaults", "default_company")
            })
        
        item.insert(ignore_permissions=True)
        frappe.logger().info(f"Created item: {sku}")
        return sku
        
    except Exception as e:
        frappe.log_error(f"Failed to create item {sku}: {e}", "Item Creation")
        
        fallback_sku = "SHOPEE-FALLBACK-ITEM"
        if not frappe.db.exists("Item", fallback_sku):
            try:
                fallback = frappe.new_doc("Item")
                fallback.item_code = fallback_sku
                fallback.item_name = "Shopee Fallback Item"
                fallback.item_group = "All Item Groups"
                fallback.stock_uom = _get_default_stock_uom()  # Ganti "Nos"
                fallback.is_stock_item = 1
                fallback.is_sales_item = 1
                fallback.description = "Fallback item for Shopee products that failed to create"
                fallback.insert(ignore_permissions=True)
            except:
                pass
        
        return fallback_sku

def _get_item_group():
    """Get or create Shopee item group."""
    item_group_name = "Shopee Products"
    if not frappe.db.exists("Item Group", item_group_name):
        try:
            item_group = frappe.new_doc("Item Group")
            item_group.item_group_name = item_group_name
            item_group.parent_item_group = "All Item Groups"
            item_group.is_group = 0
            item_group.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to create item group: {str(e)}", "Shopee Item Group Creation")
            return "All Item Groups"
    return item_group_name

def _get_customer_group():
    """Get or create Shopee customer group."""
    customer_group_name = "Shopee Customers"
    if not frappe.db.exists("Customer Group", customer_group_name):
        try:
            customer_group = frappe.new_doc("Customer Group")
            customer_group.customer_group_name = customer_group_name
            customer_group.parent_customer_group = "All Customer Groups"
            customer_group.is_group = 0
            customer_group.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to create customer group: {str(e)}", "Shopee Customer Group Creation")
            return "All Customer Groups"
    return customer_group_name

def _create_fallback_item(sku: str, item_name: str, rate: float) -> str:
    """Create minimal fallback item if normal creation fails"""
    try:
        # Try with minimal fields
        item = frappe.new_doc("Item")
        item.item_code = sku
        item.item_name = item_name[:140]
        item.item_group = "All Item Groups"  # Use default group as fallback
        item.stock_uom = _get_default_stock_uom()  # Ganti "Nos"
        item.is_stock_item = 1
        item.is_sales_item = 1
        item.standard_rate = rate
        item.insert(ignore_permissions=True)
        return sku
    except Exception as e:
        frappe.log_error(f"Fallback item creation also failed for {sku}: {str(e)}", "Shopee Fallback Item")
        # Return a generic item code - you may want to create a catch-all item
        return _ensure_catch_all_item()

def _ensure_catch_all_item():
    """Ensure catch-all item exists for failed item creation."""
    catch_all_sku = "SHOPEE-UNKNOWN-ITEM"
    if not frappe.db.exists("Item", catch_all_sku):
        try:
            item = frappe.new_doc("Item")
            item.item_code = catch_all_sku
            item.item_name = "Shopee Unknown Item"
            item.item_group = "All Item Groups"
            item.stock_uom = _get_default_stock_uom()  # Ganti "Nos"
            item.is_stock_item = 1
            item.is_sales_item = 1
            item.standard_rate = 0
            item.description = "Fallback item for Shopee products that couldn't be created"
            item.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to create catch-all item: {str(e)}", "Shopee Catch All Item")
    return catch_all_sku

def _ensure_mapping_fields(item_name: str, model_sku: str, item_id: str, model_id: str):
    updates = {}
    if model_sku and not frappe.db.get_value("Item", item_name, "custom_model_sku"):
        updates["custom_model_sku"] = model_sku
    if item_id and not frappe.db.get_value("Item", item_name, "custom_shopee_item_id"):
        updates["custom_shopee_item_id"] = str(item_id)
    if model_id and not frappe.db.get_value("Item", item_name, "custom_shopee_model_id"):
        updates["custom_shopee_model_id"] = str(model_id)
    if updates:
        frappe.db.set_value("Item", item_name, updates)

def _match_or_create_item(it: dict, rate: float) -> str:
    """Cari Item Existing berdasarkan mapping; kalau tidak ada, buat baru + set mapping."""
    model_sku = (it.get("model_sku") or "").strip()
    item_id   = str(it.get("item_id") or "")
    model_id  = str(it.get("model_id") or "0")

    # 1) by item_code / custom_model_sku (pakai model_sku)
    if model_sku:
        name = frappe.db.get_value("Item", {"item_code": model_sku}, "name") \
            or frappe.db.get_value("Item", {"custom_model_sku": model_sku}, "name")
        if name:
            _ensure_mapping_fields(name, model_sku, item_id, model_id)
            return name

    # 2) by custom id (item_id + model_id)
    name = frappe.db.get_value("Item",
                               {"custom_shopee_item_id": item_id, "custom_shopee_model_id": model_id},
                               "name")
    if name:
        _ensure_mapping_fields(name, model_sku, item_id, model_id)
        return name

    # 3) legacy patterns (item_code lama berbasis ID)
    candidates = [f"SHP-{item_id}-{model_id}", f"{item_id}-{model_id}", f"{item_id}_{model_id}"]
    for c in candidates:
        name = frappe.db.get_value("Item", {"item_code": c}, "name")
        if name:
            _ensure_mapping_fields(name, model_sku, item_id, model_id)
            return name

    # 4) tidak ketemu → buat baru (pakai model_sku kalau ada)
    code = model_sku or f"{item_id}-{model_id}"
    itname = (it.get("item_name") or it.get("model_name") or code)[:140]

    item = frappe.new_doc("Item")
    item.item_code =  _fit140(code)
    item.item_name = _fit140(itname)  
    item.item_group = frappe.db.get_single_value("Stock Settings", "default_item_group") or "All Item Groups"
    item.stock_uom = _get_default_stock_uom()  # Ganti "Nos"
    item.is_stock_item = 1
    item.maintain_stock = 1
    # set mapping
    item.custom_model_sku = model_sku or ""
    item.custom_shopee_item_id = item_id
    item.custom_shopee_model_id = model_id

    wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    if wh:
        item.append("item_defaults", {"default_warehouse": wh})
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name

def _short_log(message: str, title: str = "Shopee"):
    """Log error dengan title yang tidak terlalu panjang."""
    # Potong title maksimal 100 karakter untuk menghindari truncation warning
    clean_title = (title or "Shopee")[:100]
    clean_message = (message or "")[:3000]  # Potong message juga
    
    try:
        frappe.log_error(clean_message, clean_title)
    except Exception:
        # Kalau masih error, pakai title yang lebih pendek
        frappe.log_error(clean_message, "Shopee Error")

def _safe_set(doc, fieldname, value):
    """Set field only if it exists on the DocType (hindari exception)."""
    try:
        if hasattr(doc, fieldname):
            setattr(doc, fieldname, value)
    except Exception:
        pass

def _maybe_create_work_orders(so):
    """Auto create Work Order untuk item yang ada BOM (opsional)."""
    try:
        for row in so.items:
            # cek Item & BOM
            item_doc = frappe.get_doc("Item", row.item_code)
            bom_no = getattr(item_doc, "default_bom", None)
            if not bom_no:
                continue

            wo = frappe.new_doc("Work Order")
            wo.company            = so.company
            wo.production_item    = item_doc.name
            wo.qty                = row.qty
            wo.sales_order        = so.name
            wo.bom_no             = bom_no
            wo.planned_start_date = now()
            wo.expected_delivery_date = so.delivery_date
            # warehouse default (boleh diubah)
            wo.wip_warehouse      = frappe.db.get_single_value("Manufacturing Settings", "default_wip_warehouse") \
                                    or frappe.db.get_single_value("Stock Settings", "default_warehouse")
            wo.fg_warehouse       = row.warehouse or frappe.db.get_single_value("Stock Settings", "default_warehouse")
            wo.insert(ignore_permissions=True)
            # kalau mau langsung submit: wo.submit()
    except Exception as e:
        frappe.log_error(f"Auto WO failed for {so.name}: {e}", "Shopee Phase2 WO")

def _ts_to_date(ts):
    if not ts:
        return None
    return _hum_date(ts)

def _compose_customer_name(od):
    uname = (od.get("buyer_username") or "").strip()
    if uname:
        return f"SHP-{uname}"[:140]
    uid = str(od.get("buyer_user_id") or "")[-4:] or "0000"
    return f"SHP-buyer-{uid}"

# --- helpers (ringan, aman ditaruh dekat fungsi utama) -----------------------

def _to_int(x):
    try:
        return int(x)
    except Exception:
        return 0

def _to_flt(x):
    try:
        return float(x)
    except Exception:
        return 0.0

def _date_from_epoch(ts):
    """Return 'YYYY-MM-DD' dari epoch detik. Fallback: nowdate()."""
    try:
        return datetime.utcfromtimestamp(int(ts)).date().isoformat()
    except Exception:
        return nowdate()

def _should_make_so(order_status: str) -> bool:
    """
    Buat SO hanya untuk status2 proses.
    COMPLETED/CANCELLED/RETURNED dlsb -> skip.
    """
    allowed = {"READY_TO_SHIP"}
    return (order_status or "").upper() in allowed

# --- MAIN: buat Sales Order dari satu order_sn (phase 2) ---------------------
@frappe.whitelist()
def _process_order_to_so(order_sn: str):
    """Ambil detail order Shopee lalu buat Sales Order di ERPNext (dedup by po_no)."""
    s = _settings()

    # --- anti duplikat: cek SO yang punya po_no = order_sn
    existed_so = _get_so_by_po(order_sn)
    if existed_so:
        return {"status": "already_exists", "sales_order": existed_so}

    det = _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {
            "order_sn_list": order_sn,
            "response_optional_fields": (
                "buyer_user_id,buyer_username,recipient_address,"
                "item_list,create_time,ship_by_date,days_to_ship,order_status"
            ),
        },
    )
    if det.get("error"):
        frappe.log_error(
            f"Failed to get order detail for {order_sn}: {det.get('message')}",
            "Shopee Order Processing",
        )
        return {"status": "error", "message": det.get("message")}

    lst = (det.get("response") or {}).get("order_list") or []
    if not lst:
        return {"status": "no_data"}
    od = lst[0]

    # Customer
    customer = _create_or_get_customer(od, order_sn)

    # Dates
    transaction_date, delivery_date = _extract_dates_from_order(od)

    # Build SO
    so = frappe.new_doc("Sales Order")
    so.customer = customer
    so.order_type = "Sales"
    so.transaction_date = transaction_date
    so.delivery_date = delivery_date

    # PENTING: dedup pakai field standar Customer's Purchase Order
    so.po_no = order_sn

    # custom field ini tetap boleh diisi hanya sebagai log/reference
    so.custom_shopee_order_sn = order_sn

    so.currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "IDR"
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if company:
        so.company = company
    default_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
    if default_price_list:
        so.selling_price_list = default_price_list

    order_status = od.get("order_status", "UNKNOWN")
    so.remarks = f"Shopee Order {order_sn} | Status: {order_status}"

    items = od.get("item_list") or []
    if not items:
        return {"status": "no_items"}

    default_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    for it in items:
        sku = (it.get("model_sku") or "").strip() or (it.get("item_sku") or "").strip() \
              or f"SHP-{it.get('item_id','UNKNOWN')}-{it.get('model_id','0')}"
        qty = int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
        raw_rate = (it.get("model_discounted_price") or it.get("model_original_price")
                    or it.get("order_price") or it.get("item_price") or "0")
        rate = float(raw_rate)
        base_name = (it.get("item_name") or "").strip()
        model_name = (it.get("model_name") or "").strip()
        item_name = (f"{base_name} - {model_name}".strip(" -") or sku)[:140]

        item_code = _ensure_item_exists(sku, it, rate)

        row = so.append("items", {})
        row.item_code = item_code
        row.item_name = item_name
        row.qty = qty
        row.rate = rate
        row.delivery_date = delivery_date
        if default_warehouse:
            row.warehouse = default_warehouse

    try:
        so.insert(ignore_permissions=True)
        so.submit()
        return {"status": "created", "sales_order": so.name}
    except Exception as e:
        frappe.log_error(
            f"Failed to create Sales Order for {order_sn}: {e}\n"
            f"Order detail: {frappe.as_json(od)}",
            "Sales Order Creation",
        )
        return {"status": "error", "message": str(e)}

# ===== 1. REPLACE _process_order_to_si FUNCTION IN api.py =====
@frappe.whitelist()
def _process_order_to_si(order_sn: str):
    """Shopee order → Sales Invoice (+ auto Payment Entry). Dedup by po_no."""
    s = _settings()

    # --- anti duplikat: cek SI yang punya po_no = order_sn
    existed_si = _get_si_by_po(order_sn)
    if existed_si:
        frappe.logger().info(f"[Shopee] SI {existed_si} for {order_sn} already exists, skipping")
        return {"ok": True, "status": "already_exists", "sales_invoice": existed_si}

    # detail order
    det = _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(), s.partner_key,
        s.shop_id, s.access_token,
        {
            "order_sn_list": order_sn,
            "response_optional_fields": (
                "buyer_user_id,buyer_username,recipient_address,"
                "item_list,create_time,ship_by_date,days_to_ship,order_status"
            ),
        },
    )
    if det.get("error"):
        return {"ok": False, "error": det.get("message")}
    orders = (det.get("response") or {}).get("order_list") or []
    if not orders:
        return {"ok": False, "error": "No order data"}
    od = orders[0]

    # migration/stock handling (tetap sama)
    update_stock = 1
    if cint(getattr(s, "migration_mode", 0)) == 1:
        update_stock = 0
    elif getattr(s, "migration_cutoff_date", None):
        ct = od.get("create_time")
        if ct:
            order_date = datetime.fromtimestamp(int(ct)).date()
            cutoff = frappe.utils.getdate(s.migration_cutoff_date)
            if order_date < cutoff:
                update_stock = 0

    # customer
    customer = _create_or_get_customer(od, order_sn)

    # build SI
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = nowdate()
    si.set_posting_time = 1
    si.update_stock = update_stock
    si.currency = "IDR"

    # PENTING: simpan order_sn ke po_no untuk dedup
    si.po_no = order_sn

    # custom field tetap diisi sebagai log
    si.custom_shopee_order_sn = order_sn

    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if company:
        si.company = company
    default_wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")

    for it in (od.get("item_list") or []):
        sku = (it.get("model_sku") or "").strip() \
              or (it.get("item_sku") or "").strip() \
              or f"SHP-{it.get('item_id')}-{it.get('model_id','0')}"
        qty = _safe_int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased"), 1)
        rate = (
            flt(it.get("model_discounted_price"))
            or flt(it.get("model_original_price"))
            or flt(it.get("order_price"))
            or flt(it.get("item_price"))
        )
        if rate > 1_000_000:
            rate = rate / 100000.0

        item_code = _ensure_item_exists(sku, it, rate)

        row = si.append("items", {})
        row.item_code = item_code
        row.qty = flt(qty)
        row.rate = flt(rate)
        row.amount = flt(qty) * flt(rate)
        if default_wh:
            row.warehouse = default_wh

    if not si.items:
        return {"ok": False, "error": "No items"}

    # insert + submit (fallback no-stock sama seperti sebelumnya)
    try:
        si.insert(ignore_permissions=True)
        si.submit()
    except Exception as e:
        if "needed in" in str(e) and update_stock:
            si.reload()
            si.update_stock = 0
            si.remarks = f"Shopee order SN {order_sn} (Auto: No Stock)"
            si.save()
            si.submit()
        else:
            frappe.log_error(f"Create SI fail {order_sn}: {e}", "Shopee SI Flow")
            return {"ok": False, "error": str(e)}

    # === Escrow → Payment Entry (tetap sama kecuali lookup SI by po_no) ===
    def _norm_esc(esc: dict) -> dict:
        r = (esc or {}).get("response") or (esc or {})
        oi = r.get("order_income") or {}
        payout_amount = flt(r.get("payout_amount") or oi.get("payout_amount"))
        escrow_amount = flt(oi.get("escrow_amount_after_adjustment") or oi.get("escrow_amount") or r.get("escrow_amount"))
        refund_amount = flt(oi.get("refund_amount") or r.get("refund_amount") or 0)
        net = payout_amount or escrow_amount
        ts = r.get("payout_time") or r.get("update_time")
        commission_fee = flt(oi.get("commission_fee"))
        service_fee = flt(oi.get("service_fee")) + flt(oi.get("seller_transaction_fee")) + flt(oi.get("credit_card_transaction_fee"))
        protection = flt(oi.get("delivery_seller_protection_fee_premium_amount"))
        shipdiff = flt(oi.get("reverse_shipping_fee")) - flt(oi.get("shopee_shipping_rebate"))
        voucher_seller = flt(oi.get("voucher_from_seller"))
        coin_cash_back = flt(oi.get("coins"))
        return {
            "net_amount": net,
            "escrow_amount": escrow_amount,
            "payout_amount": payout_amount,
            "commission_fee": commission_fee,
            "service_fee": service_fee,
            "shipping_seller_protection_fee_amount": protection,
            "shipping_fee_difference": shipdiff,
            "voucher_seller": voucher_seller,
            "coin_cash_back": coin_cash_back,
            "voucher_code_seller": 0.0,
            "payout_time": ts,
        }

    esc_raw = _call(
        "/api/v2/payment/get_escrow_detail",
        str(s.partner_id).strip(), s.partner_key,
        s.shop_id, s.access_token,
        {"order_sn": order_sn}
    )
    if esc_raw.get("error"):
        frappe.logger().warning(f"[Shopee] escrow_detail fail {order_sn}: {esc_raw.get('message')}")
        return {"ok": True, "sales_invoice": si.name, "note": "No payment entry created"}

    esc_n = _norm_esc(esc_raw)
    net_amount = flt(esc_n.get("net_amount"))
    refund_amount = flt(esc_n.get("refund_amount"))
    
    # Check for existing SI and CN first
    si_exists = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    if si_exists:
        existing_si = frappe.get_doc("Sales Invoice", si_exists)
        if refund_amount > 0:
            # Check if CN already exists for this SI
            cn_exists = frappe.db.exists("Sales Invoice", {
                "custom_shopee_order_sn": order_sn,
                "return_against": existing_si.name
            })
            if not cn_exists:
                try:
                    # Create Credit Note
                    cn = frappe.new_doc("Sales Invoice")
                    cn.customer = existing_si.customer
                    cn.posting_date = nowdate()
                    cn.set_posting_time = 1
                    cn.is_return = 1
                    cn.return_against = existing_si.name
                    cn.currency = existing_si.currency
                    cn.custom_shopee_order_sn = order_sn
                    cn.po_no = order_sn
                    
                    # Copy items from original SI
                    for item in existing_si.items:
                        cn_item = cn.append("items", {})
                        cn_item.item_code = item.item_code
                        cn_item.qty = -1 * item.qty  # Negative qty for return
                        cn_item.rate = item.rate
                        if item.warehouse:
                            cn_item.warehouse = item.warehouse
                    
                    cn.insert(ignore_permissions=True)
                    cn.submit()
                    frappe.db.commit()
                    
                    frappe.logger().info(f"[Shopee] Created Credit Note for {order_sn} against {existing_si.name}")
                except Exception as e:
                    frappe.log_error(f"Failed to create Credit Note for {order_sn}: {e}", "Shopee CN Creation")
                    
        return {"ok": True, "status": "already_exists", "sales_invoice": existing_si.name}

    if net_amount > 0:
        # jangan buat PE kalau sudah ada referensi ke SI ini
        pe_exists = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si.name}
        )
        if not pe_exists:
            pe_name = create_payment_entry_from_shopee(
                si_name=si.name,
                escrow=esc_n,
                net_amount=net_amount,
                order_sn=order_sn,
                posting_ts=_safe_int(esc_n.get("payout_time") or 0),
                enqueue=False
            )
            return {"ok": True, "sales_invoice": si.name, "payment_entry": pe_name}

    return {"ok": True, "sales_invoice": si.name, "note": "No payment entry created"}

# ===== 2. ADD THESE NEW FUNCTIONS TO api.py =====

@frappe.whitelist()
def toggle_migration_mode(enable=1):
    """Toggle migration mode on/off."""
    s = _settings()
    s.migration_mode = cint(enable)
    if cint(enable):
        # Set cutoff ke hari ini kalau enable
        s.migration_cutoff_date = frappe.utils.today()
    s.save(ignore_permissions=True)
    frappe.db.commit()
    
    return {
        "success": True,
        "migration_mode": bool(s.migration_mode),
        "cutoff_date": s.migration_cutoff_date,
        "message": f"Migration mode {'enabled' if enable else 'disabled'}",
        "note": "Historical orders will not update stock" if enable else "New orders will update stock normally"
    }

@frappe.whitelist()
def check_migration_mode():
    """Check current migration mode status."""
    s = _settings()
    return {
        "success": True,
        "migration_mode": cint(getattr(s, "migration_mode", 0)),
        "cutoff_date": getattr(s, "migration_cutoff_date", None),
        "use_sales_order_flow": cint(getattr(s, "use_sales_order_flow", 0)),
        "environment": getattr(s, "environment", "Test"),
        "shop_id": getattr(s, "shop_id", None),
        "has_token": bool(getattr(s, "access_token", None))
    }

@frappe.whitelist()
def set_migration_cutoff(cutoff_date):
    """Set migration cutoff date manually."""
    s = _settings()
    s.migration_cutoff_date = frappe.utils.getdate(cutoff_date)
    s.save(ignore_permissions=True)
    frappe.db.commit()
    
    return {
        "success": True,
        "cutoff_date": s.migration_cutoff_date,
        "message": f"Orders before {s.migration_cutoff_date} will not update stock"
    }

@frappe.whitelist()
def migration_stats():
    """Get migration statistics."""
    try:
        # Count by stock status in remarks
        with_stock = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "remarks": ["like", "%(With Stock)%"]
        })
        
        without_stock = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "remarks": ["like", "%(No Stock)%"]
        })
        
        fallback_stock = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "remarks": ["like", "%Auto: No Stock%"]
        })
        
        total_shopee = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""]
        })
        
        return {
            "success": True,
            "total_shopee_invoices": total_shopee,
            "with_stock_movement": with_stock,
            "without_stock_movement": without_stock,
            "fallback_no_stock": fallback_stock,
            "unprocessed": total_shopee - with_stock - without_stock - fallback_stock
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

# ===== 3. UPDATE migrate_completed_orders_execute FUNCTION =====
# Replace the existing function with this enhanced version:

@frappe.whitelist()
def migrate_completed_orders_execute(start_date="2024-01-01", end_date="2024-08-31", 
                                   batch_size=50, max_batches=0, skip_existing=1):
    """Execute migration dengan migration mode auto-enabled."""
    from datetime import datetime
    import time
    
    try:
        s = _settings()
        
        if not s.access_token:
            frappe.throw("No access token. Please authenticate with Shopee first.")
        
        # AUTO-ENABLE migration mode untuk historical data
        original_migration_mode = cint(getattr(s, "migration_mode", 0))
        original_flow = cint(getattr(s, "use_sales_order_flow", 0))
        
        # Enable migration mode dan SI flow
        s.migration_mode = 1
        s.use_sales_order_flow = 0
        s.migration_cutoff_date = frappe.utils.today()
        s.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"Auto-enabled migration mode for historical data processing")
        
        try:
            refresh_if_needed()
        except:
            pass
        
        # Convert parameters
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        batch_size = int(batch_size)
        max_batches = int(max_batches) 
        skip_existing = int(skip_existing)
        
        offset = 0
        batch_count = 0
        total_processed = 0
        total_errors = 0
        total_skipped = 0
        batches_detail = []
        
        frappe.logger().info(f"Starting migration: {start_date} to {end_date} (Migration Mode: ON)")
        
        while True:
            batch_count += 1
            
            if max_batches > 0 and batch_count > max_batches:
                frappe.logger().info(f"Reached max batches limit: {max_batches}")
                break
            
            frappe.logger().info(f"Processing batch {batch_count}, offset {offset}")
            
            # Get orders batch
            ol = _call("/api/v2/order/get_order_list",
                       str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                       {
                           "time_range_field": "create_time",
                           "time_from": int(start.timestamp()),
                           "time_to": int(end.timestamp()),
                           "page_size": batch_size,
                           "order_status": "COMPLETED",
                           "offset": offset
                       })
            
            if ol.get("error"):
                error_msg = f"Batch {batch_count} failed: {ol.get('error')} - {ol.get('message')}"
                frappe.log_error(error_msg, "Migration Batch {batch_count}")
                
                # Try token refresh once
                if "access token" in str(ol.get("message", "")).lower():
                    try:
                        refresh_result = refresh_if_needed()
                        if refresh_result.get("status") == "refreshed":
                            frappe.logger().info("Token refreshed, continuing...")
                            continue
                    except:
                        pass
                
                batches_detail.append({
                    "batch": batch_count,
                    "status": "error",
                    "error": error_msg,
                    "processed": 0,
                    "skipped": 0,
                    "errors": 1
                })
                total_errors += 1
                break
            
            response = ol.get("response", {})
            orders = response.get("order_list", [])
            
            if not orders:
                frappe.logger().info("No more orders found")
                break
            
            batch_processed = 0
            batch_skipped = 0 
            batch_errors = 0
            
            # Process each order in batch
            for order in orders:
                order_sn = order.get("order_sn")
                if not order_sn:
                    continue
                
                try:
                    # Skip if already exists
                    if skip_existing:
                        if (frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}) or
                            frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})):
                            batch_skipped += 1
                            continue
                    
                    # Process order (migration mode will ensure no stock movement)
                    result = _process_order_to_si(order_sn)
                    
                    if result and result.get("ok"):
                        batch_processed += 1
                        frappe.logger().info(f"✓ Processed {order_sn} ({result.get('mode', 'Unknown')})")
                    else:
                        batch_errors += 1
                        frappe.logger().warning(f"✗ Failed {order_sn}: {result.get('error', 'Unknown error')}")
                
                except Exception as e:
                    batch_errors += 1
                    frappe.log_error(f"Failed to process order {order_sn}: {str(e)}", "Migration Order Process")
                    frappe.logger().warning(f"✗ Failed {order_sn}: {str(e)}")
                
                # Throttle to avoid API limits
                time.sleep(0.1)
            
            batches_detail.append({
                "batch": batch_count,
                "status": "completed",
                "orders_in_batch": len(orders),
                "processed": batch_processed,
                "skipped": batch_skipped,
                "errors": batch_errors,
                "offset": offset
            })
            
            total_processed += batch_processed
            total_skipped += batch_skipped
            total_errors += batch_errors
            
            frappe.logger().info(f"Batch {batch_count}: {batch_processed} processed, {batch_skipped} skipped, {batch_errors} errors")
            
            # Check if has more pages
            if not response.get("has_next_page"):
                frappe.logger().info("No more pages")
                break
                
            offset = response.get("next_offset", offset + batch_size)
            
            # Sleep between batches to be nice to API
            time.sleep(1)
        
        # RESTORE original settings setelah migration selesai
        s.migration_mode = original_migration_mode
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"Restored original settings: migration_mode={original_migration_mode}, use_sales_order_flow={original_flow}")
        
        result = {
            "success": True,
            "migration_completed": True,
            "period": f"{start_date} to {end_date}",
            "total_batches": batch_count,
            "total_processed": total_processed,
            "total_skipped": total_skipped,
            "total_errors": total_errors,
            "batch_details": batches_detail,
            "settings": {
                "batch_size": batch_size,
                "max_batches": max_batches,
                "skip_existing": bool(skip_existing),
                "migration_mode_used": True
            }
        }
        
        frappe.logger().info(f"Migration completed: {total_processed} processed, {total_errors} errors")
        return result
        
    except Exception as e:
        # Restore settings on error
        try:
            s.migration_mode = original_migration_mode
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
            
        frappe.log_error(f"Migration execute failed: {str(e)}", "Migration Execute")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def migrate_completed_orders_monthly(year=2024, start_month=1, end_month=8, batch_size=50):
    """
    Migrate completed orders bulan per bulan dengan type fixing.
    """
    from datetime import datetime, timedelta
    import calendar
    
    try:
        s = _settings()
        if not s.access_token:
            frappe.throw("No access token. Please authenticate first.")
        
        # Convert to int untuk avoid comparison errors
        year = int(year)
        start_month = int(start_month)
        end_month = int(end_month)
        batch_size = int(batch_size) 
        
        # Force SI flow
        original_flow = getattr(s, "use_sales_order_flow", 0)  
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        monthly_results = []
        total_processed = 0
        total_errors = 0
        
        for month in range(start_month, end_month + 1):
            # Get month boundaries
            start_date = datetime(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = datetime(year, month, last_day, 23, 59, 59)
            
            month_str = start_date.strftime("%B %Y")
            frappe.logger().info(f"Processing month: {month_str}")
            
            # Execute migration for this month using 15-day chunks to handle API limitation
            month_processed = 0
            month_errors = 0
            
            # Split month into 15-day chunks
            current_date = start_date
            while current_date <= end_date:
                chunk_end = min(current_date + timedelta(days=14), end_date)
                
                frappe.logger().info(f"Processing chunk: {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}")
                
                result = migrate_completed_orders_execute(
                    start_date=current_date.strftime("%Y-%m-%d"),
                    end_date=chunk_end.strftime("%Y-%m-%d"),
                    batch_size=batch_size,
                    max_batches=0,  # No limit for chunks
                    skip_existing=1
                )
                
                if result.get("success"):
                    month_processed += result.get("total_processed", 0)
                    month_errors += result.get("total_errors", 0)
                else:
                    month_errors += 1
                    frappe.log_error(f"Chunk failed for {current_date.strftime('%Y-%m-%d')}: {result.get('error')}", "Monthly Migration Chunk")
                
                current_date = chunk_end + timedelta(days=1)
                
                # Sleep between chunks
                import time
                time.sleep(1)
            
            monthly_results.append({
                "month": month_str,
                "processed": month_processed,
                "errors": month_errors,
                "status": "completed" if month_errors == 0 else "partial"
            })
            
            total_processed += month_processed
            total_errors += month_errors
            
            frappe.logger().info(f"Completed {month_str}: {month_processed} orders, {month_errors} errors")
            
            # Sleep between months
            import time
            time.sleep(2)
        
        # Restore setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        return {
            "success": True,
            "migration_type": "monthly",
            "period": f"{start_month}/{year} - {end_month}/{year}", 
            "total_processed": total_processed,
            "total_errors": total_errors,
            "monthly_results": monthly_results
        }
        
    except Exception as e:
        # Restore setting
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
        
        frappe.log_error(f"Monthly migration failed: {str(e)}", "Monthly Migration")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def check_migration_status(start_date="2024-01-01", end_date="2024-08-31"):
    """Check status migrasi - berapa yang sudah ter-migrate"""
    from datetime import datetime
    
    try:
        # Count migrated Sales Invoices
        si_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date]]
        })
        
        # Count migrated Sales Orders  
        so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date]]
        })
        
        # Sample recent migrated
        recent_si = frappe.get_list("Sales Invoice", 
            filters={
                "custom_shopee_order_sn": ["!=", ""],
                "posting_date": ["between", [start_date, end_date]]
            },
            fields=["name", "custom_shopee_order_sn", "posting_date", "grand_total"],
            order_by="creation desc",
            limit=10
        )
        
        # Get Shopee settings
        s = _settings()
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date}",
            "migrated_sales_invoices": si_count,
            "migrated_sales_orders": so_count,
            "total_migrated": si_count + so_count,
            "current_flow": "Sales Order" if getattr(s, "use_sales_order_flow", 0) else "Sales Invoice",
            "token_status": "Valid" if s.access_token else "Missing",
            "recent_migrations": recent_si
        }
        
    except Exception as e:
        return {"error": str(e), "success": False}

# ===== HISTORICAL MIGRATION FUNCTIONS - PASTE KE api.py =====

@frappe.whitelist()
def migrate_completed_orders_preview(start_date="2024-01-01", end_date="2024-01-15"):
    """
    Preview dengan proper type handling.
    """
    from datetime import datetime
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            return {"error": "No access token. Please authenticate first."}
        
        # Refresh token if needed
        try:
            refresh_if_needed()
        except:
            pass
        
        # Sample check - ambil page pertama saja untuk estimasi
        ol = _call("/api/v2/order/get_order_list",
                   str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                   {
                       "time_range_field": "create_time",  # FIX: use create_time for historical
                       "time_from": int(start.timestamp()),
                       "time_to": int(end.timestamp()),
                       "page_size": 100,
                       "order_status": "COMPLETED",
                       "offset": 0
                   })
        
        if ol.get("error"):
            return {
                "error": f"API Error: {ol.get('error')} - {ol.get('message')}",
                "suggestion": "Check your token or try refresh_if_needed()"
            }
        
        response = ol.get("response", {})
        orders = response.get("order_list", [])
        has_more = response.get("has_next_page", False)
        
        # Check sudah ada berapa yang ter-migrate - FIX: proper date comparison
        existing_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        existing_so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date.split()[0]}",
            "sample_orders_found": len(orders),
            "has_more_pages": has_more,
            "estimated_total": "1000+" if has_more else len(orders),
            "already_migrated_si": existing_count,
            "already_migrated_so": existing_so_count,
            "sample_orders": [
                {
                    "order_sn": o.get("order_sn"),
                    "status": o.get("order_status"),
                    "create_time": _hum_epoch(o.get("create_time")) if o.get("create_time") else None
                } for o in orders[:5]
            ],
            "next_step": "Run migrate_completed_orders_execute() if looks good"
        }
        
    except Exception as e:
        frappe.log_error(f"Migration preview failed: {str(e)}", "Migration Preview")
        return {"error": str(e)}

@frappe.whitelist()
def migrate_completed_orders_execute(start_date="2024-01-01", end_date="2024-08-31", 
                                   batch_size=50, max_batches=0, skip_existing=1):
    """Execute migration untuk completed orders dengan type fixing."""
    from datetime import datetime
    import time
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            frappe.throw("No access token. Please authenticate with Shopee first.")
        
        # Force SI flow untuk historical data (completed orders)
        original_flow = getattr(s, "use_sales_order_flow", 0)
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        offset = 0
        batch_count = 0
        total_processed = 0
        total_errors = 0
        total_skipped = 0
        batches_detail = []
        
        # Convert to int untuk avoid type comparison errors
        batch_size = int(batch_size)
        max_batches = int(max_batches) 
        skip_existing = int(skip_existing)
        
        frappe.logger().info(f"Starting migration: {start_date} to {end_date}")
        
        while True:
            batch_count += 1
            
            # Check max batches limit  
            if max_batches > 0 and batch_count > max_batches:
                frappe.logger().info(f"Reached max batches limit: {max_batches}")
                break
            
            frappe.logger().info(f"Processing batch {batch_count}, offset {offset}")
            
            # Get orders batch
            ol = _call("/api/v2/order/get_order_list",
                       str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                       {
                           "time_range_field": "create_time",  # FIX: use create_time for historical
                           "time_from": int(start.timestamp()),
                           "time_to": int(end.timestamp()),
                           "page_size": batch_size,
                           "order_status": "COMPLETED",
                           "offset": offset
                       })
            
            if ol.get("error"):
                error_msg = f"Batch {batch_count} failed: {ol.get('error')} - {ol.get('message')}"
                frappe.log_error(error_msg, "Migration Execute")
                
                # Try token refresh once
                if "access token" in str(ol.get("message", "")).lower():
                    try:
                        refresh_result = refresh_if_needed()
                        if refresh_result.get("status") == "refreshed":
                            frappe.logger().info("Token refreshed, continuing...")
                            continue
                    except:
                        pass
                
                # Skip this batch if error persists
                batches_detail.append({
                    "batch": batch_count,
                    "status": "error",
                    "error": error_msg,
                    "processed": 0,
                    "skipped": 0,
                    "errors": 1
                })
                total_errors += 1
                break
            
            response = ol.get("response", {})
            orders = response.get("order_list", [])
            
            if not orders:
                frappe.logger().info("No more orders found")
                break
            
            batch_processed = 0
            batch_skipped = 0 
            batch_errors = 0
            
            # Process each order in batch
            for order in orders:
                order_sn = order.get("order_sn")
                if not order_sn:
                    continue
                
                try:
                    # Skip if already exists
                    if skip_existing:
                        if (frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}) or
                            frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})):
                            batch_skipped += 1
                            continue
                    
                    # Process order (will create Sales Invoice because we set use_sales_order_flow=0)
                    result = _process_order_to_si(order_sn)
                    
                    if result and result.get("ok"):
                        batch_processed += 1
                        frappe.logger().info(f"✓ Processed {order_sn}")
                    else:
                        batch_errors += 1
                        frappe.logger().warning(f"✗ Failed {order_sn}: {result}")
                
                except Exception as e:
                    batch_errors += 1
                    frappe.log_error(f"Failed to process order {order_sn}: {str(e)}", "Migration Order Process")
                    frappe.logger().warning(f"✗ Failed {order_sn}: {str(e)}")
                
                # Throttle to avoid API limits
                time.sleep(0.1)
            
            batches_detail.append({
                "batch": batch_count,
                "status": "completed",
                "orders_in_batch": len(orders),
                "processed": batch_processed,
                "skipped": batch_skipped,
                "errors": batch_errors,
                "offset": offset
            })
            
            total_processed += batch_processed
            total_skipped += batch_skipped
            total_errors += batch_errors
            
            frappe.logger().info(f"Batch {batch_count}: {batch_processed} processed, {batch_skipped} skipped, {batch_errors} errors")
            
            # Check if has more pages
            if not response.get("has_next_page"):
                frappe.logger().info("No more pages")
                break
                
            offset = response.get("next_offset", offset + batch_size)
            
            # Sleep between batches to be nice to API
            time.sleep(1)
        
        # Restore original setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        result = {
            "success": True,
            "migration_completed": True,
            "period": f"{start_date} to {end_date}",
            "total_batches": batch_count,
            "total_processed": total_processed,
            "total_skipped": total_skipped,
            "total_errors": total_errors,
            "batch_details": batches_detail,
            "settings": {
                "batch_size": batch_size,
                "max_batches": max_batches,
                "skip_existing": bool(skip_existing)
            }
        }
        
        frappe.logger().info(f"Migration completed: {total_processed} processed, {total_errors} errors")
        return result
        
    except Exception as e:
        # Restore setting on error
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
            
        frappe.log_error(f"Migration execute failed: {str(e)}", "Migration Execute")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def migrate_completed_orders_monthly(year=2024, start_month=1, end_month=8, batch_size=50):
    """
    Migrate completed orders bulan per bulan dengan type fixing.
    """
    from datetime import datetime, timedelta
    import calendar
    
    try:
        s = _settings()
        if not s.access_token:
            frappe.throw("No access token. Please authenticate first.")
        
        # Convert to int untuk avoid comparison errors
        year = int(year)
        start_month = int(start_month)
        end_month = int(end_month)
        batch_size = int(batch_size) 
        
        # Force SI flow
        original_flow = getattr(s, "use_sales_order_flow", 0)  
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        monthly_results = []
        total_processed = 0
        total_errors = 0
        
        for month in range(start_month, end_month + 1):
            # Get month boundaries
            start_date = datetime(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = datetime(year, month, last_day, 23, 59, 59)
            
            month_str = start_date.strftime("%B %Y")
            frappe.logger().info(f"Processing month: {month_str}")
            
            # Execute migration for this month using 15-day chunks to handle API limitation
            month_processed = 0
            month_errors = 0
            
            # Split month into 15-day chunks
            current_date = start_date
            while current_date <= end_date:
                chunk_end = min(current_date + timedelta(days=14), end_date)
                
                frappe.logger().info(f"Processing chunk: {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}")
                
                result = migrate_completed_orders_execute(
                    start_date=current_date.strftime("%Y-%m-%d"),
                    end_date=chunk_end.strftime("%Y-%m-%d"),
                    batch_size=batch_size,
                    max_batches=0,  # No limit for chunks
                    skip_existing=1
                )
                
                if result.get("success"):
                    month_processed += result.get("total_processed", 0)
                    month_errors += result.get("total_errors", 0)
                else:
                    month_errors += 1
                    frappe.log_error(f"Chunk failed for {current_date.strftime('%Y-%m-%d')}: {result.get('error')}", "Monthly Migration Chunk")
                
                current_date = chunk_end + timedelta(days=1)
                
                # Sleep between chunks
                import time
                time.sleep(1)
            
            monthly_results.append({
                "month": month_str,
                "processed": month_processed,
                "errors": month_errors,
                "status": "completed" if month_errors == 0 else "partial"
            })
            
            total_processed += month_processed
            total_errors += month_errors
            
            frappe.logger().info(f"Completed {month_str}: {month_processed} orders, {month_errors} errors")
            
            # Sleep between months
            import time
            time.sleep(2)
        
        # Restore setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        return {
            "success": True,
            "migration_type": "monthly",
            "period": f"{start_month}/{year} - {end_month}/{year}", 
            "total_processed": total_processed,
            "total_errors": total_errors,
            "monthly_results": monthly_results
        }
        
    except Exception as e:
        # Restore setting
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
        
        frappe.log_error(f"Monthly migration failed: {str(e)}", "Monthly Migration")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def check_migration_status(start_date="2024-01-01", end_date="2024-08-31"):
    """Check status migrasi - berapa yang sudah ter-migrate"""
    from datetime import datetime
    
    try:
        # Count migrated Sales Invoices
        si_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date]]
        })
        
        # Count migrated Sales Orders  
        so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date]]
        })
        
        # Sample recent migrated
        recent_si = frappe.get_list("Sales Invoice", 
            filters={
                "custom_shopee_order_sn": ["!=", ""],
                "posting_date": ["between", [start_date, end_date]]
            },
            fields=["name", "custom_shopee_order_sn", "posting_date", "grand_total"],
            order_by="creation desc",
            limit=10
        )
        
        # Get Shopee settings
        s = _settings()
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date}",
            "migrated_sales_invoices": si_count,
            "migrated_sales_orders": so_count,
            "total_migrated": si_count + so_count,
            "current_flow": "Sales Order" if getattr(s, "use_sales_order_flow", 0) else "Sales Invoice",
            "token_status": "Valid" if s.access_token else "Missing",
            "recent_migrations": recent_si
        }
        
    except Exception as e:
        return {"error": str(e), "success": False}

# ===== HISTORICAL MIGRATION FUNCTIONS - PASTE KE api.py =====

@frappe.whitelist()
def migrate_completed_orders_preview(start_date="2024-01-01", end_date="2024-01-15"):
    """
    Preview dengan proper type handling.
    """
    from datetime import datetime
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            return {"error": "No access token. Please authenticate first."}
        
        # Refresh token if needed
        try:
            refresh_if_needed()
        except:
            pass
        
        # Sample check - ambil page pertama saja untuk estimasi
        ol = _call("/api/v2/order/get_order_list",
                   str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                   {
                       "time_range_field": "create_time",  # FIX: use create_time for historical
                       "time_from": int(start.timestamp()),
                       "time_to": int(end.timestamp()),
                       "page_size": 100,
                       "order_status": "COMPLETED",
                       "offset": 0
                   })
        
        if ol.get("error"):
            return {
                "error": f"API Error: {ol.get('error')} - {ol.get('message')}",
                "suggestion": "Check your token or try refresh_if_needed()"
            }
        
        response = ol.get("response", {})
        orders = response.get("order_list", [])
        has_more = response.get("has_next_page", False)
        
        # Check sudah ada berapa yang ter-migrate - FIX: proper date comparison
        existing_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        existing_so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date.split()[0]}",
            "sample_orders_found": len(orders),
            "has_more_pages": has_more,
            "estimated_total": "1000+" if has_more else len(orders),
            "already_migrated_si": existing_count,
            "already_migrated_so": existing_so_count,
            "sample_orders": [
                {
                    "order_sn": o.get("order_sn"),
                    "status": o.get("order_status"),
                    "create_time": _hum_epoch(o.get("create_time")) if o.get("create_time") else None
                } for o in orders[:5]
            ],
            "next_step": "Run migrate_completed_orders_execute() if looks good"
        }
        
    except Exception as e:
        frappe.log_error(f"Migration preview failed: {str(e)}", "Migration Preview")
        return {"error": str(e)}

@frappe.whitelist()
def migrate_completed_orders_execute(start_date="2024-01-01", end_date="2024-08-31", 
                                   batch_size=50, max_batches=0, skip_existing=1):
    """Execute migration untuk completed orders dengan type fixing."""
    from datetime import datetime
    import time
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            frappe.throw("No access token. Please authenticate with Shopee first.")
        
        # Force SI flow untuk historical data (completed orders)
        original_flow = getattr(s, "use_sales_order_flow", 0)
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        offset = 0
        batch_count = 0
        total_processed = 0
        total_errors = 0
        total_skipped = 0
        batches_detail = []
        
        # Convert to int untuk avoid type comparison errors
        batch_size = int(batch_size)
        max_batches = int(max_batches) 
        skip_existing = int(skip_existing)
        
        frappe.logger().info(f"Starting migration: {start_date} to {end_date}")
        
        while True:
            batch_count += 1
            
            # Check max batches limit  
            if max_batches > 0 and batch_count > max_batches:
                frappe.logger().info(f"Reached max batches limit: {max_batches}")
                break
            
            frappe.logger().info(f"Processing batch {batch_count}, offset {offset}")
            
            # Get orders batch
            ol = _call("/api/v2/order/get_order_list",
                       str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                       {
                           "time_range_field": "create_time",  # FIX: use create_time for historical
                           "time_from": int(start.timestamp()),
                           "time_to": int(end.timestamp()),
                           "page_size": batch_size,
                           "order_status": "COMPLETED",
                           "offset": offset
                       })
            
            if ol.get("error"):
                error_msg = f"Batch {batch_count} failed: {ol.get('error')} - {ol.get('message')}"
                frappe.log_error(error_msg, "Migration Execute")
                
                # Try token refresh once
                if "access token" in str(ol.get("message", "")).lower():
                    try:
                        refresh_result = refresh_if_needed()
                        if refresh_result.get("status") == "refreshed":
                            frappe.logger().info("Token refreshed, continuing...")
                            continue
                    except:
                        pass
                
                # Skip this batch if error persists
                batches_detail.append({
                    "batch": batch_count,
                    "status": "error",
                    "error": error_msg,
                    "processed": 0,
                    "skipped": 0,
                    "errors": 1
                })
                total_errors += 1
                break
            
            response = ol.get("response", {})
            orders = response.get("order_list", [])
            
            if not orders:
                frappe.logger().info("No more orders found")
                break
            
            batch_processed = 0
            batch_skipped = 0 
            batch_errors = 0
            
            # Process each order in batch
            for order in orders:
                order_sn = order.get("order_sn")
                if not order_sn:
                    continue
                
                try:
                    # Skip if already exists
                    if skip_existing:
                        if (frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}) or
                            frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})):
                            batch_skipped += 1
                            continue
                    
                    # Process order (will create Sales Invoice because we set use_sales_order_flow=0)
                    result = _process_order_to_si(order_sn)
                    
                    if result and result.get("ok"):
                        batch_processed += 1
                        frappe.logger().info(f"✓ Processed {order_sn}")
                    else:
                        batch_errors += 1
                        frappe.logger().warning(f"✗ Failed {order_sn}: {result}")
                
                except Exception as e:
                    batch_errors += 1
                    frappe.log_error(f"Failed to process order {order_sn}: {str(e)}", "Migration Order Process")
                    frappe.logger().warning(f"✗ Failed {order_sn}: {str(e)}")
                
                # Throttle to avoid API limits
                time.sleep(0.1)
            
            batches_detail.append({
                "batch": batch_count,
                "status": "completed",
                "orders_in_batch": len(orders),
                "processed": batch_processed,
                "skipped": batch_skipped,
                "errors": batch_errors,
                "offset": offset
            })
            
            total_processed += batch_processed
            total_skipped += batch_skipped
            total_errors += batch_errors
            
            frappe.logger().info(f"Batch {batch_count}: {batch_processed} processed, {batch_skipped} skipped, {batch_errors} errors")
            
            # Check if has more pages
            if not response.get("has_next_page"):
                frappe.logger().info("No more pages")
                break
                
            offset = response.get("next_offset", offset + batch_size)
            
            # Sleep between batches to be nice to API
            time.sleep(1)
        
        # Restore original setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        result = {
            "success": True,
            "migration_completed": True,
            "period": f"{start_date} to {end_date}",
            "total_batches": batch_count,
            "total_processed": total_processed,
            "total_skipped": total_skipped,
            "total_errors": total_errors,
            "batch_details": batches_detail,
            "settings": {
                "batch_size": batch_size,
                "max_batches": max_batches,
                "skip_existing": bool(skip_existing)
            }
        }
        
        frappe.logger().info(f"Migration completed: {total_processed} processed, {total_errors} errors")
        return result
        
    except Exception as e:
        # Restore setting on error
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
            
        frappe.log_error(f"Migration execute failed: {str(e)}", "Migration Execute")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def migrate_completed_orders_monthly(year=2024, start_month=1, end_month=8, batch_size=50):
    """
    Migrate completed orders bulan per bulan dengan type fixing.
    """
    from datetime import datetime, timedelta
    import calendar
    
    try:
        s = _settings()
        if not s.access_token:
            frappe.throw("No access token. Please authenticate first.")
        
        # Convert to int untuk avoid comparison errors
        year = int(year)
        start_month = int(start_month)
        end_month = int(end_month)
        batch_size = int(batch_size) 
        
        # Force SI flow
        original_flow = getattr(s, "use_sales_order_flow", 0)  
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        monthly_results = []
        total_processed = 0
        total_errors = 0
        
        for month in range(start_month, end_month + 1):
            # Get month boundaries
            start_date = datetime(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = datetime(year, month, last_day, 23, 59, 59)
            
            month_str = start_date.strftime("%B %Y")
            frappe.logger().info(f"Processing month: {month_str}")
            
            # Execute migration for this month using 15-day chunks to handle API limitation
            month_processed = 0
            month_errors = 0
            
            # Split month into 15-day chunks
            current_date = start_date
            while current_date <= end_date:
                chunk_end = min(current_date + timedelta(days=14), end_date)
                
                frappe.logger().info(f"Processing chunk: {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}")
                
                result = migrate_completed_orders_execute(
                    start_date=current_date.strftime("%Y-%m-%d"),
                    end_date=chunk_end.strftime("%Y-%m-%d"),
                    batch_size=batch_size,
                    max_batches=0,  # No limit for chunks
                    skip_existing=1
                )
                
                if result.get("success"):
                    month_processed += result.get("total_processed", 0)
                    month_errors += result.get("total_errors", 0)
                else:
                    month_errors += 1
                    frappe.log_error(f"Chunk failed for {current_date.strftime('%Y-%m-%d')}: {result.get('error')}", "Monthly Migration Chunk")
                
                current_date = chunk_end + timedelta(days=1)
                
                # Sleep between chunks
                import time
                time.sleep(1)
            
            monthly_results.append({
                "month": month_str,
                "processed": month_processed,
                "errors": month_errors,
                "status": "completed" if month_errors == 0 else "partial"
            })
            
            total_processed += month_processed
            total_errors += month_errors
            
            frappe.logger().info(f"Completed {month_str}: {month_processed} orders, {month_errors} errors")
            
            # Sleep between months
            import time
            time.sleep(2)
        
        # Restore setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        return {
            "success": True,
            "migration_type": "monthly",
            "period": f"{start_month}/{year} - {end_month}/{year}", 
            "total_processed": total_processed,
            "total_errors": total_errors,
            "monthly_results": monthly_results
        }
        
    except Exception as e:
        # Restore setting
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
        
        frappe.log_error(f"Monthly migration failed: {str(e)}", "Monthly Migration")
        return {"error": str(e), "success": False}

@frappe.whitelist()
def check_migration_status(start_date="2024-01-01", end_date="2024-08-31"):
    """Check status migrasi - berapa yang sudah ter-migrate"""
    from datetime import datetime
    
    try:
        # Count migrated Sales Invoices
        si_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date]]
        })
        
        # Count migrated Sales Orders  
        so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date]]
        })
        
        # Sample recent migrated
        recent_si = frappe.get_list("Sales Invoice", 
            filters={
                "custom_shopee_order_sn": ["!=", ""],
                "posting_date": ["between", [start_date, end_date]]
            },
            fields=["name", "custom_shopee_order_sn", "posting_date", "grand_total"],
            order_by="creation desc",
            limit=10
        )
        
        # Get Shopee settings
        s = _settings()
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date}",
            "migrated_sales_invoices": si_count,
            "migrated_sales_orders": so_count,
            "total_migrated": si_count + so_count,
            "current_flow": "Sales Order" if getattr(s, "use_sales_order_flow", 0) else "Sales Invoice",
            "token_status": "Valid" if s.access_token else "Missing",
            "recent_migrations": recent_si
        }
        
    except Exception as e:
        return {"error": str(e), "success": False}

# ===== HISTORICAL MIGRATION FUNCTIONS - PASTE KE api.py =====

@frappe.whitelist()
    """Sync Shopee orders → SO (READY_TO_SHIP/PROCESSED), SI+PE (COMPLETED), cancel docs (CANCELLED).
    Menggunakan cursor pagination (v2) & multi-status. Ada fallback pakai create_time jika update_time tidak mengembalikan data.
    Pastikan tidak ada duplikat SO/SI: dedup pakai po_no dan custom_shopee_order_sn.
    """
    s = _settings()
    if not getattr(s, "access_token", ""):
        frappe.throw("Access token required. Please authenticate with Shopee first.")

    # Refresh token bila ada helper-nya
    try:
        if callable(globals().get("refresh_if_needed")):
            refresh_if_needed()
    except Exception:
        pass

    import time as _t
    now = int(_t.time())
    last = int(getattr(s, "last_success_update_time", 0) or 0)
    overlap = int(getattr(s, "overlap_seconds", 600) or 600)

    # first run → pakai hours; next runs → pakai last + overlap
    if last == 0:
        time_from = now - int(hours) * 3600
    else:
        time_from = max(0, last - overlap)
    time_to = now

    # Debug waktu human-readable
    try:
        from datetime import datetime, timezone
        human_from = datetime.fromtimestamp(time_from, tz=timezone.utc).isoformat()
        human_to = datetime.fromtimestamp(time_to, tz=timezone.utc).isoformat()
    except Exception:
        human_from = str(time_from)
        human_to = str(time_to)

    frappe.logger().info(f"[Shopee Sync] Window update_time: {time_from} → {time_to} ({human_from} → {human_to})")

    STATUSES = ("READY_TO_SHIP", "COMPLETED", "CANCELLED")

    stats = {
        "SO": 0,           # created/exists count for SO
        "SI": 0,           # created/exists count for SI
        "PE": 0,           # created PE
        "CANCELLED": 0,    # cancelled docs count
        "errors": 0,
        "api_calls": 0,
    }
    highest_ut = last

    def _pull(status: str, time_field: str) -> list[dict]:
        """Tarik order list dengan cursor pagination."""
        items, cursor, round_ = [], "", 0
        while True:
            params = {
                "time_range_field": time_field,  # "update_time" atau "create_time"
                "time_from": time_from,
                "time_to": time_to,
                "page_size": int(page_size),
                "order_status": status,
            }
            if cursor:
                params["cursor"] = cursor

            resp = _call(
                "/api/v2/order/get_order_list",
                str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token, params
            )
            stats["api_calls"] += 1

            if resp.get("error"):
                frappe.log_error(
                    f"get_order_list[{status},{time_field}] {resp.get('error')} - {resp.get('message')}",
                    "Shopee Sync"
                )
                raise Exception(resp.get("message") or resp.get("error"))

            body = resp.get("response") or {}
            batch = body.get("order_list", []) or []
            items.extend(batch)

            # Shopee v2: pagination pakai 'more' + 'next_cursor'
            if body.get("more"):
                cursor = body.get("next_cursor") or ""
                if not cursor:
                    break
            else:
                break
            round_ += 1
        frappe.logger().info(f"[Shopee Sync] Pulled {len(items)} orders [{status}] with {time_field}")
        return items

    # Helper: pastikan PE dibuat jika fungsi SI tidak sempat membuatnya
    def _ensure_payment(order_sn: str):
        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if not si_name:
            return
        # sudah ada PE utk SI ini?
        pe_exists = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si_name}
        )
        if pe_exists:
            return

        # tarik escrow & buat PE
        esc = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {"order_sn": order_sn}
        )
        stats["api_calls"] += 1
        if esc.get("error"):
            frappe.logger().warning(f"[Shopee Sync] escrow_detail fail {order_sn}: {esc.get('message')}")
            return

        # gunakan normalisasi di create_payment_entry_from_shopee (atau kirim raw)
        try:
            from .webhook import create_payment_entry_from_shopee
            pe_name = create_payment_entry_from_shopee(
                si_name=si_name,
                escrow=esc,
                net_amount=0,                 # biarkan fungsi normalisasi menentukan
                order_sn=order_sn,
                posting_ts=None,
                enqueue=False
            )
            if pe_name:
                stats["PE"] += 1
        except Exception as e:
            stats["errors"] += 1
            frappe.log_error(f"Ensure payment {order_sn} fail: {e}", "Shopee Sync Payment")

    # Helper: dedup check for SO/SI
    def _already_exists(order_sn: str):
        # Cek SO/SI by po_no dan custom_shopee_order_sn
        so_exists = frappe.db.exists("Sales Order", {"po_no": order_sn}) or frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
        si_exists = frappe.db.exists("Sales Invoice", {"po_no": order_sn}) or frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
        return so_exists or si_exists

    # Helper: ensure po_no and custom_shopee_order_sn always filled
    def _ensure_po_no_filled(doc, order_sn):
        try:
            if hasattr(doc, "po_no") and not getattr(doc, "po_no", None):
                doc.po_no = order_sn
            if hasattr(doc, "custom_shopee_order_sn") and not getattr(doc, "custom_shopee_order_sn", None):
                doc.custom_shopee_order_sn = order_sn
        except Exception:
            pass

    # Pass utama: pakai update_time
    for status in STATUSES:
        try:
            orders = _pull(status, "update_time")
        except Exception as e:
            stats["errors"] += 1
            frappe.logger().warning(f"[Shopee Sync] Skip status {status} due to error: {e}")
            continue

        for o in orders:
            order_sn = o.get("order_sn")
            if not order_sn:
                continue
            try:
                ut = int(o.get("update_time") or 0)
                if ut > highest_ut:
                    highest_ut = ut

                # Dedup: skip jika sudah ada SO/SI
                if _already_exists(order_sn):
                    continue

                if status == "READY_TO_SHIP":
                    res = _process_order_to_so(order_sn) or {}
                    # Pastikan po_no dan custom_shopee_order_sn diisi
                    if res.get("sales_order"):
                        try:
                            so = frappe.get_doc("Sales Order", res.get("sales_order"))
                            _ensure_po_no_filled(so, order_sn)
                            so.save(ignore_permissions=True)
                            frappe.db.commit()
                        except Exception:
                            pass
                    if res.get("status") in ("created", "already_exists", "ok"):
                        stats["SO"] += 1

                elif status == "COMPLETED":
                    res = _process_order_to_si(order_sn) or {}
                    # Pastikan po_no dan custom_shopee_order_sn diisi
                    if res.get("sales_invoice"):
                        try:
                            si = frappe.get_doc("Sales Invoice", res.get("sales_invoice"))
                            _ensure_po_no_filled(si, order_sn)
                            si.save(ignore_permissions=True)
                            frappe.db.commit()
                        except Exception:
                            pass
                    if res.get("ok") or res.get("sales_invoice") or frappe.db.exists(
                        "Sales Invoice", {"custom_shopee_order_sn": order_sn}
                    ):
                        stats["SI"] += 1
                        _ensure_payment(order_sn)

                elif status in ("IN_CANCEL", "CANCELLED"):
                    # 1. Cek SI dulu, kalau ada buat CN
                    si_name = _get_si_by_po(order_sn)
                    if si_name:
                        try:
                            si = frappe.get_doc("Sales Invoice", si_name)
                            if si.docstatus == 1:
                                # Check if CN already exists
                                cn_exists = frappe.db.exists("Sales Invoice", {
                                    "custom_shopee_order_sn": order_sn,
                                    "return_against": si_name,
                                    "is_return": 1
                                })
                                if not cn_exists:
                                    # Create Credit Note
                                    cn = frappe.new_doc("Sales Invoice")
                                    cn.customer = si.customer
                                    cn.posting_date = nowdate()
                                    cn.set_posting_time = 1
                                    cn.is_return = 1
                                    cn.return_against = si_name
                                    cn.currency = si.currency
                                    cn.custom_shopee_order_sn = order_sn
                                    cn.po_no = order_sn
                                    cn.remarks = f"Return/Cancel for Shopee Order {order_sn}"
                                    
                                    # Copy items from original SI
                                    for item in si.items:
                                        cn_item = cn.append("items", {})
                                        cn_item.item_code = item.item_code
                                        cn_item.qty = -1 * item.qty  # Negative qty for return
                                        cn_item.rate = item.rate
                                        if item.warehouse:
                                            cn_item.warehouse = item.warehouse
                                    
                                    cn.insert(ignore_permissions=True)
                                    cn.submit()
                                    stats["CN"] += 1
                                    frappe.db.commit()
                            
                        except Exception as e:
                            stats["errors"] += 1
                            frappe.log_error(f"Process return SI {si_name} error: {e}")

                    # 2. Cancel SO jika masih ada
                    so_name = _get_so_by_po(order_sn)
                    if so_name:
                        try:
                            so = frappe.get_doc("Sales Order", so_name)
                            if so.docstatus == 1:
                                so.cancel()
                                stats["CANCELLED"] += 1
                        except Exception as e:
                            stats["errors"] += 1
                            frappe.log_error(f"Cancel SO {so_name} error: {e}")

            except Exception as e:
                stats["errors"] += 1
                frappe.log_error(f"Process {order_sn} [{status}] fail: {e}", "Shopee Sync Loop")

    # Fallback: kalau sama sekali tidak ada order dan tidak ada error, coba pakai create_time
    if stats["SO"] + stats["SI"] + stats["CANCELLED"] == 0 and stats["errors"] == 0:
        frappe.logger().info("[Shopee Sync] No orders via update_time, retrying with create_time window...")
        for status in STATUSES:
            try:
                orders = _pull(status, "create_time")
            except Exception as e:
                stats["errors"] += 1
                frappe.logger().warning(f"[Shopee Sync] (fallback) Skip status {status}: {e}")
                continue

            for o in orders:
                order_sn = o.get("order_sn")
                if not order_sn:
                    continue
                try:
                    ct = int(o.get("create_time") or 0)
                    if ct > highest_ut:
                        highest_ut = ct

                    # Dedup: skip jika sudah ada SO/SI
                    if _already_exists(order_sn):
                        continue

                    if status == "READY_TO_SHIP":
                        res = _process_order_to_so(order_sn) or {}
                        if res.get("sales_order"):
                            try:
                                so = frappe.get_doc("Sales Order", res.get("sales_order"))
                                _ensure_po_no_filled(so, order_sn)
                                so.save(ignore_permissions=True)
                                frappe.db.commit()
                            except Exception:
                                pass
                        if res.get("status") in ("created", "already_exists", "ok"):
                            stats["SO"] += 1

                    elif status == "COMPLETED":
                        res = _process_order_to_si(order_sn) or {}
                        if res.get("sales_invoice"):
                            try:
                                si = frappe.get_doc("Sales Invoice", res.get("sales_invoice"))
                                _ensure_po_no_filled(si, order_sn)
                                si.save(ignore_permissions=True)
                                frappe.db.commit()
                            except Exception:
                                pass
                        if res.get("ok") or res.get("sales_invoice") or frappe.db.exists(
                            "Sales Invoice", {"custom_shopee_order_sn": order_sn}
                        ):
                            stats["SI"] += 1
                            _ensure_payment(order_sn)

                    elif status == "CANCELLED":
                        so_name = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
                        if so_name:
                            try:
                                so = frappe.get_doc("Sales Order", so_name)
                                if so.docstatus == 1:
                                    so.cancel()
                                    stats["CANCELLED"] += 1
                            except Exception as e:
                                stats["errors"] += 1
                                frappe.log_error(f"(fallback) Cancel SO {so_name} error: {e}")
                        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
                        if si_name:
                            try:
                                si = frappe.get_doc("Sales Invoice", si_name)
                                if si.docstatus == 1:
                                    si.cancel()
                                    stats["CANCELLED"] += 1
                            except Exception as e:
                                stats["errors"] += 1
                                frappe.log_error(f"(fallback) Cancel SI {si_name} error: {e}")

                except Exception as e:
                    stats["errors"] += 1
                    frappe.log_error(f"(fallback) Process {order_sn} [{status}] fail: {e}", "Shopee Sync Loop")

    # update watermark hanya jika bergerak maju
    if highest_ut > (getattr(s, "last_success_update_time", 0) or 0):
        s.last_success_update_time = highest_ut
        s.save(ignore_permissions=True)
        frappe.db.commit()

    total_processed = stats["SO"] + stats["SI"] + stats["CANCELLED"]
    frappe.logger().info(f"[Shopee Sync] DONE processed={total_processed} errors={stats['errors']} api_calls={stats['api_calls']}")
    return {
        "from": time_from,
        "to": time_to,
        "processed": {"SO": stats["SO"], "SI": stats["SI"], "PE": stats["PE"], "CANCELLED": stats["CANCELLED"]},
        "errors": stats["errors"],
        "last_update_time": highest_ut,
        "api_calls": stats["api_calls"],
        "success": (total_processed > 0 and stats["errors"] == 0) or (stats["errors"] == 0),
        "window": {"from_iso": human_from, "to_iso": human_to}
    }

@frappe.whitelist()
def force_cancel_shopee_orders(batch_size=250):
    """Force cancel Shopee orders without using bulk operations."""
    orders = frappe.get_all("Sales Order", 
        filters={
            "custom_shopee_order_sn": ["!=", ""], 
            "docstatus": 1
        }, 
        fields=["name", "custom_shopee_order_sn"],
        limit=int(batch_size))
    
    cancelled = []
    errors = []
    
    for order in orders:
        try:
            # Check for linked documents
            linked_si = frappe.db.exists("Sales Invoice", 
                {"custom_shopee_order_sn": order.custom_shopee_order_sn})
            linked_dn = frappe.db.exists("Delivery Note", 
                {"custom_shopee_order_sn": order.custom_shopee_order_sn})
            
            if linked_si or linked_dn:
                errors.append(f"{order.name}: Has linked documents")
                continue
                
            so = frappe.get_doc("Sales Order", order.name)
            so.flags.ignore_permissions = True
            so.flags.ignore_mandatory = True
            so.cancel()
            cancelled.append(order.name)
            
        except Exception as e:
            errors.append(f"{order.name}: {str(e)}")
        
        # Commit every record to avoid transaction locks
        frappe.db.commit()
    
    return {
        "cancelled": len(cancelled),
        "errors": len(errors),
        "cancelled_orders": cancelled,
        "error_details": errors[:5]  # Show first 5 errors
    }

def scheduled_hourly_sync():
    """Scheduled function to sync orders hourly (backup)."""
    try:
        frappe.logger().info("Starting hourly order sync")
        result = sync_recent_orders(hours=1)  # Sync last hour
        
        if result.get("errors", 0) > 0:
            frappe.logger().warning(f"Hourly sync completed with {result.get('errors')} errors")
        else:
            frappe.logger().info(f"Hourly sync completed successfully: {result.get('processed_orders')} orders processed")
            
    except Exception as e:
        frappe.log_error(f"Hourly order sync failed: {str(e)}", "Hourly Order Sync")

def scheduled_cleanup():
    """Scheduled function to cleanup old data."""
    try:
        frappe.logger().info("Starting scheduled cleanup")
        # Add cleanup logic here if needed
        frappe.logger().info("Cleanup completed successfully")
    except Exception as e:
        frappe.log_error(f"Scheduled cleanup failed: {str(e)}", "Scheduled Cleanup")

def _get_default_stock_uom() -> str:
    """Get default stock UOM, prefer Pcs over Nos."""
    # Priority order: Pcs > Unit > Nos > fallback
    preferred_uoms = ["Pcs", "Unit", "Nos"]
    
    for uom in preferred_uoms:
        if frappe.db.exists("UOM", uom):
            return uom
    
    # Ultimate fallback
    return "Nos"
def _match_or_create_item(it: dict, rate: float) -> str:
    """
    Cari atau buat item tanpa mengubah UOM item yang sudah ada.
    """
    model_sku = (it.get("model_sku") or "").strip()
    item_id = str(it.get("item_id") or "")
    model_id = str(it.get("model_id") or "0")

    # 1) Cari berdasarkan SKU
    if model_sku:
        existing = (frappe.db.get_value("Item", {"item_code": model_sku}, "name") or
                   frappe.db.get_value("Item", {"custom_model_sku": model_sku}, "name"))
        if existing:
            # Update mapping fields tanpa mengubah UOM
            _ensure_mapping_fields_safe(existing, model_sku, item_id, model_id)
            return existing

    # 2) Cari berdasarkan custom fields
    existing = frappe.db.get_value("Item", {
        "custom_shopee_item_id": item_id, 
        "custom_shopee_model_id": model_id
    }, "name")
    if existing:
        _ensure_mapping_fields_safe(existing, model_sku, item_id, model_id)
        return existing

    # 3) Cari berdasarkan pola lama
    legacy_codes = [f"SHP-{item_id}-{model_id}", f"{item_id}-{model_id}", f"{item_id}_{model_id}"]
    for code in legacy_codes:
        existing = frappe.db.get_value("Item", {"item_code": code}, "name")
        if existing:
            _ensure_mapping_fields_safe(existing, model_sku, item_id, model_id)
            return existing

    # 4) Buat item baru
    code = model_sku or f"{item_id}-{model_id}"
    nice_name = _clean_title(it.get("model_name") or it.get("item_name") or code)[:140]
    
    defaults = _cfg_defaults()
    
    item = frappe.new_doc("Item")
    item.item_code = _fit140(code)
    item.item_name = _fit140(nice_name)
    item.item_group = defaults["item_group"]
    item.stock_uom = defaults["stock_uom"]
    item.is_stock_item = 1
    item.maintain_stock = 1
    
    # Set custom fields
    for field_name, field_value in [
        ("custom_model_sku", model_sku),
        ("custom_shopee_item_id", item_id),
        ("custom_shopee_model_id", model_id)
    ]:
        try:
            if hasattr(item, field_name):
                setattr(item, field_name, field_value)
        except Exception:
            pass
    
    # Set default warehouse
    if defaults["default_warehouse"]:
        try:
            item.append("item_defaults", {"default_warehouse": defaults["default_warehouse"]})
        except Exception:
            pass
    
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name

def _ensure_mapping_fields_safe(item_name: str, model_sku: str, item_id: str, model_id: str):
    """Update mapping fields tanpa mengubah hal lain."""
    updates = {}
    
    # Hanya update field yang kosong
    current = frappe.db.get_value("Item", item_name, 
        ["custom_model_sku", "custom_shopee_item_id", "custom_shopee_model_id"], 
        as_dict=True)
    
    if not current:
        return
    
    if model_sku and not current.get("custom_model_sku"):
        updates["custom_model_sku"] = model_sku
    if item_id and not current.get("custom_shopee_item_id"):
        updates["custom_shopee_item_id"] = str(item_id)
    if model_id and not current.get("custom_shopee_model_id"):
        updates["custom_shopee_model_id"] = str(model_id)
    
    if updates:
        try:
            frappe.db.set_value("Item", item_name, updates)
        except Exception as e:
            frappe.log_error(f"Failed to update mapping for {item_name}: {e}", "Mapping Update")


def _clean_title(s: str) -> str:
    """Clean up item names."""
    if not s:
        return ""
    s = s.strip()
    # Hapus pola ID yang suka nempel
    import re
    s = re.sub(r"SHP-\d+-\d+", "", s)
    s = re.sub(r"\b\d{6,}\b", "", s)      # token angka panjang
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s



@frappe.whitelist()
def fix_item_names_from_shopee(update: int = 0, limit_pages: int = 999):
    """Perbaiki item_name jadi nama model dari Shopee. Jalankan dulu dengan update=0 (preview)."""
    s = _settings()
    updated, preview = 0, []
    offset, page_size, pages = 0, 50, 0
    while pages < int(limit_pages):
        lst = _call("/api/v2/product/get_item_list",
                    str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token,
                    {"offset": offset, "page_size": page_size})
        resp = lst.get("response") or {}
        items = resp.get("item") or resp.get("items") or []
        for it in items:
            item_id = str(it.get("item_id") or "")
            md = _call("/api/v2/product/get_model_list",
                       str(s.partner_id).strip(), s.partner_key,
                       s.shop_id, s.access_token,
                       {"item_id": int(item_id)})
            models = (md.get("response") or {}).get("model") or (md.get("response") or {}).get("model_list") or []
            for m in models:
                model_id = str(m.get("model_id") or "0")
                model_name = _clean_title(m.get("model_name") or it.get("item_name") or "")
                if not model_name:
                    continue
                # temukan item lokal
                name = (frappe.db.get_value("Item", {"custom_shopee_item_id": item_id,
                                                     "custom_shopee_model_id": model_id}, "name"))
                if not name:
                    for c in (f"SHP-{item_id}-{model_id}", f"{item_id}-{model_id}", f"{item_id}_{model_id}"):
                        name = frappe.db.get_value("Item", {"item_code": c}, "name")
                        if name: break
                if not name: 
                    continue
                cur = frappe.db.get_value("Item", name, "item_name")
                # hanya ubah kalau nama sekarang kelihatan 'ID-ish'
                import re
                looks_bad = (cur == frappe.db.get_value("Item", name, "item_code") or
                             re.search(r"SHP-\d+-\d+", cur or "") or
                             re.fullmatch(r"\d{3,}-\d{1,}", cur or "") or
                             re.fullmatch(r"\d{6,}", cur or ""))
                if looks_bad and cur != model_name:
                    preview.append({"item": name, "from": cur, "to": model_name})
                    if int(update):
                        frappe.db.set_value("Item", name, "item_name", model_name)
                        updated += 1
        if not resp.get("has_next_page"): break
        offset = resp.get("next_offset", offset + page_size); pages += 1
    if int(update): frappe.db.commit()
    return {"updated": updated, "sample": preview[:50], "total_candidates": len(preview)}

@frappe.whitelist()
def debug_get_order_detail(order_sn: str):
    """Ambil 1 order + field opsional selengkap mungkin (v2)."""
    s = _settings()
    optional = ",".join([
        # identitas pembeli (bisa null/masked tergantung region/izin)
        "buyer_user_id","buyer_username",
        # alamat/recipient
        "recipient_address",
        # item & harga
        "item_list","payment_method","total_amount","pay_time",
        # logistik & paket
        "shipping_carrier","package_list","edt",
        # pembatalan
        "buyer_cancel_reason","cancel_by","cancel_reason",
        # lain-lain
        "fulfillment_flag","note","note_update_time","order_chargeable_weight_gram"
    ])
    return _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
        {
            "order_sn_list": str(order_sn),            # WAJIB string, bukan array
            "response_optional_fields": optional,
            # "request_order_status_pending": True,    # hanya jika kamu butuh dukung PENDING (opsional)
        }
    )


@frappe.whitelist()
def diagnose_order(order_sn: str, hours: int = 72):
    """Diagnosa cepat: detail, escrow, dan apakah order muncul di get_order_list dalam window waktu."""
    import time, json
    now = int(time.time())
    s = _settings()

    # detail
    detail = _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
        {"order_sn_list": str(order_sn), "response_optional_fields": "item_list,recipient_address,buyer_info"}
    )

    # escrow
    escrow = _call(
        "/api/v2/payment/get_escrow_detail",
        str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
        {"order_sn": str(order_sn)}
    )

    # visibility di list
    ol = _call(
        "/api/v2/order/get_order_list",
        str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
        {
            "time_range_field": "update_time",
            "time_from": now - int(hours) * 3600,
            "time_to": now,
            "page_size": 50,
            "offset": 0
        }
    )

    visible_in_list = False
    status_hits = []
    if isinstance(ol, dict) and not ol.get("error"):
        for o in (ol.get("response") or {}).get("order_list", []) or []:
            if o.get("order_sn") == order_sn:
                visible_in_list = True
                status_hits.append(o.get("order_status"))

    # ringkas detail
    summary = {}
    if isinstance(detail, dict) and not detail.get("error"):
        lst = (detail.get("response") or {}).get("order_list", []) or []
        if lst:
            od = lst[0]
            # GUNAKAN fungsi utama _safe_int yang sudah ada
            ct  = _safe_int(od.get("create_time"))
            sbd = _safe_int(od.get("ship_by_date"))
            dts = _safe_int(od.get("days_to_ship"))
            if not sbd and ct and dts:
                sbd = ct + dts * 86400

            def _hum(ts):
                try:
                    return _hum_epoch(ts)
                except: 
                    return None

            items = []
            for it in (od.get("item_list") or []):
                items.append({
                    "item_id": it.get("item_id"),
                    "model_id": it.get("model_id"),
                    "item_name": it.get("item_name"),
                    "model_name": it.get("model_name"),
                    "qty": it.get("model_quantity_purchased") or it.get("variation_quantity_purchased"),
                    "price": it.get("model_discounted_price") or it.get("order_price") or it.get("item_price")
                })

            summary = {
                "order_sn": od.get("order_sn"),
                "status": od.get("order_status"),
                "create_time": ct, "create_time_human": _hum(ct),
                "ship_by": sbd,    "ship_by_human": _hum(sbd),
                "buyer_username": od.get("buyer_username"),
                "recipient_name": (od.get("recipient_address") or {}).get("name"),
                "items": items
            }

    esc_summary = {}
    if isinstance(escrow, dict) and not escrow.get("error"):
        er = (escrow.get("response") or {}) or {}
        # GUNAKAN fungsi utama _safe_flt yang sudah ada
        esc_summary = {
            "net": _safe_flt(er.get("escrow_amount") or er.get("payout_amount") or er.get("net_amount")),
            "commission": _safe_flt(er.get("seller_commission_fee") or er.get("commission_fee")),
            "service": _safe_flt(er.get("seller_service_fee") or er.get("service_fee")),
            "protection": _safe_flt(er.get("shipping_seller_protection_fee_amount")),
            "shipdiff": _safe_flt(er.get("shipping_fee_difference")),
            "voucher": _safe_flt(er.get("voucher_seller")) + _safe_flt(er.get("coin_cash_back")) + _safe_flt(er.get("voucher_code_seller")),
        }

    return {
        "ok": bool(summary),
        "order_sn": order_sn,
        "visible_in_list": visible_in_list,
        "visible_status_hits": status_hits,
        "detail_error": detail if detail.get("error") else None,
        "escrow_error": escrow if escrow.get("error") else None,
        "detail": summary if summary else None,
        "escrow": esc_summary if esc_summary else None,
    }

@frappe.whitelist()
def force_cancel_shopee_orders(batch_size=250):
    """Force cancel Shopee orders without using bulk operations."""
    orders = frappe.get_all("Sales Order", 
        filters={
            "custom_shopee_order_sn": ["!=", ""], 
            "docstatus": 1
        }, 
        fields=["name", "custom_shopee_order_sn"],
        limit=int(batch_size))
    
    cancelled = []
    errors = []
    
    for order in orders:
        try:
            # Check for linked documents
            linked_si = frappe.db.exists("Sales Invoice", 
                {"custom_shopee_order_sn": order.custom_shopee_order_sn})
            linked_dn = frappe.db.exists("Delivery Note", 
                {"custom_shopee_order_sn": order.custom_shopee_order_sn})
            
            if linked_si or linked_dn:
                errors.append(f"{order.name}: Has linked documents")
                continue
                
            so = frappe.get_doc("Sales Order", order.name)
            so.flags.ignore_permissions = True
            so.flags.ignore_mandatory = True
            so.cancel()
            cancelled.append(order.name)
            
        except Exception as e:
            errors.append(f"{order.name}: {str(e)}")
        
        # Commit every record to avoid transaction locks
        frappe.db.commit()
    
    return {
        "cancelled": len(cancelled),
        "errors": len(errors),
        "cancelled_orders": cancelled,
        "error_details": errors[:5]  # Show first 5 errors
    }

def scheduled_hourly_sync():
    """Scheduled function to sync orders hourly (backup)."""
    try:
        frappe.logger().info("Starting hourly order sync")
        result = sync_recent_orders(hours=1)  # Sync last hour
        
        if result.get("errors", 0) > 0:
            frappe.logger().warning(f"Hourly sync completed with {result.get('errors')} errors")
        else:
            frappe.logger().info(f"Hourly sync completed successfully: {result.get('processed_orders')} orders processed")
            
    except Exception as e:
        frappe.log_error(f"Hourly order sync failed: {str(e)}", "Hourly Order Sync")

def scheduled_cleanup():
    """Scheduled function to cleanup old data."""
    try:
        frappe.logger().info("Starting scheduled cleanup")
        # Add cleanup logic here if needed
        frappe.logger().info("Cleanup completed successfully")
    except Exception as e:
        frappe.log_error(f"Scheduled cleanup failed: {str(e)}", "Scheduled Cleanup")

def _get_default_stock_uom() -> str:
    """Get default stock UOM, prefer Pcs over Nos."""
    # Priority order: Pcs > Unit > Nos > fallback
    preferred_uoms = ["Pcs", "Unit", "Nos"]
    
    for uom in preferred_uoms:
        if frappe.db.exists("UOM", uom):
            return uom
    
    # Ultimate fallback
    return "Nos"

@frappe.whitelist()
def manual_exchange_code(code: str, shop_id: str = None):
    """Manual exchange authorization code ke access token - bisa dipanggil dari browser/client"""
    try:
        if not code or not code.strip():
            return {
                "status": "error",
                "message": "Authorization code is required"
            }
        
        result = exchange_code(code, shop_id)
        
        if result.get("ok"):
            return {
                "status": "success",
                "message": "Successfully exchanged authorization code for tokens",
                "data": {
                    "shop_id": result.get("shop_id"),
                    "token_expires_at": result.get("expire_at"),
                    "access_token_preview": result.get("access_token_preview")
                }
            }
        else:
            return {
                "status": "error", 
                "message": "Failed to exchange code",
                "data": result
            }
            
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Manual Exchange Code Error")
        return {
            "status": "error",
            "message": str(e)
        }

@frappe.whitelist()
def manual_token_refresh():
    """Manual refresh token - bisa dipanggil dari client script"""
    try:
        result = refresh_if_needed()
        return {
            "status": "success" if result.get("status") == "refreshed" else result.get("status", "unknown"),
            "message": result.get("message", "Token refresh completed"),
            "data": result
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Manual Token Refresh Error")
        return {
            "status": "error",
            "message": str(e)
        }

@frappe.whitelist()
def get_oauth_url():
    """Generate OAuth URL untuk mendapatkan authorization code"""
    try:
        result = connect_url("shop")
        return {
            "status": "success",
            "message": "OAuth URL generated successfully",
            "data": {
                "oauth_url": result.get("url"),
                "redirect_url": result.get("redirect_url"),
                "partner_id": result.get("partner_id")
            }
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "OAuth URL Generation Error")
        return {
            "status": "error",
            "message": str(e)
        }

@frappe.whitelist()
def get_token_status():
    """Get current token status and info"""
    try:
        s = _settings()
        
        token_valid = bool(s.access_token)
        expires_soon = False
        expires_in = None
        
        if s.token_expire_at:
            import time
            current_time = int(time.time())
            expires_in = int(s.token_expire_at) - current_time
            expires_soon = expires_in < 300  # Less than 5 minutes
        
        return {
            "status": "success",
            "data": {
                "has_access_token": token_valid,
                "has_refresh_token": bool(s.refresh_token),
                "shop_id": s.shop_id,
                "partner_id": s.partner_id,
                "environment": s.environment,
                "token_expires_in": expires_in,
                "token_expires_soon": expires_soon,
                "access_token_preview": s.access_token[:20] + "..." if s.access_token else None
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

# ===== HISTORICAL MIGRATION FUNCTIONS - PASTE KE api.py =====

@frappe.whitelist()
def migrate_completed_orders_preview(start_date="2024-01-01", end_date="2024-01-15"):
    """
    Preview dengan proper type handling.
    """
    from datetime import datetime
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            return {"error": "No access token. Please authenticate first."}
        
        # Refresh token if needed
        try:
            refresh_if_needed()
        except:
            pass
        
        # Sample check - ambil page pertama saja untuk estimasi
        ol = _call("/api/v2/order/get_order_list",
                   str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                   {
                       "time_range_field": "create_time",  # FIX: use create_time for historical
                       "time_from": int(start.timestamp()),
                       "time_to": int(end.timestamp()),
                       "page_size": 100,
                       "order_status": "COMPLETED",
                       "offset": 0
                   })
        
        if ol.get("error"):
            return {
                "error": f"API Error: {ol.get('error')} - {ol.get('message')}",
                "suggestion": "Check your token or try refresh_if_needed()"
            }
        
        response = ol.get("response", {})
        orders = response.get("order_list", [])
        has_more = response.get("has_next_page", False)
        
        # Check sudah ada berapa yang ter-migrate - FIX: proper date comparison
        existing_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        existing_so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date.split()[0]]]  # Remove time part
        })
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date.split()[0]}",
            "sample_orders_found": len(orders),
            "has_more_pages": has_more,
            "estimated_total": "1000+" if has_more else len(orders),
            "already_migrated_si": existing_count,
            "already_migrated_so": existing_so_count,
            "sample_orders": [
                {
                    "order_sn": o.get("order_sn"),
                    "status": o.get("order_status"),
                    "create_time": _hum_epoch(o.get("create_time")) if o.get("create_time") else None
                } for o in orders[:5]
            ],
            "next_step": "Run migrate_completed_orders_execute() if looks good"
        }
        
    except Exception as e:
        frappe.log_error(f"Migration preview failed: {str(e)}", "Migration Preview")
        return {"error": str(e)}

@frappe.whitelist()
def migrate_completed_orders_execute(start_date="2024-01-01", end_date="2024-08-31", 
                                   batch_size=50, max_batches=0, skip_existing=1):
    """
    Execute migration untuk completed orders dengan type fixing.
    """
    from datetime import datetime
    import time
    
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S")
        
        s = _settings()
        
        if not s.access_token:
            frappe.throw("No access token. Please authenticate with Shopee first.")
        
        # Force SI flow untuk historical data (completed orders)
        original_flow = getattr(s, "use_sales_order_flow", 0)
        s.use_sales_order_flow = 0  # Force Sales Invoice flow
        s.save(ignore_permissions=True)
        
        try:
            refresh_if_needed()
        except:
            pass
        
        offset = 0
        batch_count = 0
        total_processed = 0
        total_errors = 0
        total_skipped = 0
        batches_detail = []
        
        # Convert to int untuk avoid type comparison errors
        batch_size = int(batch_size)
        max_batches = int(max_batches) 
        skip_existing = int(skip_existing)
        
        frappe.logger().info(f"Starting migration: {start_date} to {end_date}")
        
        while True:
            batch_count += 1
            
            # Check max batches limit  
            if max_batches > 0 and batch_count > max_batches:
                frappe.logger().info(f"Reached max batches limit: {max_batches}")
                break
            
            frappe.logger().info(f"Processing batch {batch_count}, offset {offset}")
            
            # Get orders batch
            ol = _call("/api/v2/order/get_order_list",
                       str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                       {
                           "time_range_field": "create_time",  # FIX: use create_time for historical
                           "time_from": int(start.timestamp()),
                           "time_to": int(end.timestamp()),
                           "page_size": batch_size,
                           "order_status": "COMPLETED",
                           "offset": offset
                       })
            
            if ol.get("error"):
                error_msg = f"Batch {batch_count} failed: {ol.get('error')} - {ol.get('message')}"
                frappe.log_error(error_msg, "Migration Execute")
                
                # Try token refresh once
                if "access token" in str(ol.get("message", "")).lower():
                    try:
                        refresh_result = refresh_if_needed()
                        if refresh_result.get("status") == "refreshed":
                            frappe.logger().info("Token refreshed, continuing...")
                            continue
                    except:
                        pass
                
                # Skip this batch if error persists
                batches_detail.append({
                    "batch": batch_count,
                    "status": "error",
                    "error": error_msg,
                    "processed": 0,
                    "skipped": 0,
                    "errors": 1
                })
                total_errors += 1
                break
            
            response = ol.get("response", {})
            orders = response.get("order_list", [])
            
            if not orders:
                frappe.logger().info("No more orders found")
                break
            
            batch_processed = 0
            batch_skipped = 0 
            batch_errors = 0
            
            # Process each order in batch
            for order in orders:
                order_sn = order.get("order_sn")
                if not order_sn:
                    continue
                
                try:
                    # Skip if already exists
                    if skip_existing:
                        if (frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}) or
                            frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})):
                            batch_skipped += 1
                            continue
                    
                    # Process order (will create Sales Invoice because we set use_sales_order_flow=0)
                    result = _process_order_to_si(order_sn)
                    
                    if result and result.get("ok"):
                        batch_processed += 1
                        frappe.logger().info(f"✓ Processed {order_sn}")
                    else:
                        batch_errors += 1
                        frappe.logger().warning(f"✗ Failed {order_sn}: {result}")
                
                except Exception as e:
                    batch_errors += 1
                    frappe.log_error(f"Failed to process order {order_sn}: {str(e)}", "Migration Order Process")
                    frappe.logger().warning(f"✗ Failed {order_sn}: {str(e)}")
                
                # Throttle to avoid API limits
                time.sleep(0.1)
            
            batches_detail.append({
                "batch": batch_count,
                "status": "completed",
                "orders_in_batch": len(orders),
                "processed": batch_processed,
                "skipped": batch_skipped,
                "errors": batch_errors,
                "offset": offset
            })
            
            total_processed += batch_processed
            total_skipped += batch_skipped
            total_errors += batch_errors
            
            frappe.logger().info(f"Batch {batch_count}: {batch_processed} processed, {batch_skipped} skipped, {batch_errors} errors")
            
            # Check if has more pages
            if not response.get("has_next_page"):
                frappe.logger().info("No more pages")
                break
                
            offset = response.get("next_offset", offset + batch_size)
            
            # Sleep between batches to be nice to API
            time.sleep(1)
        
        # Restore original setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        result = {
            "success": True,
            "migration_completed": True,
            "period": f"{start_date} to {end_date}",
            "total_batches": batch_count,
            "total_processed": total_processed,
            "total_skipped": total_skipped,
            "total_errors": total_errors,
            "batch_details": batches_detail,
            "settings": {
                "batch_size": batch_size,
                "max_batches": max_batches,
                "skip_existing": bool(skip_existing)
            }
        }
        
        frappe.logger().info(f"Migration completed: {total_processed} processed, {total_errors} errors")
        return result
        
    except Exception as e:
        # Restore setting on error
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
            
        frappe.log_error(f"Migration execute failed: {str(e)}", "Migration Execute")
        return {"error": str(e), "success": False}


@frappe.whitelist()
def migrate_completed_orders_monthly(year=2024, start_month=1, end_month=8, batch_size=50):
    """
    Migrate completed orders bulan per bulan dengan type fixing.
    """
    from datetime import datetime, timedelta
    import calendar
    
    try:
        s = _settings()
        if not s.access_token:
            frappe.throw("No access token. Please authenticate first.")
        
        # Convert to int untuk avoid comparison errors
        year = int(year)
        start_month = int(start_month)
        end_month = int(end_month)
        batch_size = int(batch_size)
        
        # Force SI flow
        original_flow = getattr(s, "use_sales_order_flow", 0)  
        s.use_sales_order_flow = 0
        s.save(ignore_permissions=True)
        
        monthly_results = []
        total_processed = 0
        total_errors = 0
        
        for month in range(start_month, end_month + 1):
            # Get month boundaries
            start_date = datetime(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end_date = datetime(year, month, last_day, 23, 59, 59)
            
            month_str = start_date.strftime("%B %Y")
            frappe.logger().info(f"Processing month: {month_str}")
            
            # Execute migration for this month using 15-day chunks to handle API limitation
            month_processed = 0
            month_errors = 0
            
            # Split month into 15-day chunks
            current_date = start_date
            while current_date <= end_date:
                chunk_end = min(current_date + timedelta(days=14), end_date)
                
                frappe.logger().info(f"Processing chunk: {current_date.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}")
                
                result = migrate_completed_orders_execute(
                    start_date=current_date.strftime("%Y-%m-%d"),
                    end_date=chunk_end.strftime("%Y-%m-%d"),
                    batch_size=batch_size,
                    max_batches=0,  # No limit for chunks
                    skip_existing=1
                )
                
                if result.get("success"):
                    month_processed += result.get("total_processed", 0)
                    month_errors += result.get("total_errors", 0)
                else:
                    month_errors += 1
                    frappe.log_error(f"Chunk failed for {current_date.strftime('%Y-%m-%d')}: {result.get('error')}", "Monthly Migration Chunk")
                
                current_date = chunk_end + timedelta(days=1)
                
                # Sleep between chunks
                import time
                time.sleep(1)
            
            monthly_results.append({
                "month": month_str,
                "processed": month_processed,
                "errors": month_errors,
                "status": "completed" if month_errors == 0 else "partial"
            })
            
            total_processed += month_processed
            total_errors += month_errors
            
            frappe.logger().info(f"Completed {month_str}: {month_processed} orders, {month_errors} errors")
            
            # Sleep between months
            import time
            time.sleep(2)
        
        # Restore setting
        s.use_sales_order_flow = original_flow
        s.save(ignore_permissions=True)
        
        return {
            "success": True,
            "migration_type": "monthly",
            "period": f"{start_month}/{year} - {end_month}/{year}", 
            "total_processed": total_processed,
            "total_errors": total_errors,
            "monthly_results": monthly_results
        }
        
    except Exception as e:
        # Restore setting
        try:
            s.use_sales_order_flow = original_flow
            s.save(ignore_permissions=True)
        except:
            pass
        
        frappe.log_error(f"Monthly migration failed: {str(e)}", "Monthly Migration")
        return {"error": str(e), "success": False}



@frappe.whitelist()  
def check_migration_status(start_date="2024-01-01", end_date="2024-08-31"):
    """Check status migrasi - berapa yang sudah ter-migrate"""
    from datetime import datetime
    
    try:
        # Count migrated Sales Invoices
        si_count = frappe.db.count("Sales Invoice", {
            "custom_shopee_order_sn": ["!=", ""],
            "posting_date": ["between", [start_date, end_date]]
        })
        
        # Count migrated Sales Orders  
        so_count = frappe.db.count("Sales Order", {
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date, end_date]]
        })
        
        # Sample recent migrated
        recent_si = frappe.get_list("Sales Invoice", 
            filters={
                "custom_shopee_order_sn": ["!=", ""],
                "posting_date": ["between", [start_date, end_date]]
            },
            fields=["name", "custom_shopee_order_sn", "posting_date", "grand_total"],
            order_by="creation desc",
            limit=10
        )
        
        # Get Shopee settings
        s = _settings()
        
        return {
            "success": True,
            "period": f"{start_date} to {end_date}",
            "migrated_sales_invoices": si_count,
            "migrated_sales_orders": so_count,
            "total_migrated": si_count + so_count,
            "current_flow": "Sales Order" if getattr(s, "use_sales_order_flow", 0) else "Sales Invoice",
            "token_status": "Valid" if s.access_token else "Missing",
            "recent_migrations": recent_si
        }
        
    except Exception as e:
        return {"error": str(e), "success": False}

LOCK_ERRORS = (
    "deadlock", "lock wait timeout", "locked", "1213", "1205",
    "could not obtain lock", "too many connections"
)

@frappe.whitelist()
def complete_order_to_si(order_sn: str):
    """Convert Sales Order Shopee → Sales Invoice + Payment Entry."""
    try:
        # Cari SO dulu
        so_name = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
        if not so_name:
            return {"ok": False, "error": f"No Sales Order found for {order_sn}"}

        so = frappe.get_doc("Sales Order", so_name)

        # Kalau sudah ada SI, skip
        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if si_name:
            return {"ok": True, "status": "already_invoiced", "sales_invoice": si_name}

        # Buat SI dari SO
        si = frappe.get_doc(so).make_sales_invoice()
        si.custom_shopee_order_sn = order_sn
        si.posting_date = nowdate()
        si.set_posting_time = 1
        si.update_stock = 0  # biar ga bikin negative stock
        si.insert(ignore_permissions=True)
        si.submit()

        # Ambil escrow detail
        s = _settings()
        esc = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(),
            s.partner_key,
            s.shop_id,
            s.access_token,
            {"order_sn": order_sn}
        )
        escrow = (esc.get("response") or {}) if not esc.get("error") else {}

        net = flt(escrow.get("escrow_amount") or escrow.get("payout_amount") or 0)
        if net > 0:
            pe = frappe.new_doc("Payment Entry")
            pe.payment_type = "Receive"
            pe.party_type = "Customer"
            pe.party = so.customer
            pe.posting_date = nowdate()
            pe.mode_of_payment = "Shopee"
            pe.paid_from = frappe.db.get_single_value("Accounts Settings", "default_receivable_account") or "Debtors - AC"
            pe.paid_to = "Bank - Shopee (Escrow)"
            pe.paid_amount = net
            pe.received_amount = net
            pe.reference_no = order_sn

            ref = pe.append("references", {})
            ref.reference_doctype = "Sales Invoice"
            ref.reference_name = si.name
            ref.allocated_amount = net

            pe.insert(ignore_permissions=True)
            pe.submit()

            return {"ok": True, "status": "completed", "sales_invoice": si.name, "payment_entry": pe.name}

        return {"ok": True, "status": "invoiced_no_payment", "sales_invoice": si.name}

    except Exception as e:
        frappe.log_error(f"Complete order {order_sn} fail: {e}", "Shopee Complete Order")
        return {"ok": False, "error": str(e)}


@frappe.whitelist()
def cancel_order(order_sn: str):
    """Cancel SO / SI jika order Shopee dibatalkan."""
    cancelled = []

    try:
        so_name = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
        if so_name:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                if so.docstatus == 1:
                    so.cancel()
                    cancelled.append(so.name)
            except Exception as e:
                frappe.log_error(f"Cancel SO {so_name} error: {e}")

        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if si_name:
            try:
                si = frappe.get_doc("Sales Invoice", si_name)
                if si.docstatus == 1:
                    si.cancel()
                    cancelled.append(si.name)
            except Exception as e:
                frappe.log_error(f"Cancel SI {si_name} error: {e}")

        return {"ok": True, "cancelled": cancelled}

    except Exception as e:
        frappe.log_error(f"Cancel order {order_sn} fail: {e}", "Shopee Cancel Order")
        return {"ok": False, "error": str(e)}

@frappe.whitelist()
def cleanup_shopee_duplicates(order_sn: str, dry_run: int = 1, prefer: str = "latest") -> dict:
    """
    Cancel & delete dokumen Shopee duplikat berdasarkan order_sn (pakai po_no).
    - Prioritas identifikasi pakai Sales Order / Sales Invoice dengan po_no = order_sn.
    - 'prefer': "latest" (keep yang terbaru) atau "earliest" (keep yang tertua).
    - Urutan aman:
        1) Payment Entry yg refer ke SI → cancel & delete dulu
        2) Sales Invoice → cancel & delete (kecuali 1 yang dipertahankan)
        3) Sales Order   → cancel & delete (kecuali 1 yang dipertahankan)
    - default dry_run=1 → hanya laporan, tidak ada perubahan.
    """
    assert order_sn, "order_sn wajib diisi"
    summary = {"order_sn": order_sn, "dry_run": bool(int(dry_run)), "prefer": prefer, "kept": {}, "deleted": [], "errors": []}

    def _pick_keep(rows):
        if not rows:
            return None
        rows = sorted(rows, key=lambda r: (r["modified"], r["name"]))
        return rows[-1] if prefer == "latest" else rows[0]

    try:
        # Kumpulkan semua SI dan SO yang po_no = order_sn
        sis = frappe.get_all(
            "Sales Invoice",
            filters={"po_no": order_sn},
            fields=["name", "posting_date", "modified", "docstatus"]
        )
        sos = frappe.get_all(
            "Sales Order",
            filters={"po_no": order_sn},
            fields=["name", "transaction_date as posting_date", "modified", "docstatus"]
        )

        # Tentukan siapa yang di-keep
        si_keep = _pick_keep(sis)
        so_keep = _pick_keep(sos)
        if si_keep: summary["kept"]["Sales Invoice"] = si_keep["name"]
        if so_keep: summary["kept"]["Sales Order"] = so_keep["name"]

        # 1) Bersihkan Payment Entry yang mengacu ke SEMUA SI (termasuk yang akan dihapus)
        for si in sis:
            # Skip PE untuk SI yang dipertahankan
            if si_keep and si["name"] == si_keep["name"]:
                continue
            refs = frappe.get_all(
                "Payment Entry Reference",
                filters={"reference_doctype": "Sales Invoice", "reference_name": si["name"]},
                fields=["parent"]
            )
            for r in refs:
                try:
                    pe = frappe.get_doc("Payment Entry", r["parent"])
                    if int(dry_run):
                        summary["deleted"].append({"doctype": "Payment Entry", "name": pe.name, "action": "would_cancel_delete"})
                        continue
                    if pe.docstatus == 1:
                        pe.cancel()
                    frappe.delete_doc("Payment Entry", pe.name, ignore_permissions=True, force=True)
                    summary["deleted"].append({"doctype": "Payment Entry", "name": pe.name, "action": "cancel_delete"})
                except Exception as e:
                    summary["errors"].append(f"PE {r['parent']} → {e}")

        # 2) Hapus SI duplikat (kecuali yang di-keep)
        for si in sis:
            if si_keep and si["name"] == si_keep["name"]:
                continue
            try:
                doc = frappe.get_doc("Sales Invoice", si["name"])
                if int(dry_run):
                    summary["deleted"].append({"doctype": "Sales Invoice", "name": doc.name, "action": "would_cancel_delete"})
                    continue
                if doc.docstatus == 1:
                    doc.cancel()
                frappe.delete_doc("Sales Invoice", doc.name, ignore_permissions=True, force=True)
                summary["deleted"].append({"doctype": "Sales Invoice", "name": doc.name, "action": "cancel_delete"})
            except Exception as e:
                summary["errors"].append(f"SI {si['name']} → {e}")

        # 3) Hapus SO duplikat (kecuali yang di-keep)
        for so in sos:
            if so_keep and so["name"] == so_keep["name"]:
                continue
            try:
                doc = frappe.get_doc("Sales Order", so["name"])
                if int(dry_run):
                    summary["deleted"].append({"doctype": "Sales Order", "name": doc.name, "action": "would_cancel_delete"})
                    continue
                if doc.docstatus == 1:
                    doc.cancel()
                frappe.delete_doc("Sales Order", doc.name, ignore_permissions=True, force=True)
                summary["deleted"].append({"doctype": "Sales Order", "name": doc.name, "action": "cancel_delete"})
            except Exception as e:
                summary["errors"].append(f"SO {so['name']} → {e}")

        if not int(dry_run):
            frappe.db.commit()

        return summary

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Shopee Cleanup Duplicates")
        summary["errors"].append(str(e))
        return summary
    
@frappe.whitelist()
def backfill_po_no_from_custom_sn(limit: int = 1000) -> dict:
    """
    Set po_no = custom_shopee_order_sn untuk SO & SI yang po_no masih kosong.
    Biar ke depan dedup pakai po_no langsung.
    """
    changed = {"Sales Order": 0, "Sales Invoice": 0}
    for doctype in ("Sales Order", "Sales Invoice"):
        rows = frappe.get_all(
            doctype,
            filters={"po_no": ["in", [None, "" ]], "custom_shopee_order_sn": ["!=", ""]},
            fields=["name", "custom_shopee_order_sn"],
            limit=limit
        )
        for r in rows:
            try:
                frappe.db.set_value(doctype, r["name"], "po_no", r["custom_shopee_order_sn"])
                changed[doctype] += 1
            except Exception as e:
                frappe.log_error(f"Backfill po_no {doctype} {r['name']} fail: {e}", "Shopee Backfill")
    frappe.db.commit()
    return {"changed": changed}

@frappe.whitelist()
def cleanup_all_shopee_duplicates(dry_run: int = 1, prefer: str = "latest", limit_sn: int = 500) -> dict:
    """
    Scan semua order yang berpotensi duplikat pakai kunci gabungan:
      key = po_no if po_no else custom_shopee_order_sn
    Lalu panggil cleanup_shopee_duplicates(key) per item.

    - dry_run=1: simulasi (tidak mengubah data)
    - prefer: "latest" atau "earliest" (dokumen yang disisakan)
    - limit_sn: batasi jumlah order_sn yang dibersihkan dalam sekali jalan
    """
    from collections import defaultdict

    def _keyify(row):
        # jadikan 1 kunci yang konsisten
        po = (row.get("po_no") or "").strip()
        cs = (row.get("custom_shopee_order_sn") or "").strip()
        return po or cs  # pakai po_no kalau ada, kalau kosong pakai custom

    # Kumpulkan kandidat dari SO & SI — ambil yang punya minimal salah satu field terisi
    hits = defaultdict(lambda: {"SO": 0, "SI": 0})

    # Sales Order
    so_rows = frappe.get_all(
        "Sales Order",
        fields=["name", "po_no", "custom_shopee_order_sn"],
        filters=[["po_no", "!=", ""], ["custom_shopee_order_sn", "!=", ""], ["name", "is", "set"]],
        or_filters=[["po_no", "!=", ""], ["custom_shopee_order_sn", "!=", ""]],
        limit=10000,
    )
    for r in so_rows:
        key = _keyify(r)
        if key:
            hits[key]["SO"] += 1

    # Sales Invoice
    si_rows = frappe.get_all(
        "Sales Invoice",
        fields=["name", "po_no", "custom_shopee_order_sn"],
        filters=[["po_no", "!=", ""], ["custom_shopee_order_sn", "!=", ""], ["name", "is", "set"]],
        or_filters=[["po_no", "!=", ""], ["custom_shopee_order_sn", "!=", ""]],
        limit=10000,
    )
    for r in si_rows:
        key = _keyify(r)
        if key:
            hits[key]["SI"] += 1

    # Ambil hanya kunci yang duplikat (muncul >1 di salah satu doctype)
    targets = [k for k, c in hits.items() if c["SO"] > 1 or c["SI"] > 1]
    # Batasi sesuai limit
    targets = targets[: int(limit_sn)]

    results = []
    for key in targets:
        try:
            # cleanup_shopee_duplicates menerima 'order_sn' → kita kirim key gabungan ini.
            res = cleanup_shopee_duplicates(key, dry_run=dry_run, prefer=prefer)
            results.append(res)
        except Exception as e:
            results.append({"order_sn": key, "error": str(e)})

    return {
        "count": len(results),
        "dry_run": bool(int(dry_run)),
        "prefer": prefer,
        "results": results,
    }