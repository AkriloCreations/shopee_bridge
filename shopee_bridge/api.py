import time, hmac, hashlib, requests, frappe, re  # pyright: ignore[reportMissingImports]
from frappe.utils import get_url, nowdate, cint, add_days, now, convert_utc_to_user_timezone, format_datetime # pyright: ignore[reportMissingImports]
from datetime import datetime, timedelta, timezone
import json

def _settings():
    return frappe.get_single("Shopee Settings")

def _hum_epoch(ts: int | None):
    """Epoch detik -> string waktu sesuai timezone user, atau None."""
    if not ts:
        return None
    dt_utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    dt_user = convert_utc_to_user_timezone(dt_utc)
    return format_datetime(dt_user)

def _base():
    """Host Shopee sesuai Environment di Shopee Settings."""
    s = _settings()
    env = (getattr(s, "environment", None) or "Test").strip()
    if env == "Production":
        return "https://partner.shopeemobile.com"
    return "https://partner.test-stable.shopeemobile.com"

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

        return data
    except requests.exceptions.RequestException as e:
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
def refresh_if_needed():
    """Refresh token jika mau habis. Dipanggil scheduler."""
    s = _settings()
    if not s.refresh_token:
        return {"status": "no_refresh_token"}
        
    now = int(time.time())
    if s.token_expire_at and (int(s.token_expire_at) - now) > 300:
        return {"status": "token_still_valid"}
        
    res = _call("/api/v2/auth/access_token/get", str(s.partner_id).strip(), s.partner_key,
                s.shop_id, None, {"refresh_token": s.refresh_token, "shop_id": s.shop_id})
    
    if res.get("error"):
        frappe.log_error(f"Token refresh failed: {res.get('error')} - {res.get('message')}")
        return {"status": "error", "message": res.get("message")}
        
    resp = res.get("response") or {}
    if resp.get("access_token"):
        s.access_token = resp["access_token"]
        s.refresh_token = resp.get("refresh_token", s.refresh_token)
        s.token_expire_at = now + int(resp.get("expire_in") or 0)
        s.save(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "refreshed"}
    
    return {"status": "no_new_token"}

def _default_item_group() -> str:
    # Ambil dari Stock Settings kalau ada; kalau tidak, pakai Shopee Products (dibuat otomatis oleh _get_item_group)
    return (
        frappe.db.get_single_value("Stock Settings", "default_item_group")
        or _get_item_group()  # akan membuat "Shopee Products" jika belum ada
        or "All Item Groups"
    )

@frappe.whitelist()
def sync_recent_orders(hours: int = 24):
    """Enhanced version with better error handling and retry logic (multi-status safe)."""
    s = _settings()
    if not s.access_token:
        frappe.throw("Access token required. Please authenticate with Shopee first.")

    # Refresh token bila perlu
    refresh_result = refresh_if_needed()
    frappe.logger().info(f"Token refresh status: {refresh_result.get('status')}")

    now = int(time.time())
    last = int(s.last_success_update_time or 0)
    overlap = int(getattr(s, "overlap_seconds", 600) or 600)
    time_from = (now - hours * 3600) if last == 0 else max(0, last - overlap)
    time_to = now

    page_size = 50
    highest = last
    processed_count = 0
    error_count = 0
    retry_attempts = 0
    max_retries = 3

    # Shopee: order_status harus 1 nilai saja per request
    statuses = ["READY_TO_SHIP", "PROCESSED", "COMPLETED"]
    seen = set()  # dedupe order_sn antar status

    frappe.logger().info(f"Starting order sync: from {time_from} to {time_to}")

    try:
        for st in statuses:
            offset = 0
            while True:
                try:
                    # small backoff
                    if processed_count > 0 or retry_attempts > 0:
                        time.sleep(0.5 + retry_attempts * 0.5)

                    ol = _call(
                        "/api/v2/order/get_order_list",
                        str(s.partner_id).strip(), s.partner_key,
                        s.shop_id, s.access_token,
                        {
                            "time_range_field": "update_time",
                            "time_from": time_from,
                            "time_to": time_to,
                            "page_size": page_size,
                            "order_status": st,   # << kirim satu status
                            "offset": offset
                        }
                    )

                    if ol.get("error"):
                        msg_lc = (ol.get("message") or "").lower()

                        # token expired → coba refresh
                        if ("access token expired" in msg_lc or "invalid access token" in msg_lc) and retry_attempts < max_retries:
                            frappe.logger().info(f"Token issue, refreshing (attempt {retry_attempts+1})")
                            r = refresh_if_needed()
                            if r.get("status") == "refreshed":
                                retry_attempts += 1
                                continue

                        # rate limit → tunggu lalu retry
                        if ("rate limit" in msg_lc or "too many requests" in msg_lc) and retry_attempts < max_retries:
                            wait_time = 5 + retry_attempts * 2
                            frappe.logger().info(f"Rate limited, waiting {wait_time}s…")
                            time.sleep(wait_time)
                            retry_attempts += 1
                            continue

                        error_full = f"Failed to get orders: {ol.get('error')} - {ol.get('message')}"
                        frappe.log_error(error_full, "Shopee API Error")
                        error_count += 1
                        if error_count > 3:
                            frappe.throw(error_full)
                        break  # keluar dari while current status

                    resp = ol.get("response") or {}
                    orders = resp.get("order_list", []) or []

                    if not orders:
                        frappe.logger().info(f"No orders for status {st}, moving on")
                        break

                    batch_processed = 0
                    for o in orders:
                        try:
                            order_sn = o.get("order_sn")
                            if not order_sn:
                                continue
                            if order_sn in seen:
                                continue
                            seen.add(order_sn)

                            # skip jika sudah pernah dibuat
                            if not frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}):
                                _process_order(order_sn)
                                batch_processed += 1

                            ut = int(o.get("update_time") or 0)
                            if ut > highest:
                                highest = ut
                        except Exception as order_error:
                            error_count += 1
                            frappe.log_error(f"Failed to process order {o.get('order_sn')}: {str(order_error)}",
                                             "Shopee Order Processing")
                            continue

                    processed_count += batch_processed
                    frappe.logger().info(f"[{st}] processed {batch_processed} this page, total {processed_count}")

                    if not resp.get("has_next_page"):
                        break
                    offset = resp.get("next_offset", offset + page_size)
                    retry_attempts = 0  # reset retry kalau sukses halaman ini

                except Exception as e:
                    error_count += 1
                    frappe.log_error(f"Error processing batch [{st}]: {str(e)}", "Shopee Sync Error")
                    if error_count > 5:
                        frappe.logger().error("Too many errors, stopping sync")
                        break
                    continue

        # update last success time hanya bila meningkat
        if highest > (s.last_success_update_time or 0):
            s.last_success_update_time = highest
            s.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.logger().info(f"Updated last success time to: {highest}")

        result = {
            "from": time_from,
            "to": time_to,
            "max_update_time": highest,
            "processed_orders": processed_count,
            "errors": error_count,
            "success": error_count < processed_count or processed_count == 0
        }
        frappe.logger().info(f"Sync completed: {result}")
        return result

    except Exception as e:
        frappe.log_error(f"Sync failed: {str(e)}", "Shopee Sync Critical Error")
        raise

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

def _ensure_item_exists(sku: str, sh_it: dict, default_rate: float) -> str:
    """
    Pastikan item ada.
    - Item Code = SKU (bukan Shopee item_id)
    - Simpan mapping Shopee di custom fields:
      custom_model_sku, custom_shopee_item_id, custom_shopee_model_id
    - Jika sudah ada item (by SKU atau by custom fields), pakai itu.
    """
    code = (sku or "").strip()
    if not code:
        # fallback minimal
        code = f"SHP-{sh_it.get('item_id')}-{sh_it.get('model_id', '0')}"

    # 1) Cek by Item Code (SKU)
    if frappe.db.exists("Item", code):
        return code

    # 2) Cek by custom fields (kalau sebelumnya pernah buat dengan code lain)
    filt = {
        "custom_shopee_item_id": str(sh_it.get("item_id") or ""),
        "custom_shopee_model_id": str(sh_it.get("model_id") or "0"),
    }
    if filt["custom_shopee_item_id"]:
        existing = frappe.db.get_value("Item", filt, "name")
        if existing:
            return existing

    # 3) Buat baru dengan Item Code = SKU
    item = frappe.new_doc("Item")
    item.item_code = code[:140]
    item.item_name = (sh_it.get("item_name") or sh_it.get("model_name") or code)[:140]
    item.item_group = _default_item_group()
    item.is_stock_item = 1

    # optional harga awal
    try:
        item.standard_rate = float(default_rate or 0)
    except Exception:
        pass

    # simpan identitas Shopee
    item.custom_model_sku = (sh_it.get("model_sku") or sh_it.get("item_sku") or "")[:140]
    item.custom_shopee_item_id = str(sh_it.get("item_id") or "")
    item.custom_shopee_model_id = str(sh_it.get("model_id") or "0")

    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name

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
        item.stock_uom = "Nos"
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
            item.stock_uom = "Nos"
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
    item.stock_uom = "Nos"
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
    t = (title or "Shopee")[:140]
    m = (message or "")[:4000]
    frappe.log_error(m, t)

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

def _safe_int(v, d=0):
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

def _safe_flt(v, d=0.0):
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

def _ts_to_date(ts):
    if not ts:
        return None
    return frappe.utils.formatdate(frappe.utils.convert_utc_to_user_datetime(ts).date())

def _compose_customer_name(od):
    uname = (od.get("buyer_username") or "").strip()
    if uname:
        return f"SHP-{uname}"[:140]
    uid = str(od.get("buyer_user_id") or "")[-4:] or "0000"
    return f"SHP-buyer-{uid}"

def _process_order_to_so(order_sn: str):
    """Buat SO pakai field standar saja."""
    s = _settings()

    # Cegah duplikat via po_no (Customer's Purchase Order)
    if frappe.db.exists("Sales Order", {"po_no": order_sn}) or \
       frappe.db.exists("Sales Invoice", {"po_no": order_sn}):
        frappe.logger().info(f"[SO] {order_sn} already processed, skipping")
        return

    try:
        try:
            refresh_if_needed()
        except Exception:
            pass

        # Ambil detail order
        det = _call(
            "/api/v2/order/get_order_detail",
            str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
            {"order_sn_list": order_sn, "response_optional_fields":"item_list,recipient_address,buyer_info"}
        )
        if det.get("error"):
            _short_log(f"Failed to get order detail for {order_sn}: {det.get('message')}", "Shopee Phase2")
            return

        orders = (det.get("response") or {}).get("order_list", []) or []
        if not orders:
            _short_log(f"No order data found for {order_sn}", "Shopee Phase2")
            return
        od = orders[0]

        # Tanggal
        create_ts  = _safe_int(od.get("create_time"))
        pay_ts     = _safe_int(od.get("pay_time"))
        ship_by_ts = _safe_int(od.get("ship_by_date"))
        if not ship_by_ts:
            dts = _safe_int(od.get("days_to_ship"))
            if dts and create_ts:
                ship_by_ts = create_ts + dts * 86400

        transaction_date = _ts_to_date(create_ts) or nowdate()
        delivery_date    = _ts_to_date(ship_by_ts) or transaction_date
        customer_po_date = _ts_to_date(pay_ts) or None  # opsional

        # Customer/Address/Contact pakai fungsi yang sudah ada
        customer = _create_or_get_customer(od) or _compose_customer_name(od)
        company  = frappe.db.get_single_value("Global Defaults", "default_company")

        # Build SO
        so = frappe.new_doc("Sales Order")
        so.company = company
        so.customer = customer
        so.transaction_date = transaction_date
        so.delivery_date = delivery_date
        so.po_no = order_sn  # simpan Order SN di field standar
        if hasattr(so, "customer_purchase_order_date") and customer_po_date:
            so.customer_purchase_order_date = customer_po_date

        # Remarks hanya sebagai catatan (tidak wajib)
        so.remarks = f"Shopee order {order_sn}. create={transaction_date}, ship_by={delivery_date}"

        # Items
        for it in (od.get("item_list") or []):
            sku = (it.get("model_sku") or "").strip() or \
                  (it.get("item_sku") or "").strip() or \
                  f"SHP-{it.get('item_id')}-{it.get('model_id','0')}"
            qty = _safe_int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
            rate = _safe_flt(
                it.get("model_discounted_price")
                or it.get("order_price")
                or it.get("item_price")
                or it.get("model_original_price")
                or 0
            )
            if rate > 1_000_000:
                rate /= 100000

            item_code = _ensure_item_exists(sku, it, rate)

            row = so.append("items", {})
            row.item_code = item_code
            row.item_name = (it.get("item_name") or it.get("model_name") or item_code)[:140]
            row.qty = qty
            row.rate = rate
            row.delivery_date = delivery_date  # tenggat kirim

        _insert_submit_with_retry(so, max_tries=3)
        frappe.logger().info(f"[SO] Created {so.name} for {order_sn}")

        # Payment Entry NANTI ambil escrow saat bikin PE (tanpa menyimpan di SO).
        return {"ok": True, "sales_order": so.name}

    except Exception as e:
        _short_log(f"Failed to create SO for {order_sn}: {e}", "Shopee Phase2")
        raise

LOCK_ERRORS = ("Lock wait timeout exceeded", "deadlock found")

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

def _process_order_to_si(order_sn: str):
    """Jalur lama: langsung buat Sales Invoice. Ada fallback jika stok kurang."""
    s = _settings()

    # Skip jika sudah pernah dibuat
    if frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}):
        frappe.logger().info(f"[SI] Order {order_sn} already processed, skipping")
        return

    try:
        # --- Order detail (minta item_list eksplisit kalau perlu) ---
        det = _call(
            "/api/v2/order/get_order_detail",
            str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
            {"order_sn_list": order_sn, "response_optional_fields": "item_list,recipient_address,payment_info,buyer_info"}
        )
        if det.get("error"):
            _short_log(f"Failed to get order detail for {order_sn}: {det.get('message')}", "Shopee SI Flow")
            return

        orders = (det.get("response") or {}).get("order_list", []) or []
        if not orders:
            _short_log(f"No order data found for {order_sn}", "Shopee SI Flow")
            return
        od = orders[0]

        # --- Escrow (opsional) ---
        esc = _call("/api/v2/payment/get_escrow_detail",
                    str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                    {"order_sn": order_sn})
        if esc.get("error"):
            _short_log(f"Escrow fail {order_sn}: {esc.get('message')}", "Shopee SI Flow")
            esc = {"response": {}}
        esc = esc.get("response", {}) or {}

        # --- Customer ---
        customer = _create_or_get_customer(od)

        # --- Sales Invoice header ---
        si = frappe.new_doc("Sales Invoice")
        si.customer = customer
        si.posting_date = nowdate()
        si.set_posting_time = 1
        si.update_stock = 1  # default: gerakkan stok; akan fallback ke 0 jika error stok
        si.currency = "IDR"
        si.custom_shopee_order_sn = order_sn
        si.remarks = f"Shopee order SN {order_sn}"

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            si.company = company

        # --- Items ---
        items = od.get("item_list") or od.get("items") or []
        if not items:
            _short_log(f"No items from Shopee for {order_sn}", "Shopee SI Flow")
            return

        default_wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")

        for it in items:
            sku = (it.get("model_sku") or "").strip() or \
                  (it.get("item_sku") or "").strip() or \
                  f"SHP-{it.get('item_id')}-{it.get('model_id', '0')}"
            if not sku:
                sku = f"SHP-UNKNOWN-{order_sn}-{it.get('item_id', 'NOITEM')}"

            qty = int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
            rate = float(it.get("model_original_price") or it.get("model_discounted_price")
                         or it.get("order_price") or it.get("item_price") or 0)
            if rate > 1_000_000:   # micro-unit guard
                rate = rate / 100000

            item_code = _ensure_item_exists(sku, it, rate)

            row = si.append("items", {})
            row.item_code = item_code
            row.qty = qty
            row.rate = rate
            row.amount = qty * rate
            if default_wh:
                row.warehouse = default_wh

        if not si.items:
            _short_log(f"No valid items for {order_sn}", "Shopee SI Flow")
            return

        # --- Insert + submit dengan fallback stok kurang ---
        si.insert(ignore_permissions=True)
        try:
            si.submit()
        except Exception as e:
            msg = str(e)
            if "needed in Warehouse" in msg or "negative stock" in msg.lower():
                si.reload()
                si.update_stock = 0
                si.save()
                si.submit()
                si.add_comment("Comment",
                    text=f"Auto-submitted with update_stock=0 (stock shortage). Original: {msg}")
                frappe.logger().warning(f"Submitted SI {si.name} without stock movement (order {order_sn}).")
            else:
                raise

        frappe.logger().info(f"Created Sales Invoice {si.name} for order {order_sn}")

        # --- Payment Entry (opsional) ---
        net = float(esc.get("escrow_amount") or esc.get("net_amount") or esc.get("payout_amount") or 0)
        if net > 0:
            _create_payment_entry(si, esc, net, order_sn)

        return {"ok": True, "sales_invoice": si.name}

    except Exception as e:
        _short_log(f"Failed to process order {order_sn}: {e}", "Shopee SI Flow")
        raise


def _process_order(order_sn: str):
    """
    Router:
    - Jika Shopee Settings.use_sales_order_flow = 1  -> buat Sales Order (Phase-2)
    - Jika 0 -> jalur lama (langsung Sales Invoice)
    """
    s = _settings()
    try:
        if cint(getattr(s, "use_sales_order_flow", 0)):
            return _process_order_to_so(order_sn)
        else:
            return _process_order_to_si(order_sn)
    except Exception as e:
        _short_log(f"Failed to process order {order_sn}: {e}", "Shopee Order Processing")
        raise

def _create_or_get_customer(order_detail):
    """Customer dibentuk dari nama penerima + 4 digit hp (fallback buyer info)."""
    addr = order_detail.get("recipient_address") or {}
    buyer_username = (order_detail.get("buyer_username") or "").strip()
    buyer_user_id  = str(order_detail.get("buyer_user_id") or "").strip()

    base_raw = (addr.get("name") or buyer_username or (f"buyer_{buyer_user_id}" if buyer_user_id else "buyer"))
    # buang karakter aneh/emoji
    safe_base = re.sub(r"[^A-Za-z0-9\- ]+", "", (base_raw or "")).strip() or "buyer"
    phone     = (addr.get("phone") or addr.get("tel") or "").strip()
    digits    = re.sub(r"\D", "", phone)
    tail4     = (digits[-4:] if digits else "0000")

    customer_name = f"SHP-{safe_base[:20]}-{tail4}"

    if frappe.db.exists("Customer", {"customer_name": customer_name}):
        address_name = frappe.db.get_value("Address", {"address_title": customer_name}, "name")
        contact_name = frappe.db.get_value("Contact", {"first_name": safe_base[:140]}, "name")
        return customer_name, address_name, contact_name

    # Customer
    c = frappe.new_doc("Customer")
    c.customer_name  = customer_name
    c.customer_group = _get_customer_group()
    c.customer_type  = "Individual"
    c.territory      = "All Territories"
    c.insert(ignore_permissions=True)

    # Address
    address_name = None
    if addr:
        a = frappe.new_doc("Address")
        a.address_title = customer_name
        a.address_type  = "Shipping"
        full_line = (addr.get("full_address") or "").strip()
        city      = (addr.get("city") or addr.get("state") or "").strip()
        a.address_line1 = (full_line or city or customer_name)[:140]
        a.city      = city[:140]
        a.country   = addr.get("country") or "Indonesia"
        a.phone     = phone
        a.append("links", {"link_doctype": "Customer", "link_name": customer_name})
        a.insert(ignore_permissions=True)
        address_name = a.name

    # Contact
    contact_name = None
    try:
        ct = frappe.new_doc("Contact")
        ct.first_name = safe_base[:140] or customer_name
        if phone:
            ct.append("phone_nos", {"phone": phone, "is_primary_phone": 1})
        ct.append("links", {"link_doctype": "Customer", "link_name": customer_name})
        ct.insert(ignore_permissions=True)
        contact_name = ct.name
    except Exception:
        pass

    return customer_name, address_name, contact_name

def _create_payment_entry(si, esc, net, order_sn):
    """Create payment entry with Shopee fees."""
    try:
        # Account mappings for Shopee fees
        ACC = {
            "commission": _get_or_create_account("Komisi Shopee", "Expense Account"),
            "service": _get_or_create_account("Biaya Layanan Shopee", "Expense Account"),
            "protection": _get_or_create_account("Proteksi Pengiriman Shopee", "Expense Account"),
            "shipdiff": _get_or_create_account("Selisih Ongkir Shopee", "Expense Account"),
            "voucher": _get_or_create_account("Voucher Shopee", "Expense Account")
        }

        fees = {
            "commission": float(esc.get("commission_fee") or esc.get("seller_commission_fee") or 0),
            "service": float(esc.get("service_fee") or esc.get("seller_service_fee") or 0),
            "protection": float(esc.get("shipping_seller_protection_fee_amount") or 0),
            "shipdiff": float(esc.get("shipping_fee_difference") or 0),
            "voucher": float(esc.get("voucher_seller") or 0)
                          + float(esc.get("coin_cash_back") or 0)
                          + float(esc.get("voucher_code_seller") or 0),
        }

        # Get default accounts
        paid_from = frappe.db.get_single_value("Accounts Settings", "default_receivable_account")
        if not paid_from:
            company = frappe.db.get_single_value("Global Defaults", "default_company")
            paid_from = frappe.db.get_value("Company", company, "default_receivable_account")
        
        paid_to = _get_or_create_account("Bank - Shopee (Escrow)", "Bank")

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.posting_date = nowdate()
        pe.mode_of_payment = _get_or_create_mode_of_payment("Shopee")
        pe.paid_from = paid_from
        pe.paid_to = paid_to
        pe.paid_amount = net
        pe.received_amount = net
        pe.reference_no = order_sn

        # Set company
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            pe.company = company

        # Link to Sales Invoice
        r = pe.append("references", {})
        r.reference_doctype = "Sales Invoice"
        r.reference_name = si.name
        r.allocated_amount = net + sum(fees.values())

        # Add fee deductions
        for k, v in fees.items():
            if v > 0:
                d = pe.append("deductions", {})
                d.account = ACC[k]
                d.amount = v

        pe.insert(ignore_permissions=True)
        pe.submit()
        
        frappe.logger().info(f"Created Payment Entry {pe.name} for order {order_sn}")

    except Exception as e:
        frappe.log_error(f"Failed to create payment entry for {order_sn}: {str(e)}", "Shopee Payment Entry Creation")

def _get_or_create_account(account_name, account_type):
    """Get or create account."""
    if frappe.db.exists("Account", {"account_name": account_name}):
        return account_name
    
    try:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        
        account = frappe.new_doc("Account")
        account.account_name = account_name
        account.account_type = account_type
        account.company = company
        
        # Set parent account based on type
        if account_type == "Bank":
            account.parent_account = "Bank Accounts - " + frappe.db.get_value("Company", company, "abbr")
        elif account_type == "Expense Account":
            account.parent_account = "Expenses - " + frappe.db.get_value("Company", company, "abbr")
        
        account.insert(ignore_permissions=True)
        return account_name
    except Exception as e:
        frappe.log_error(f"Failed to create account {account_name}: {str(e)}", "Shopee Account Creation")
        # Return a default account
        return frappe.db.get_single_value("Accounts Settings", "default_cash_account")

def _get_or_create_mode_of_payment(mode_name):
    """Get or create mode of payment."""
    if frappe.db.exists("Mode of Payment", mode_name):
        return mode_name
    
    try:
        mode = frappe.new_doc("Mode of Payment")
        mode.mode_of_payment = mode_name
        mode.type = "Electronic"
        mode.insert(ignore_permissions=True)
        return mode_name
    except Exception as e:
        frappe.log_error(f"Failed to create mode of payment {mode_name}: {str(e)}", "Shopee Mode of Payment Creation")
        return "Cash"  # Fallback to default

@frappe.whitelist()
def debug_sign():
    """Debug signature generation"""
    s = frappe.get_single("Shopee Settings")
    path = "/api/v2/shop/auth_partner"  # Seller/Shop API
    ts = int(time.time())

    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    base = f"{partner_id}{path}{ts}"
    sign = _sign(partner_key, base)

    return {
        "partner_id": partner_id,
        "partner_key_length": len(partner_key),
        "partner_key_first_10": partner_key[:10] + "..." if len(partner_key) > 10 else partner_key,
        "path": path,
        "timestamp": ts,
        "base_string": base,
        "signature": sign,
        "url": f"{_base()}{path}?partner_id={partner_id}&timestamp={ts}&sign={sign}",
        "environment": s.environment
    }

@frappe.whitelist()
def exchange_code(code: str, shop_id: str | None = None):
    """
    Manual: tukar code -> access_token & refresh_token, simpan di Shopee Settings.
    Panggil dari Client Script.
    """
    if not code or not code.strip():
        frappe.throw("Authorization code is required")
        
    s = _settings()
    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    
    if not partner_id or not partner_key:
        frappe.throw("Partner ID and Partner Key must be configured in Shopee Settings")

    ts = int(time.time())
    path = "/api/v2/auth/token/get"
    
    # For token exchange, signature is: partner_id + path + timestamp (NO access_token/shop_id)
    base_string = f"{partner_id}{path}{ts}"
    sign = _sign(partner_key, base_string)

    url = f"{_base()}{path}?partner_id={partner_id}&timestamp={ts}&sign={sign}"
    body = {"code": code, "partner_id": int(partner_id)}
    
    if shop_id:
        body["shop_id"] = int(shop_id)

    try:
        r = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=30)
        
        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
        else:
            frappe.throw(f"Invalid response from Shopee: {r.text}")
            
    except requests.exceptions.RequestException as e:
        frappe.throw(f"Request to Shopee failed: {str(e)}")

    # Check for API errors
    if data.get("error"):
        error_msg = data.get("message", "Unknown error")
        frappe.throw(f"Shopee API error: {data.get('error')} - {error_msg}")

    # Extract response data
    response_data = data.get("response", data)  # Some responses have nested "response"
    
    if not response_data.get("access_token"):
        frappe.throw("No access token received from Shopee")

    # Save tokens to settings
    s.access_token = response_data.get("access_token")
    s.refresh_token = response_data.get("refresh_token")
    s.token_expire_at = int(time.time()) + int(response_data.get("expire_in", 0))
    
    if shop_id:
        s.shop_id = shop_id
        
    s.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "ok": True,
        "shop_id": s.shop_id,
        "expire_at": s.token_expire_at,
        "access_token_preview": s.access_token[:10] + "..." if s.access_token else None
    }

def _cfg_defaults():
    """Get configuration defaults from settings."""
    s = _settings()
    return {
        "price_list": getattr(s, "price_list", None) or "Standard Selling",
        "item_group": getattr(s, "item_group", None) or "Products",
        "stock_uom": getattr(s, "stock_uom", None) or "Nos",
    }

def _get_or_create_price_list(pl_name: str):
    """Get or create price list."""
    if not frappe.db.exists("Price List", {"price_list_name": pl_name}):
        try:
            pl = frappe.new_doc("Price List")
            pl.price_list_name = pl_name
            pl.selling = 1
            pl.currency = "IDR"
            pl.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to create price list {pl_name}: {str(e)}", "Shopee Price List Creation")

def _upsert_price(item_code: str, price_list: str, currency: str, rate: float):
    """Update or insert item price."""
    try:
        _get_or_create_price_list(price_list)
        cond = {"item_code": item_code, "price_list": price_list, "currency": currency}
        name = frappe.db.get_value("Item Price", cond, "name")
        if name:
            ip = frappe.get_doc("Item Price", name)
            ip.price_list_rate = rate
            ip.save(ignore_permissions=True)
        else:
            ip = frappe.new_doc("Item Price")
            ip.update({
                "item_code": item_code,
                "price_list": price_list,
                "currency": currency,
                "price_list_rate": rate,
            })
            ip.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"Failed to upsert price for {item_code}: {str(e)}", "Shopee Price Update")

def _upsert_item(item_code: str, item_name: str, item_group: str, stock_uom: str, rate: float) -> str:
    """Buat/Update Item. Item Group diset di Item, bukan di Item Default."""
    item_code = (item_code or "").strip()
    item_name = (item_name or "").strip()[:140] or item_code

    if not item_code:
        raise Exception("Empty item_code in _upsert_item")

    # Pastikan Item Group ada
    if not frappe.db.exists("Item Group", item_group):
        ig = frappe.new_doc("Item Group")
        ig.item_group_name = item_group
        ig.parent_item_group = "All Item Groups"
        ig.is_group = 0
        ig.insert(ignore_permissions=True)

    # Buat / update Item
    if frappe.db.exists("Item", item_code):
        it = frappe.get_doc("Item", item_code)
        changed = False
        if it.item_name != item_name:
            it.item_name = item_name
            changed = True
        if it.item_group != item_group:
            it.item_group = item_group
            changed = True
        if it.stock_uom != stock_uom:
            it.stock_uom = stock_uom
            changed = True
        # simpan kalau ada perubahan
        if changed:
            it.save(ignore_permissions=True)
    else:
        it = frappe.new_doc("Item")
        it.item_code  = item_code
        it.item_name  = item_name
        it.description = item_name
        it.item_group = item_group
        it.stock_uom  = stock_uom
        it.is_stock_item = 1
        it.has_variants  = 0
        it.insert(ignore_permissions=True)

    # Tambahkan Item Default minimal (company/warehouse) bila perlu
    try:
        company   = frappe.db.get_single_value("Global Defaults", "default_company")
        warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        if company and warehouse:
            has_row = any([d.company == company for d in (it.item_defaults or [])])
            if not has_row:
                d = it.append("item_defaults", {})
                d.company = company
                d.default_warehouse = warehouse
                it.save(ignore_permissions=True)
    except Exception:
        pass

    return it.item_code


def _get_models_for_item(item_id: int):
    """Get models/variations for a Shopee item."""
    s = _settings()
    res = _call(
        "/api/v2/product/get_model_list",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {"item_id": int(item_id)},
    )
    resp = res.get("response") or {}
    models = resp.get("model") or resp.get("models") or []
    return models if isinstance(models, list) else []

def _get_item_base_info(item_id: int) -> dict:
    """
    Ambil judul produk (item_name) + SKU dasarnya dari Shopee.
    Sumber utama: /api/v2/product/get_item_base_info
    Return selalu dict dengan minimal key: item_name, item_sku.
    """
    s = _settings()
    res = _call(
        "/api/v2/product/get_item_base_info",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {"item_id_list": str(item_id)}
    )

    base = {}
    if isinstance(res, dict) and not res.get("error"):
        lst = (res.get("response") or {}).get("item_list") or []
        if lst:
            base = lst[0] or {}

    # Normalisasi minimal field
    item_name = (base.get("item_name") or "").strip()
    item_sku  = (base.get("item_sku")  or "").strip()

    # Fallback terakhir: kalau masih kosong banget, coba /get_model_list dulu untuk ambil item_sku
    if not item_sku:
        try:
            ml = _call(
                "/api/v2/product/get_model_list",
                str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
                {"item_id": int(item_id)}
            )
            if isinstance(ml, dict) and not ml.get("error"):
                resp = (ml.get("response") or {})
                item = (resp.get("item") or {})
                item_sku = (item.get("item_sku") or "").strip()
        except Exception:
            pass

    return {"item_name": item_name, "item_sku": item_sku}


# ===== Helpers (ADD jika belum ada) =========================================
from typing import Optional
import time
import frappe  # pyright: ignore[reportMissingImports]

def _fit140(s: str) -> str:
    return ((s or "").strip())[:140]

def _compose_item_name(base_name: str, model_name: Optional[str]) -> str:
    base = (base_name or "").strip()
    mdl  = (model_name or "").strip()
    if base and mdl:
        return f"{base} - {mdl}"
    if base:
        return base
    if mdl:
        return mdl
    return ""

def _normalize_rate(x) -> float:
    try:
        v = float(x or 0)
        # Shopee kadang kirim micro units untuk sebagian region
        if v > 1_000_000:
            v = v / 100000
        return v
    except Exception:
        return 0.0

def _upsert_item(item_code: str,
                 item_name: str,
                 item_group: str,
                 stock_uom: str,
                 standard_rate: float = 0.0,
                 meta: Optional[dict] = None) -> str:
    """
    Buat/update Item master:
      - item_code & item_name dipotong 140
      - full name taruh di description (jika ada)
      - mapping shopee ke custom fields:
        custom_model_sku, custom_shopee_item_id, custom_shopee_model_id
    Return: item.name yang dipakai
    """
    meta = meta or {}
    code140 = _fit140(item_code)
    name140 = _fit140(item_name)

    if frappe.db.exists("Item", code140):
        item = frappe.get_doc("Item", code140)
        if name140 and item.item_name != name140:
            item.item_name = name140
        if meta.get("description"):
            item.description = meta["description"]
        if "custom_model_sku" in meta:
            item.custom_model_sku = _fit140(meta.get("custom_model_sku") or "")
        if "custom_shopee_item_id" in meta:
            item.custom_shopee_item_id = str(meta.get("custom_shopee_item_id") or "")
        if "custom_shopee_model_id" in meta:
            item.custom_shopee_model_id = str(meta.get("custom_shopee_model_id") or "")
        try:
            if standard_rate and float(standard_rate) > 0:
                item.standard_rate = float(standard_rate)
        except Exception:
            pass
        item.save(ignore_permissions=True)
        frappe.db.commit()
        return item.name

    item = frappe.new_doc("Item")
    item.item_code = code140
    item.item_name = name140
    item.item_group = item_group or "Products"
    item.stock_uom = stock_uom or "Nos"
    item.is_stock_item = 1
    if meta.get("description"):
        item.description = meta["description"]
    item.custom_model_sku = _fit140(meta.get("custom_model_sku", "")) if meta else ""
    item.custom_shopee_item_id = str(meta.get("custom_shopee_item_id", "")) if meta else ""
    item.custom_shopee_model_id = str(meta.get("custom_shopee_model_id", "")) if meta else ""
    try:
        if standard_rate and float(standard_rate) > 0:
            item.standard_rate = float(standard_rate)
    except Exception:
        pass
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name

def _upsert_price(item_code: str, price_list: str, currency: str, rate: float):
    """Buat/update Item Price pada price_list tertentu."""
    if not price_list:
        return
    rows = frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "price_list": price_list, "currency": currency},
        fields=["name"], limit=1,
    )
    if rows:
        ip = frappe.get_doc("Item Price", rows[0].name)
        ip.price_list_rate = float(rate or 0)
        ip.save(ignore_permissions=True)
        frappe.db.commit()
        return
    ip = frappe.new_doc("Item Price")
    ip.item_code = item_code
    ip.price_list = price_list
    ip.currency = currency
    ip.price_list_rate = float(rate or 0)
    ip.selling = 1
    ip.insert(ignore_permissions=True)
    frappe.db.commit()

# ====== SYNC ITEMS (PASTE/REPLACE FUNGSI LAMA) ==============================

@frappe.whitelist()
def sync_items(hours: int = 720, status: str = "NORMAL"):
    """
    Sinkron Item Shopee -> ERPNext.
    - Code: model_sku (jika ada) else SHP-<item_id> / SHP-<item_id>-<model_id>
    - Name: "Judul Produk - Nama Varian" (max 140)
    - Mapping custom fields: custom_model_sku, custom_shopee_item_id, custom_shopee_model_id
    """
    import time

    # ---- util lokal (hindari dependensi) ----
    def _fit140_local(s: str) -> str:
        return ((s or "").strip())[:140]

    def _compose_local(base_name: str, model_name: str) -> str:
        b = (base_name or "").strip()
        m = (model_name or "").strip()
        if b and m: return f"{b} - {m}"
        return b or m or ""

    def _norm_rate(x) -> float:
        try:
            v = float(x or 0)
            if v > 1_000_000:  # sebagian region pakai micro units
                v = v / 100000
            return v
        except Exception:
            return 0.0

    s = _settings()
    defaults = _cfg_defaults()
    currency = "IDR"

    now = int(time.time())
    time_from = now - hours * 3600
    time_to = now

    page_size, offset = 100, 0
    created, updated = 0, 0
    processed_items = 0
    error_count = 0

    # segarkan token kalau perlu (abaikan error)
    try:
        refresh_if_needed()
    except Exception:
        pass

    frappe.logger().info(f"[sync_items] from={time_from} to={time_to} status={status}")

    try:
        while True:
            # throttle ringan
            if processed_items > 0:
                time.sleep(0.2)

            gl = _call(
                "/api/v2/product/get_item_list",
                str(s.partner_id).strip(),
                s.partner_key,
                s.shop_id,
                s.access_token,
                {
                    "offset": offset,
                    "page_size": page_size,
                    "update_time_from": time_from,
                    "update_time_to": time_to,
                    "item_status": status,
                },
            )

            if not isinstance(gl, dict):
                frappe.log_error(f"Unexpected payload type: {type(gl).__name__}", "Shopee sync_items")
                return {"ok": False, "error": "bad_payload_type"}

            if gl.get("error"):
                msg = f"get_item_list error: {gl.get('error')} - {gl.get('message')}"
                frappe.log_error(msg, "Shopee sync_items")
                # refresh token lalu coba ulang 1x
                if "access token" in str(gl.get("message", "")).lower():
                    ri = refresh_if_needed()
                    if ri.get("status") == "refreshed":
                        continue
                return {"ok": False, "error": gl.get("error"), "message": gl.get("message")}

            resp = gl.get("response") or {}
            item_list = resp.get("item") or resp.get("items") or []
            if not isinstance(item_list, list):
                item_list = []

            has_next = bool(resp.get("has_next_page") or resp.get("has_next") or resp.get("more"))

            for it in item_list:
                try:
                    processed_items += 1
                    item_id = int(it.get("item_id"))

                    # ---- ambil judul produk (base) ----
                    base_info = _call(
                        "/api/v2/product/get_item_base_info",
                        str(s.partner_id).strip(),
                        s.partner_key,
                        s.shop_id,
                        s.access_token,
                        {"item_id_list": str(item_id)},
                    )
                    base_name = ""
                    base_sku = ""
                    if isinstance(base_info, dict) and not base_info.get("error"):
                        lst = (base_info.get("response") or {}).get("item_list") or []
                        if lst:
                            base_name = (lst[0].get("item_name") or "").strip()
                            base_sku = (lst[0].get("item_sku") or "").strip()

                    # ---- ambil model list (varian) ----
                    ml = _call(
                        "/api/v2/product/get_model_list",
                        str(s.partner_id).strip(),
                        s.partner_key,
                        s.shop_id,
                        s.access_token,
                        {"item_id": item_id},
                    )
                    models = []
                    if isinstance(ml, dict) and not ml.get("error"):
                        models = (ml.get("response") or {}).get("model") or []
                        if not isinstance(models, list):
                            models = []

                    # ===== tanpa model: 1 item =====
                    if not models:
                        sku = base_sku if base_sku else f"SHP-{item_id}"
                        full_name = base_name or ""
                        name_140 = _fit140_local(full_name or base_name or "")
                        if not name_140:
                            name_140 = _fit140_local(sku)

                        rate = _norm_rate(
                            (lst[0].get("normal_price") if (isinstance(base_info, dict) and not base_info.get("error") and lst) else None)
                        )

                        existed = bool(frappe.db.exists("Item", {"item_code": sku}))
                        used_code = _upsert_item(
                            sku, name_140,
                            defaults.get("item_group"), defaults.get("stock_uom"), rate,
                            meta={
                                "description": (full_name or base_name or sku),
                                "custom_model_sku": base_sku,
                                "custom_shopee_item_id": str(item_id),
                                "custom_shopee_model_id": "0",
                            },
                        )
                        _upsert_price(used_code, defaults.get("price_list"), currency, rate)
                        if existed: updated += 1
                        else:       created += 1
                        continue

                    # ===== ada model: 1 item per varian =====
                    for m in models:
                        try:
                            model_id  = str(m.get("model_id") or "0")
                            model_sku = (m.get("model_sku") or "").strip()
                            sku       = model_sku if model_sku else f"SHP-{item_id}-{model_id}"

                            model_name = (m.get("model_name") or "").strip()
                            full_name  = _compose_local(base_name, model_name)
                            name_140   = _fit140_local(full_name if full_name else sku)
                            rate       = _norm_rate(m.get("price") or m.get("original_price"))

                            existed = bool(frappe.db.exists("Item", {"item_code": sku}))
                            used_code = _upsert_item(
                                sku, name_140,
                                defaults.get("item_group"), defaults.get("stock_uom"), rate,
                                meta={
                                    "description": (full_name or f"{base_name} - {model_name}" or sku),
                                    "custom_model_sku": model_sku,
                                    "custom_shopee_item_id": str(item_id),
                                    "custom_shopee_model_id": model_id,
                                },
                            )
                            _upsert_price(used_code, defaults.get("price_list"), currency, rate)
                            if existed: updated += 1
                            else:       created += 1

                        except Exception as model_err:
                            error_count += 1
                            frappe.log_error(
                                f"Process model {m.get('model_id')} for item {item_id}: {model_err}",
                                "Shopee sync_items/model",
                            )
                            continue

                except Exception as item_err:
                    error_count += 1
                    frappe.log_error(f"Process item {it.get('item_id')}: {item_err}", "Shopee sync_items/item")
                    continue

            if not has_next:
                break
            offset = resp.get("next_offset", offset + page_size)

        result = {
            "ok": True,
            "window": {"from": time_from, "to": time_to},
            "processed_items": processed_items,
            "created": created,
            "updated": updated,
            "errors": error_count,
        }
        frappe.logger().info(f"[sync_items] done {result}")
        return result

    except Exception as e:
        frappe.log_error(f"sync_items crashed: {e}", "Shopee sync_items")
        return {"ok": False, "error": "exception", "message": str(e)}


@frappe.whitelist()
def test_connection():
    """Test Shopee API connection and token validity."""
    try:
        s = _settings()
        
        if not s.access_token:
            return {"success": False, "error": "No access token configured"}
        
        # Test with shop info API
        result = _call("/api/v2/shop/get_shop_info", 
                      str(s.partner_id).strip(), s.partner_key,
                      s.shop_id, s.access_token, {})
        
        if result.get("error"):
            # Try to refresh token if expired
            if "access token expired" in str(result.get("message", "")).lower():
                refresh_result = refresh_if_needed()
                if refresh_result.get("status") == "refreshed":
                    # Retry with new token
                    result = _call("/api/v2/shop/get_shop_info", 
                                  str(s.partner_id).strip(), s.partner_key,
                                  s.shop_id, s.access_token, {})
            
            if result.get("error"):
                return {"success": False, "error": result.get("error"), "message": result.get("message")}
        
        shop_info = result.get("response", {})
        return {
            "success": True,
            "shop_name": shop_info.get("shop_name"),
            "shop_id": shop_info.get("shop_id"),
            "region": shop_info.get("region"),
            "status": shop_info.get("status")
        }
        
    except Exception as e:
        return {"success": False, "error": "exception", "message": str(e)}

@frappe.whitelist()
def manual_sync_order(order_sn: str):
    """Manually sync a specific order by order SN."""
    try:
        if not order_sn:
            frappe.throw("Order SN is required")
        
        _process_order(order_sn)
        return {"success": True, "message": f"Order {order_sn} synced successfully"}
    except Exception as e:
        frappe.log_error(f"Manual order sync failed for {order_sn}: {str(e)}", "Manual Order Sync")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def get_sync_status():
    """Get current sync status and statistics."""
    try:
        s = _settings()
        
        # Get count of synced orders
        total_orders = frappe.db.count("Sales Invoice", {"custom_shopee_order_sn": ["!=", ""]})
        
        # Get recent sync info
        last_sync_time = None
        if s.last_success_update_time:
            last_sync_time = datetime.fromtimestamp(int(s.last_success_update_time))
        
        # Get recent errors
        recent_errors = frappe.db.count("Error Log", {
            "error": ["like", "%Shopee%"],
            "creation": [">=", datetime.now() - timedelta(hours=24)]
        })
        
        return {
            "success": True,
            "token_status": "valid" if s.access_token else "missing",
            "token_expires": datetime.fromtimestamp(int(s.token_expire_at)) if s.token_expire_at else None,
            "last_sync": last_sync_time,
            "total_synced_orders": total_orders,
            "recent_errors": recent_errors,
            "environment": s.environment
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}

# Scheduled job functions (called by ERPNext scheduler)
def scheduled_order_sync():
    """Scheduled function to sync recent orders (called by scheduler)."""
    try:
        frappe.logger().info("Starting scheduled order sync")
        result = sync_recent_orders(hours=24)  # Sync last 24 hours
        
        if result.get("errors", 0) > 0:
            frappe.logger().warning(f"Order sync completed with {result.get('errors')} errors")
        else:
            frappe.logger().info(f"Order sync completed successfully: {result.get('processed_orders')} orders processed")
            
    except Exception as e:
        frappe.log_error(f"Scheduled order sync failed: {str(e)}", "Scheduled Order Sync")

def scheduled_token_refresh():
    """Scheduled function to refresh token if needed (called by scheduler)."""
    try:
        result = refresh_if_needed()
        if result.get("status") == "refreshed":
            frappe.logger().info("Token refreshed successfully")
    except Exception as e:
        frappe.log_error(f"Scheduled token refresh failed: {str(e)}", "Scheduled Token Refresh")

def scheduled_item_sync():
    """Scheduled function to sync items (called weekly)."""
    try:
        frappe.logger().info("Starting scheduled item sync")
        result = sync_items(hours=168)  # Sync last week
        
        if result.get("ok"):
            frappe.logger().info(f"Item sync completed: {result.get('created')} created, {result.get('updated')} updated")
        else:
            frappe.logger().error(f"Item sync failed: {result.get('message')}")
            
    except Exception as e:
        frappe.log_error(f"Scheduled item sync failed: {str(e)}", "Scheduled Item Sync")

# Helper function for webhook handling (if you implement webhooks)
@frappe.whitelist(allow_guest=True)
def webhook_handler():
    """Handle Shopee webhooks for real-time order updates."""
    try:
        # Get webhook data from request
        data = frappe.local.form_dict
        
        # Validate webhook signature (implement based on Shopee webhook docs)
        # ... signature validation logic ...
        
        # Process webhook data
        event_type = data.get("event")
        
        if event_type == "order_status_update":
            order_sn = data.get("order_sn")
            if order_sn:
                _process_order(order_sn)
                
        return {"success": True}
        
    except Exception as e:
        frappe.log_error(f"Webhook handler failed: {str(e)}", "Shopee Webhook")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def sync_orders_range(time_from: int, time_to: int, page_size: int = 50):
    """Sync orders by absolute date range (time_from/time_to in UNIX seconds)."""
    s = _settings()
    if not s.access_token:
        frappe.throw("Access token required. Please authenticate with Shopee first.")

    if not time_from or not time_to or time_from > time_to:
        frappe.throw("Invalid time range")

    # Pastikan token masih valid
    refresh_if_needed()

    statuses = ["READY_TO_SHIP", "PROCESSED", "COMPLETED"]
    seen = set()
    processed_count, error_count = 0, 0
    highest = int(s.last_success_update_time or 0)
    page_size = int(page_size)

    for st in statuses:
        offset = 0
        while True:
            try:
                resp = _call(
                    "/api/v2/order/get_order_list",
                    str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token,
                    {
                        "time_range_field": "update_time",
                        "time_from": int(time_from),
                        "time_to": int(time_to),
                        "page_size": page_size,
                        "order_status": st,
                        "offset": offset,
                    },
                )

                if resp.get("error"):
                    frappe.log_error(f"sync_orders_range error: {resp}", "Shopee API")
                    break

                data = resp.get("response") or {}
                orders = data.get("order_list") or []
                if not orders:
                    break

                for o in orders:
                    order_sn = o.get("order_sn")
                    if not order_sn or order_sn in seen:
                        continue
                    seen.add(order_sn)

                    if not frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}):
                        _process_order(order_sn)
                        processed_count += 1

                    ut = int(o.get("update_time") or 0)
                    if ut > highest:
                        highest = ut

                if not data.get("has_next_page"):
                    break
                offset = data.get("next_offset", offset + page_size)

            except Exception as e:
                error_count += 1
                frappe.log_error(f"sync_orders_range batch error: {e}", "Shopee Sync Range")
                if error_count > 5:
                    break
                continue

    if highest > int(s.last_success_update_time or 0):
        s.last_success_update_time = highest
        s.save(ignore_permissions=True)
        frappe.db.commit()

    return {
        "from": time_from,
        "to": time_to,
        "max_update_time": highest,
        "processed_orders": processed_count,
        "errors": error_count,
        "success": error_count < processed_count or processed_count == 0,
    }

@frappe.whitelist()
def diagnose_order(order_sn: str, hours: int = 72):
    """Diagnosa cepat: detail, escrow, dan cek kemunculan order di get_order_list."""
    import time, json

    s = _settings()
    pid  = str(s.partner_id).strip()
    pkey = s.partner_key
    sid  = s.shop_id
    atok = s.access_token
    now_ts = int(time.time())

    # init supaya aman dipakai di fallback
    ct  = 0   # create_time
    sbd = 0   # ship_by_date

    # --- DETAIL ---
    detail = _call(
        "/api/v2/order/get_order_detail",
        pid, pkey, sid, atok,
        {"order_sn_list": str(order_sn), "response_optional_fields": "item_list,recipient_address,buyer_info"}
    )

    detail_error = None
    summary = None

    if isinstance(detail, dict) and not detail.get("error"):
        lst = (detail.get("response") or {}).get("order_list", []) or []
        if lst:
            od = lst[0]

            def _si(v):
                try:
                    return int(v or 0)
                except Exception:
                    return 0

            ct  = _si(od.get("create_time"))
            sbd = _si(od.get("ship_by_date"))
            if not sbd:
                dts = _si(od.get("days_to_ship"))
                if dts and ct:
                    sbd = ct + dts * 86400

            items = []
            for it in (od.get("item_list") or []):
                items.append({
                    "item_id":   it.get("item_id"),
                    "model_id":  it.get("model_id"),
                    "item_name": it.get("item_name"),
                    "model_name": it.get("model_name"),
                    "qty":  it.get("model_quantity_purchased") or it.get("variation_quantity_purchased"),
                    "price": it.get("model_discounted_price") or it.get("order_price") or it.get("item_price")
                })

            summary = {
                "order_sn": order_sn,
                "status": od.get("order_status"),
                "create_time": ct,
                "create_time_human": _hum_epoch(ct),
                "ship_by": sbd,
                "ship_by_human": _hum_epoch(sbd),
                "buyer_username": od.get("buyer_username"),
                "recipient_name": (od.get("recipient_address") or {}).get("name"),
                "items": items
            }
    else:
        detail_error = detail

    # --- ESCROW ---
    escrow = _call(
        "/api/v2/payment/get_escrow_detail",
        pid, pkey, sid, atok,
        {"order_sn": str(order_sn)}
    )
    escrow_error = None
    esc = {}
    if isinstance(escrow, dict) and not escrow.get("error"):
        er = (escrow.get("response") or {}) or {}
        def _f(x):
            try: return float(x or 0)
            except: return 0.0
        esc = {
            "net": _f(er.get("escrow_amount") or er.get("payout_amount") or er.get("net_amount")),
            "commission": _f(er.get("seller_commission_fee") or er.get("commission_fee")),
            "service": _f(er.get("seller_service_fee") or er.get("service_fee")),
            "protection": _f(er.get("shipping_seller_protection_fee_amount")),
            "shipdiff": _f(er.get("shipping_fee_difference")),
            "voucher": _f(er.get("voucher_seller")) + _f(er.get("coin_cash_back")) + _f(er.get("voucher_code_seller")),
        }
    else:
        escrow_error = escrow

    # --- VISIBILITY DI LIST ---
    visible_in_list = False
    status_hits = []

    # 1) cek by update_time (window hours)
    ol = _call(
        "/api/v2/order/get_order_list",
        pid, pkey, sid, atok,
        {
            "time_range_field": "update_time",
            "time_from": now_ts - int(hours) * 3600,
            "time_to": now_ts,
            "page_size": 50, "offset": 0
        }
    )
    if isinstance(ol, dict) and not ol.get("error"):
        for o in (ol.get("response") or {}).get("order_list", []) or []:
            if o.get("order_sn") == order_sn:
                visible_in_list = True
                status_hits.append(o.get("order_status"))
                break

    # 2) fallback: cek by create_time (±2 hari sekitar create_time)
    if not visible_in_list and ct:
        ol2 = _call(
            "/api/v2/order/get_order_list",
            pid, pkey, sid, atok,
            {
                "time_range_field": "create_time",
                "time_from": ct - 2 * 86400,
                "time_to":   ct + 2 * 86400,
                "page_size": 50, "offset": 0
            }
        )
        if isinstance(ol2, dict) and not ol2.get("error"):
            for o in (ol2.get("response") or {}).get("order_list", []) or []:
                if o.get("order_sn") == order_sn:
                    visible_in_list = True
                    status_hits.append(o.get("order_status"))
                    break

    return {
        "ok": bool(summary),
        "order_sn": order_sn,
        "visible_in_list": visible_in_list,
        "visible_status_hits": status_hits,
        "detail_error": detail_error,
        "escrow_error": escrow_error,
        "detail": summary,
        "escrow": esc
    }

@frappe.whitelist()
def backfill_mapping_from_legacy_codes(dry_run: int = 1):
    import re
    pats = [r"^SHP-(\d+)-(\d+)$", r"^(\d+)-(\d+)$", r"^(\d+)_(\d+)$"]
    rows = frappe.get_all("Item", fields=["name","item_code"])
    matched = 0
    for r in rows:
        for p in pats:
            m = re.match(p, r.item_code or "")
            if m:
                item_id, model_id = m.group(1), m.group(2)
                matched += 1
                if not int(dry_run):
                    frappe.db.set_value("Item", r.name, {
                        "custom_shopee_item_id": item_id,
                        "custom_shopee_model_id": model_id
                    })
                break
    if not int(dry_run): frappe.db.commit()
    return {"matched": matched, "updated": int(not dry_run)}

@frappe.whitelist()
def backfill_mapping_from_shopee(limit_pages: int = 999):
    s = _settings()
    updated = 0
    offset, page_size, pages = 0, 50, 0
    while pages < limit_pages:
        lst = _call("/api/v2/product/get_item_list",
                    str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token,
                    {"offset": offset, "page_size": page_size})
        resp = lst.get("response") or {}
        for it in resp.get("item", []) + resp.get("items", []):
            item_id = str(it.get("item_id") or "")
            md = _call("/api/v2/product/get_model_list",
                       str(s.partner_id).strip(), s.partner_key,
                       s.shop_id, s.access_token,
                       {"item_id": int(item_id)})
            for m in (md.get("response") or {}).get("model", []) + (md.get("response") or {}).get("model_list", []):
                model_id = str(m.get("model_id") or "")
                model_sku = (m.get("model_sku") or "").strip()
                # cari item existing by legacy code / custom id / sku
                candidates = [f"SHP-{item_id}-{model_id}", f"{item_id}-{model_id}", f"{item_id}_{model_id}"]
                name = (frappe.db.get_value("Item", {"custom_shopee_item_id": item_id, "custom_shopee_model_id": model_id}, "name")
                        or (frappe.db.get_value("Item", {"item_code": model_sku}, "name") if model_sku else None))
                if not name:
                    for c in candidates:
                        name = frappe.db.get_value("Item", {"item_code": c}, "name")
                        if name: break
                if name:
                    updates = {
                        "custom_shopee_item_id": item_id,
                        "custom_shopee_model_id": model_id
                    }
                    if model_sku: updates["custom_model_sku"] = model_sku
                    frappe.db.set_value("Item", name, updates)
                    updated += 1
        if not resp.get("has_next_page"): break
        offset = resp.get("next_offset", offset + page_size); pages += 1
    frappe.db.commit()
    return {"updated": updated}

def _clean_title(s: str) -> str:
    if not s: return ""
    s = s.strip()
    # hapus pola ID yang suka nempel
    import re
    s = re.sub(r"SHP-\d+-\d+", "", s)
    s = re.sub(r"\b\d{6,}\b", "", s)      # token angka panjang
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

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
    model_sku = (it.get("model_sku") or "").strip()
    item_id   = str(it.get("item_id") or "")
    model_id  = str(it.get("model_id") or "0")

    # 1) sudah ada by SKU (item_code/custom field)
    if model_sku:
        name = (frappe.db.get_value("Item", {"item_code": model_sku}, "name")
                or frappe.db.get_value("Item", {"custom_model_sku": model_sku}, "name"))
        if name:
            _ensure_mapping_fields(name, model_sku, item_id, model_id)
            return name

    # 2) mapping by IDs
    name = frappe.db.get_value("Item",
                               {"custom_shopee_item_id": item_id, "custom_shopee_model_id": model_id},
                               "name")
    if name:
        _ensure_mapping_fields(name, model_sku, item_id, model_id)
        return name

    # 3) legacy pola item_code berbasis ID
    for c in (f"SHP-{item_id}-{model_id}", f"{item_id}-{model_id}", f"{item_id}_{model_id}"):
        name = frappe.db.get_value("Item", {"item_code": c}, "name")
        if name:
            _ensure_mapping_fields(name, model_sku, item_id, model_id)
            return name

    # 4) Buat baru → NAMA jangan pakai ID; pakai nama dari Shopee & dibersihkan
    code = model_sku or f"{item_id}-{model_id}"      # item_code boleh pakai SKU/ID
    nice_name = _clean_title(it.get("model_name") or it.get("item_name") or code)[:140]

    item = frappe.new_doc("Item")
    item.item_code = _fit140(code)
    item.item_name = _fit140(nice_name)          # << ini yang diperbaiki
    item.item_group = frappe.db.get_single_value("Stock Settings", "default_item_group") or "All Item Groups"
    item.stock_uom = "Nos"
    item.is_stock_item = 1
    item.maintain_stock = 1
    item.custom_model_sku = model_sku or ""
    item.custom_shopee_item_id = item_id
    item.custom_shopee_model_id = model_id
    wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    if wh:
        item.append("item_defaults", {"default_warehouse": wh})
    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name

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
    """Return payload mentah get_order_detail untuk 1 SN."""
    s = _settings()
    return _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token,
        {
            "order_sn_list": str(order_sn),  # HARUS string, bukan array
            "response_optional_fields": "item_list,recipient_address,buyer_info"
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
            def _safe_int(v): 
                try: return int(v or 0)
                except: return 0
            ct  = _safe_int(od.get("create_time"))
            sbd = _safe_int(od.get("ship_by_date"))
            dts = _safe_int(od.get("days_to_ship"))
            if not sbd and ct and dts:
                sbd = ct + dts * 86400

            def _hum(ts):
                try:
                    return frappe.utils.format_datetime(frappe.utils.convert_utc_to_user_datetime(ts))
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
        def _f(x):
            try: return float(x or 0)
            except: return 0.0
        esc_summary = {
            "net": _f(er.get("escrow_amount") or er.get("payout_amount") or er.get("net_amount")),
            "commission": _f(er.get("seller_commission_fee") or er.get("commission_fee")),
            "service": _f(er.get("seller_service_fee") or er.get("service_fee")),
            "protection": _f(er.get("shipping_seller_protection_fee_amount")),
            "shipdiff": _f(er.get("shipping_fee_difference")),
            "voucher": _f(er.get("voucher_seller")) + _f(er.get("coin_cash_back")) + _f(er.get("voucher_code_seller")),
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
