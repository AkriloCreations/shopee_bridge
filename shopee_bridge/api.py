import time, hmac, hashlib, requests, frappe # pyright: ignore[reportMissingImports]
from frappe.utils import get_url, nowdate # pyright: ignore[reportMissingImports]

def _settings():
    return frappe.get_single("Shopee Settings")

def _safe_int(v, d=0):
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d

def _base():
    """Host Shopee sesuai Environment di Shopee Settings."""
    s = _settings()
    env = (getattr(s, "environment", None) or "Test").strip()
    if env == "Production":
        return "https://partner.shopeemobile.com"
    return "https://partner.test-stable.shopeemobile.com"

def _safe_flt(v, d=0.0):
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

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

    # redirect HARUS sama persis dengan yang didaftarkan di Shopee OP
    redirect = "https://erp.managerio.ddns.net/app/shopee-settings"

    # urutan param disusun seperti contoh resmi Shopee: partner_id, redirect, timestamp, sign
    url = (
        f"{_base()}{path}"
        f"?partner_id={partner_id}"
        f"&redirect={quote(redirect,safe='')}"
        f"&timestamp={ts}"
        f"&sign={sign}"
    )
    return {"url": url}

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

@frappe.whitelist()
def sync_recent_orders(hours: int = 24, page_size: int = 50, use_create_time_fallback: bool = True):
    """
    Sinkronisasi order Shopee berdasarkan update_time (fallback: create_time):
      - READY_TO_SHIP / PROCESSED -> proses _process_order (SO/logic kamu)
      - CANCELLED                 -> cancel SO/SI kalau ada
      - COMPLETED                 -> pastikan SI ada + buat Payment Entry jika belum ada
    v2 cursor pagination, satu status per panggilan (Shopee tidak menerima list status di satu call).
    """
    import time as _t
    s = _settings()
    if not getattr(s, "access_token", ""):
        frappe.throw("Access token required. Please authenticate with Shopee first.")

    # hours dari UI SELALU dipakai (override high-water), biar user kontrol rentang
    now = int(_t.time())
    time_from = now - int(hours) * 3600
    time_to = now

    STATUSES = ("READY_TO_SHIP", "PROCESSED", "COMPLETED", "CANCELLED")

    highest_ut = 0
    total_processed = 0
    errors = 0
    by_status_counts = {st: 0 for st in STATUSES}

    def _pull_for_status(status: str, time_field: str) -> list[dict]:
        """Tarik semua halaman untuk satu status & time_field (update_time/create_time)."""
        items, cursor, page_idx = [], "", 0
        while True:
            params = {
                "time_range_field": time_field,     # "update_time" atau "create_time"
                "time_from": time_from,
                "time_to": time_to,
                "page_size": int(page_size),
                "order_status": status,
            }
            if cursor:
                params["cursor"] = cursor

            resp = _call(
                "/api/v2/order/get_order_list",
                str(s.partner_id).strip(),
                s.partner_key,
                s.shop_id,
                s.access_token,
                params
            )
            if resp.get("error"):
                raise Exception(f"get_order_list error={resp.get('error')} msg={resp.get('message')} status={status} page={page_idx}")

            body = resp.get("response") or {}
            lst = body.get("order_list", []) or []
            items.extend(lst)

            frappe.logger().info(f"[Shopee Sync] status={status} time_field={time_field} page={page_idx} got={len(lst)} more={body.get('more')}")
            page_idx += 1

            if body.get("more"):
                cursor = body.get("next_cursor") or ""
                if not cursor:  # sanity
                    break
            else:
                break
        return items

    def _get_detail_light(order_sn: str) -> dict:
        """Ambil detail minimal untuk jumlah/fee dari get_order_detail (lebih cepat dari payment detail)."""
        r = _call(
            "/api/v2/order/get_order_detail",
            str(s.partner_id).strip(),
            s.partner_key,
            s.shop_id,
            s.access_token,
            {
                "order_sn_list": [order_sn],
                "response_optional_fields": "order_status,update_time,total_amount,escrow_amount,payout_amount"
            }
        )
        if r.get("error"):
            frappe.logger().warning(f"[Shopee Sync] get_order_detail error {order_sn}: {r.get('error')} {r.get('message')}")
            return {}
        ol = (r.get("response") or {}).get("order_list") or []
        return ol[0] if ol else {}

    def _get_escrow_detail(order_sn: str) -> dict:
        """Ambil payment/escrow detail untuk fee breakdown akurat."""
        r = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(),
            s.partner_key,
            s.shop_id,
            s.access_token,
            {"order_sn": order_sn}
        )
        if r.get("error"):
            frappe.logger().warning(f"[Shopee Sync] get_escrow_detail error {order_sn}: {r.get('error')} {r.get('message')}")
            return {}
        return r.get("response") or {}

    def _ensure_payment_entry(order_sn: str):
        """Pastikan ada PE untuk order COMPLETED: coba get_order_detail dulu, kalau kosong, pakai escrow_detail."""
        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if not si_name:
            # Pastikan SI ada (jalankan _process_order sekali lagi sebagai jaring pengaman)
            _process_order(order_sn)
            si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
            if not si_name:
                frappe.logger().info(f"[Shopee Sync] No Sales Invoice for COMPLETED order {order_sn}")
                return

        if frappe.db.exists("Payment Entry", {"reference_no": order_sn}):
            return  # sudah ada

        # 1) coba dari get_order_detail (cepat)
        d = _get_detail_light(order_sn)
        net_amount = _safe_flt(d.get("escrow_amount")) or _safe_flt(d.get("payout_amount"))

        escrow_payload = {}
        if net_amount <= 0:
            # 2) fallback ke escrow detail untuk fee & net yang akurat
            escrow_detail = _get_escrow_detail(order_sn)
            # map beberapa field yang dipakai create_payment_entry_from_shopee()
            escrow_payload = {
                "commission_fee": _safe_flt(escrow_detail.get("commission_fee")),
                "service_fee": _safe_flt(escrow_detail.get("service_fee")),
                "shipping_seller_protection_fee_amount": _safe_flt(escrow_detail.get("shipping_seller_protection_fee_amount")),
                "shipping_fee_difference": _safe_flt(escrow_detail.get("shipping_fee_difference")),
                "voucher_seller": _safe_flt(escrow_detail.get("voucher_seller")),
                "coin_cash_back": _safe_flt(escrow_detail.get("coin_cash_back")),
                "voucher_code_seller": _safe_flt(escrow_detail.get("voucher_code_seller")),
            }
            # net:
            net_amount = _safe_flt(
                escrow_detail.get("payout_amount") or escrow_detail.get("escrow_amount")
            )

        posting_ts = _safe_int(d.get("update_time"))
        from .webhook import create_payment_entry_from_shopee
        create_payment_entry_from_shopee(
            si_name=si_name,
            escrow=escrow_payload or d,  # kalau d punya angka, pakai itu; kalau tidak, escrow_payload
            net_amount=net_amount or 0,
            order_sn=order_sn,
            posting_ts=posting_ts,
            enqueue=True
        )
        frappe.logger().info(f"[Shopee Sync] Payment Entry enqueued for {si_name} (order {order_sn})")

    # --------- eksekusi utama ----------
    frappe.logger().info(f"[Shopee Sync] partner={s.partner_id} shop={s.shop_id} range={time_from}->{time_to} hours={hours}")

    try_order_time_fields = ["update_time"]
    if use_create_time_fallback:
        try_order_time_fields.append("create_time")

    for time_field in try_order_time_fields:
        loop_processed_before = total_processed

        for status in STATUSES:
            try:
                orders = _pull_for_status(status, time_field)
            except Exception as e:
                errors += 1
                frappe.log_error(frappe.get_traceback(), f"Shopee pull failed status={status} time_field={time_field}")
                continue

            by_status_counts[status] += len(orders)

            for o in orders:
                order_sn = o.get("order_sn")
                order_status = o.get("order_status") or status
                if not order_sn:
                    continue

                try:
                    if order_status in ("READY_TO_SHIP", "PROCESSED"):
                        _process_order(order_sn)

                    elif order_status == "CANCELLED":
                        so_name = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
                        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
                        for doctype, name in (("Sales Order", so_name), ("Sales Invoice", si_name)):
                            if not name:
                                continue
                            try:
                                doc = frappe.get_doc(doctype, name)
                                if doc.docstatus == 1:
                                    doc.cancel()
                                    frappe.logger().info(f"[Shopee Sync] Cancelled {doctype} {name} for {order_sn}")
                            except Exception:
                                errors += 1
                                frappe.log_error(frappe.get_traceback(), f"Cancel {doctype} error for {order_sn}")

                    elif order_status == "COMPLETED":
                        _process_order(order_sn)  # pastikan SO/SI terbentuk sesuai logic kamu
                        _ensure_payment_entry(order_sn)

                    # track latest update_time bila tersedia
                    ut = _safe_int(o.get("update_time"))
                    if ut > highest_ut:
                        highest_ut = ut

                    total_processed += 1

                except Exception:
                    errors += 1
                    frappe.log_error(frappe.get_traceback(), f"Process order error {order_sn}")

        # kalau di time_field pertama (update_time) tidak ada yang diproses dan kita punya fallback create_time, lanjut; kalau sudah ada, tidak perlu fallback
        if total_processed > loop_processed_before:
            break  # sudah ada hasil di update_time; tak perlu coba create_time

    # update high-water mark hanya jika ada yang diproses
    if highest_ut > 0 and total_processed > 0:
        s.last_success_update_time = highest_ut
        s.save(ignore_permissions=True)
        frappe.db.commit()

    return {
        "from": time_from,
        "to": time_to,
        "processed": total_processed,
        "errors": errors,
        "by_status": by_status_counts,
        "max_update_time": highest_ut,
        "used_fallback": (try_order_time_fields[-1] == "create_time" and total_processed == 0)
    }

def _ensure_item_exists(sku: str, item_data: dict, rate: float) -> str:
    """
    Ensure item exists in ERPNext, create if not found.
    Returns the item_code to use.
    """
    # Check if item already exists
    if frappe.db.exists("Item", sku):
        return sku
    
    # Extract item info from Shopee data
    item_name = item_data.get("item_name") or \
                item_data.get("model_name") or \
                item_data.get("variation_name") or \
                f"Shopee Item {sku}"
    
    # Create new Item
    try:
        item = frappe.new_doc("Item")
        item.item_code = sku
        item.item_name = item_name[:140]  # ERPNext limit
        item.item_group = "All Item Groups"  # Default group
        item.stock_uom = "Nos"  # Default UOM
        item.is_stock_item = 1
        item.include_item_in_manufacturing = 0
        item.is_sales_item = 1
        item.is_purchase_item = 1
        item.maintain_stock = 1
        item.valuation_rate = rate
        item.standard_rate = rate
        
        # Shopee specific fields (if custom fields exist)
        if hasattr(item, 'shopee_item_id'):
            item.shopee_item_id = item_data.get("item_id")
        if hasattr(item, 'shopee_model_id'):
            item.shopee_model_id = item_data.get("model_id")
        if hasattr(item, 'shopee_sku'):
            item.shopee_sku = item_data.get("model_sku") or item_data.get("item_sku")
        
        # Description with Shopee info
        description_parts = []
        if item_data.get("item_name"):
            description_parts.append(f"Item: {item_data.get('item_name')}")
        if item_data.get("model_name"):
            description_parts.append(f"Variant: {item_data.get('model_name')}")
        if item_data.get("item_id"):
            description_parts.append(f"Shopee ID: {item_data.get('item_id')}")
        if item_data.get("model_id"):
            description_parts.append(f"Model ID: {item_data.get('model_id')}")
            
        item.description = " | ".join(description_parts)[:500]  # ERPNext limit
        
        item.insert(ignore_permissions=True)
        
        frappe.logger().info(f"Auto-created item: {sku} - {item_name}")
        return sku
        
    except Exception as e:
        # If item creation fails, log error and return original SKU
        frappe.log_error(f"Failed to create item {sku}: {str(e)}", "Shopee Item Creation")
        
        # Try to use existing similar item or create minimal fallback
        return _create_fallback_item(sku, item_name, rate)

def _create_fallback_item(sku: str, item_name: str, rate: float) -> str:
    """Create minimal fallback item if normal creation fails"""
    try:
        # Try with minimal fields
        item = frappe.new_doc("Item")
        item.item_code = sku
        item.item_name = item_name[:140]
        item.item_group = "All Item Groups"
        item.stock_uom = "Nos"
        item.is_stock_item = 1
        item.is_sales_item = 1
        item.standard_rate = rate
        item.insert(ignore_permissions=True)
        return sku
    except Exception as e:
        frappe.log_error(f"Fallback item creation also failed for {sku}: {str(e)}", "Shopee Fallback Item")
        # Return a generic item code - you may want to create a catch-all item
        return "SHOPEE-UNKNOWN-ITEM"

def _process_order(order_sn: str):
    s = _settings()
    if frappe.db.exists("Sales Invoice", {"shopee_order_sn": order_sn}):
        return

    det = _call("/api/v2/order/get_order_detail", str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token, {"order_sn_list": order_sn})
    
    if det.get("error"):
        frappe.log_error(f"Failed to get order detail for {order_sn}: {det.get('message')}")
        return
        
    order_list = det.get("response", {}).get("order_list", [])
    if not order_list:
        return
    det = order_list[0]

    esc = _call("/api/v2/payment/get_escrow_detail", str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token, {"order_sn": order_sn})
    
    if esc.get("error"):
        frappe.log_error(f"Failed to get escrow detail for {order_sn}: {esc.get('message')}")
        esc = {"response": {}}
        
    esc = esc.get("response", {}) or {}

    customer = f"SHP-{det.get('buyer_username') or 'UNKNOWN'}"
    if not frappe.db.exists("Customer", {"customer_name": customer}):
        c = frappe.new_doc("Customer")
        c.customer_name = customer
        c.customer_group = "All Customer Groups"
        c.customer_type = "Individual"
        c.insert(ignore_permissions=True)

    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = nowdate()
    si.set_posting_time = 1
    si.update_stock = 1
    si.currency = "IDR"
    si.shopee_order_sn = order_sn
    si.remarks = f"Shopee order SN {order_sn}"

    for it in det.get("item_list", []):
        # SKU fallback priority: model_sku -> item_sku -> item_id-model_id
        sku = (it.get("model_sku") or "").strip() or \
              (it.get("item_sku") or "").strip() or \
              f"SHP-{it.get('item_id')}-{it.get('model_id', '0')}"
        
        # Ensure SKU is not empty
        if not sku:
            sku = f"SHP-UNKNOWN-{order_sn}-{it.get('item_id', 'NOITEM')}"
        
        qty = int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
        rate = float(it.get("model_original_price") or it.get("model_discounted_price") or it.get("order_price") or 0)
        
        # Auto-create item if not exists
        item_code = _ensure_item_exists(sku, it, rate)
        
        row = si.append("items", {})
        row.item_code = item_code
        row.qty = qty
        row.rate = rate

    si.insert(ignore_permissions=True)
    si.submit()

    net = float(esc.get("escrow_amount") or esc.get("net_amount") or esc.get("payout_amount") or 0)
    if net <= 0:
        return

    ACC = {
        "commission": "Komisi Shopee",
        "service":    "Biaya Layanan Shopee",
        "protection": "Proteksi Pengiriman Shopee",
        "shipdiff":   "Selisih Ongkir Shopee",
        "voucher":    "Voucher Shopee"
    }

    fees = {
        "commission": float(esc.get("commission_fee") or esc.get("seller_commission_fee") or 0),
        "service":    float(esc.get("service_fee") or esc.get("seller_service_fee") or 0),
        "protection": float(esc.get("shipping_seller_protection_fee_amount") or 0),
        "shipdiff":   float(esc.get("shipping_fee_difference") or 0),
        "voucher":    float(esc.get("voucher_seller") or 0)
                      + float(esc.get("coin_cash_back") or 0)
                      + float(esc.get("voucher_code_seller") or 0),
    }

    paid_from = frappe.db.get_single_value("Accounts Settings", "default_receivable_account") or "Debtors - AC"
    paid_to   = "Bank - Shopee (Escrow)"

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Receive"
    pe.party_type = "Customer"
    pe.party = customer
    pe.posting_date = nowdate()
    pe.mode_of_payment = "Shopee"
    pe.paid_from = paid_from
    pe.paid_to = paid_to
    pe.paid_amount = net
    pe.received_amount = net

    r = pe.append("references", {})
    r.reference_doctype = "Sales Invoice"
    r.reference_name = si.name
    r.allocated_amount = net + sum(fees.values())

    for k, v in fees.items():
        if v:
            d = pe.append("deductions", {})
            d.account = ACC[k]
            d.amount  = v

    pe.insert(ignore_permissions=True)
    pe.submit()

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
    s = _settings()
    return {
        "price_list": getattr(s, "price_list", None) or "Standard Selling",
        "item_group": getattr(s, "item_group", None) or "Products",
        "stock_uom":  getattr(s, "stock_uom",  None) or "pcs",
    }

def _get_or_create_price_list(pl_name: str):
    if not frappe.db.exists("Price List", {"price_list_name": pl_name}):
        pl = frappe.new_doc("Price List")
        pl.price_list_name = pl_name
        pl.selling = 1
        pl.insert(ignore_permissions=True)

def _upsert_price(item_code: str, price_list: str, currency: str, rate: float):
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

def _upsert_item(item_code: str, item_name: str, item_group: str, stock_uom: str, rate: float) -> str:
    """Idempotent upsert Item. Return item_code actually used."""
    try:
        exists = frappe.db.exists("Item", {"item_code": item_code})
        if exists:
            doc = frappe.get_doc("Item", exists)
            # update minimal fields bila kosong/berubah
            if item_name and doc.item_name != item_name[:140]:
                doc.item_name = item_name[:140]
            if doc.item_group != item_group:
                doc.item_group = item_group
            if doc.stock_uom != stock_uom:
                doc.stock_uom = stock_uom
            # standar rate hanya sebagai default (boleh diupdate)
            if rate and (float(doc.get("standard_rate") or 0) != float(rate)):
                doc.standard_rate = rate
            doc.save(ignore_permissions=True)
            return doc.name
        else:
            doc = frappe.new_doc("Item")
            doc.item_code = item_code
            doc.item_name = item_name[:140]
            doc.item_group = item_group
            doc.stock_uom = stock_uom
            doc.is_stock_item = 1
            doc.is_sales_item = 1
            if rate:
                doc.standard_rate = rate
            doc.insert(ignore_permissions=True)
            return doc.name
    except Exception:
        # fallback terakhir
        return _create_fallback_item(item_code, item_name, rate)

def _get_models_for_item(item_id: int):
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


# --- nama item dasar (tanpa model) ---
def _get_item_base_info(item_id: int):
    s = _settings()
    res = _call(
        "/api/v2/product/get_item_base_info",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {"item_id_list": str(item_id)},
    )
    lst = (res.get("response") or {}).get("item_list", []) or []
    return lst[0] if lst and isinstance(lst, list) else {}

@frappe.whitelist()
def sync_items(hours: int = 720, status: str = "NORMAL"):
    """
    Sinkron Item dari Shopee ke ERPNext dengan model_sku sebagai Item Code bila ada.
    - hours: jendela update_time ke belakang (default 30 hari).
    - status: filter Shopee item_status (default 'NORMAL').
    """
    s = _settings()
    defaults = _cfg_defaults()
    currency = "IDR"

    now = int(time.time())
    time_from = now - hours * 3600
    time_to = now

    page_size, offset = 100, 0
    created, updated = 0, 0
    processed_items = 0

    while True:
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

        # Guard: pastikan dict & bukan error
        if not isinstance(gl, dict):
            frappe.log_error(
                f"Unexpected get_item_list payload type: {type(gl).__name__}",
                "Shopee sync_items",
            )
            return {"ok": False, "error": "bad_payload_type"}

        if gl.get("error"):
            frappe.log_error(f"get_item_list error: {gl.get('error')} - {gl.get('message')}", "Shopee sync_items")
            return {"ok": False, "error": gl.get("error"), "message": gl.get("message")}

        resp = gl.get("response") or {}
        # Beberapa region: 'item' vs 'items', 'has_next_page' vs 'has_next'
        item_list = (resp.get("item") or resp.get("items") or [])
        if not isinstance(item_list, list):
            item_list = []

        has_next = bool(resp.get("has_next_page") or resp.get("has_next") or False)

        for it in item_list:
            processed_items += 1
            item_id = int(it.get("item_id"))
            base = _get_item_base_info(item_id)
            base_name = base.get("item_name") or f"Item {item_id}"

            models = _get_models_for_item(item_id)

            # Tanpa model → satu Item
            if not models:
                sku = str(base.get("item_sku") or item_id)
                rate = float(base.get("normal_price") or 0)
                used_code = _upsert_item(
                    sku, base_name, defaults["item_group"], defaults["stock_uom"], rate
                )
                _upsert_price(used_code, defaults["price_list"], currency, rate)
                if not frappe.db.exists("Item", {"item_code": sku}):
                    created += 1
                else:
                    updated += 1
                continue

            # Ada model → satu Item per model
            for m in models:
                model_sku = (m.get("model_sku") or "").strip()
                sku = model_sku if model_sku else f"{item_id}-{m.get('model_id')}"
                model_name = m.get("model_name") or ""
                name = f"{base_name} - {model_name}" if model_name else base_name
                rate = float(m.get("price") or m.get("original_price") or 0)

                before_exists = frappe.db.exists("Item", {"item_code": sku})
                used_code = _upsert_item(
                    sku, name, defaults["item_group"], defaults["stock_uom"], rate
                )
                _upsert_price(used_code, defaults["price_list"], currency, rate)
                if not before_exists and frappe.db.exists("Item", {"item_code": used_code}):
                    created += 1
                else:
                    updated += 1

        if not has_next:
            break
        offset = resp.get("next_offset", offset + page_size)

    return {
        "ok": True,
        "window": {"from": time_from, "to": time_to},
        "processed_items": processed_items,
        "created": created,
        "updated": updated,
    }