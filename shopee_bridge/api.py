import time, hmac, hashlib, requests, frappe
from frappe.utils import get_url, nowdate
from datetime import datetime, timedelta
import json

def _settings():
    return frappe.get_single("Shopee Settings")

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
                            if not frappe.db.exists("Sales Invoice", {"shopee_order_sn": order_sn}):
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


def _ensure_item_exists(sku: str, item_data: dict, rate: float) -> str:
    """
    Enhanced item creation with better error handling.
    Ensure item exists in ERPNext, create if not found.
    Returns the item_code to use.
    """
    # Sanitize SKU to ensure it's valid
    if not sku or not sku.strip():
        sku = f"SHP-UNKNOWN-{item_data.get('item_id', 'NOITEM')}-{int(time.time())}"
    
    sku = sku.strip()[:140]  # ERPNext item_code limit
    
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
        item.item_group = _get_item_group()
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
        # If item creation fails, log error and return fallback
        frappe.log_error(f"Failed to create item {sku}: {str(e)}", "Shopee Item Creation")
        return _create_fallback_item(sku, item_name, rate)

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

def _process_order(order_sn: str):
    """Enhanced order processing with better error handling."""
    s = _settings()
    
    # Check if order already processed
    if frappe.db.exists("Sales Invoice", {"shopee_order_sn": order_sn}):
        frappe.logger().info(f"Order {order_sn} already processed, skipping")
        return

    try:
        # Get order details
        det = _call("/api/v2/order/get_order_detail", str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token, {"order_sn_list": [order_sn]})
        
        if det.get("error"):
            frappe.log_error(f"Failed to get order detail for {order_sn}: {det.get('message')}")
            return
            
        order_list = det.get("response", {}).get("order_list", [])
        if not order_list:
            frappe.log_error(f"No order data found for {order_sn}", "Shopee Order Processing")
            return
        det = order_list[0]

        # Get escrow details
        esc = _call("/api/v2/payment/get_escrow_detail", str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token, {"order_sn": order_sn})
        
        if esc.get("error"):
            frappe.log_error(f"Failed to get escrow detail for {order_sn}: {esc.get('message')}")
            esc = {"response": {}}
            
        esc = esc.get("response", {}) or {}

        # Create or get customer
        customer = _create_or_get_customer(det)

        # Create Sales Invoice
        si = frappe.new_doc("Sales Invoice")
        si.customer = customer
        si.posting_date = nowdate()
        si.set_posting_time = 1
        si.update_stock = 1
        si.currency = "IDR"
        si.shopee_order_sn = order_sn
        si.remarks = f"Shopee order SN {order_sn}"

        # Set company from settings
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            si.company = company

        # Add items to invoice
        for it in det.get("item_list", []):
            try:
                # SKU fallback priority: model_sku -> item_sku -> item_id-model_id
                sku = (it.get("model_sku") or "").strip() or \
                      (it.get("item_sku") or "").strip() or \
                      f"SHP-{it.get('item_id')}-{it.get('model_id', '0')}"
                
                # Ensure SKU is not empty
                if not sku:
                    sku = f"SHP-UNKNOWN-{order_sn}-{it.get('item_id', 'NOITEM')}"
                
                qty = int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
                rate = float(it.get("model_original_price") or it.get("model_discounted_price") or it.get("order_price") or 0)
                
                # Convert from Shopee micro units (if rate is very high, it's likely in micro units)
                if rate > 1000000:  # Likely in micro units
                    rate = rate / 100000
                
                # Auto-create item if not exists
                item_code = _ensure_item_exists(sku, it, rate)
                
                row = si.append("items", {})
                row.item_code = item_code
                row.qty = qty
                row.rate = rate
                row.amount = qty * rate
                
                # Set warehouse if configured
                warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
                if warehouse:
                    row.warehouse = warehouse

            except Exception as item_error:
                frappe.log_error(f"Failed to process item in order {order_sn}: {str(item_error)}", 
                               "Shopee Item Processing")
                continue

        # Only proceed if we have items
        if not si.items:
            frappe.log_error(f"No valid items found for order {order_sn}", "Shopee Order Processing")
            return

        si.insert(ignore_permissions=True)
        si.submit()
        
        frappe.logger().info(f"Created Sales Invoice {si.name} for order {order_sn}")

        # Create Payment Entry if there's payment
        net = float(esc.get("escrow_amount") or esc.get("net_amount") or esc.get("payout_amount") or 0)
        if net > 0:
            _create_payment_entry(si, esc, net, order_sn)

    except Exception as e:
        frappe.log_error(f"Failed to process order {order_sn}: {str(e)}", "Shopee Order Processing")
        raise

def _create_or_get_customer(order_detail):
    """Create or get customer from order details."""
    buyer_username = order_detail.get("buyer_username") or f"buyer_{order_detail.get('buyer_user_id', 'unknown')}"
    customer_name = f"SHP-{buyer_username}"
    
    # Check if customer exists
    if frappe.db.exists("Customer", {"customer_name": customer_name}):
        return customer_name

    # Create new customer
    try:
        c = frappe.new_doc("Customer")
        c.customer_name = customer_name
        c.customer_group = _get_customer_group()
        c.customer_type = "Individual"
        c.territory = "All Territories"  # Default territory
        
        # Set customer primary contact if available
        recipient_address = order_detail.get("recipient_address", {})
        if recipient_address:
            c.customer_primary_contact = _create_customer_contact(customer_name, recipient_address)
        
        c.insert(ignore_permissions=True)
        return customer_name
        
    except Exception as e:
        frappe.log_error(f"Failed to create customer {customer_name}: {str(e)}", "Shopee Customer Creation")
        # Return the name anyway, customer might have been created by another process
        return customer_name

def _create_customer_contact(customer_name, address_data):
    """Create contact for customer."""
    try:
        contact = frappe.new_doc("Contact")
        contact.first_name = address_data.get("name", customer_name)
        contact.phone = address_data.get("phone", "")
        
        # Link to customer
        contact.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name
        })
        
        contact.insert(ignore_permissions=True)
        return contact.name
    except Exception as e:
        frappe.log_error(f"Failed to create contact for {customer_name}: {str(e)}", "Shopee Contact Creation")
        return None

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
    except Exception as e:
        frappe.log_error(f"Failed to upsert item {item_code}: {str(e)}", "Shopee Item Upsert")
        # fallback terakhir
        return _create_fallback_item(item_code, item_name, rate)

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

def _get_item_base_info(item_id: int):
    """Get base item information from Shopee."""
    s = _settings()
    res = _call(
        "/api/v2/product/get_item_base_info",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {"item_id_list": [int(item_id)]},
    )
    lst = (res.get("response") or {}).get("item_list", []) or []
    return lst[0] if lst and isinstance(lst, list) else {}

@frappe.whitelist()
def sync_items(hours: int = 720, status: str = "NORMAL"):
    """
    Enhanced item sync with better error handling.
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
    error_count = 0

    frappe.logger().info(f"Starting item sync: from {time_from} to {time_to}")

    # Ensure token is valid
    refresh_if_needed()

    try:
        while True:
            try:
                # Add small delay between API calls
                if processed_items > 0:
                    time.sleep(0.3)

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
                    error_msg = f"get_item_list error: {gl.get('error')} - {gl.get('message')}"
                    frappe.log_error(error_msg, "Shopee sync_items")
                    
                    # Handle token expiration
                    if "access token expired" in str(gl.get("message", "")).lower():
                        refresh_result = refresh_if_needed()
                        if refresh_result.get("status") == "refreshed":
                            continue  # Retry with new token
                    
                    return {"ok": False, "error": gl.get("error"), "message": gl.get("message")}

                resp = gl.get("response") or {}
                # Beberapa region: 'item' vs 'items', 'has_next_page' vs 'has_next'
                item_list = (resp.get("item") or resp.get("items") or [])
                if not isinstance(item_list, list):
                    item_list = []

                has_next = bool(resp.get("has_next_page") or resp.get("has_next") or False)

                for it in item_list:
                    try:
                        processed_items += 1
                        item_id = int(it.get("item_id"))
                        base = _get_item_base_info(item_id)
                        base_name = base.get("item_name") or f"Item {item_id}"

                        models = _get_models_for_item(item_id)

                        # Tanpa model → satu Item
                        if not models:
                            sku = str(base.get("item_sku") or item_id)
                            rate = float(base.get("normal_price") or 0)
                            
                            # Convert from micro units if needed
                            if rate > 1000000:
                                rate = rate / 100000
                            
                            before_exists = frappe.db.exists("Item", {"item_code": sku})
                            used_code = _upsert_item(
                                sku, base_name, defaults["item_group"], defaults["stock_uom"], rate
                            )
                            _upsert_price(used_code, defaults["price_list"], currency, rate)
                            
                            if not before_exists:
                                created += 1
                            else:
                                updated += 1
                            continue

                        # Ada model → satu Item per model
                        for m in models:
                            try:
                                model_sku = (m.get("model_sku") or "").strip()
                                sku = model_sku if model_sku else f"{item_id}-{m.get('model_id')}"
                                model_name = m.get("model_name") or ""
                                name = f"{base_name} - {model_name}" if model_name else base_name
                                rate = float(m.get("price") or m.get("original_price") or 0)

                                # Convert from micro units if needed
                                if rate > 1000000:
                                    rate = rate / 100000

                                before_exists = frappe.db.exists("Item", {"item_code": sku})
                                used_code = _upsert_item(
                                    sku, name, defaults["item_group"], defaults["stock_uom"], rate
                                )
                                _upsert_price(used_code, defaults["price_list"], currency, rate)
                                
                                if not before_exists:
                                    created += 1
                                else:
                                    updated += 1
                            except Exception as model_error:
                                error_count += 1
                                frappe.log_error(f"Failed to process model {m.get('model_id')} for item {item_id}: {str(model_error)}", "Shopee Model Processing")
                                continue

                    except Exception as item_error:
                        error_count += 1
                        frappe.log_error(f"Failed to process item {it.get('item_id')}: {str(item_error)}", "Shopee Item Processing")
                        continue

                if not has_next:
                    break
                offset = resp.get("next_offset", offset + page_size)

            except Exception as batch_error:
                error_count += 1
                frappe.log_error(f"Error processing item batch: {str(batch_error)}", "Shopee Item Sync")
                if error_count > 5:
                    break
                continue

        result = {
            "ok": True,
            "window": {"from": time_from, "to": time_to},
            "processed_items": processed_items,
            "created": created,
            "updated": updated,
            "errors": error_count
        }
        
        frappe.logger().info(f"Item sync completed: {result}")
        return result

    except Exception as e:
        frappe.log_error(f"Item sync failed: {str(e)}", "Shopee Item Sync Critical Error")
        return {
            "ok": False,
            "error": "critical_error",
            "message": str(e),
            "processed_items": processed_items,
            "created": created,
            "updated": updated,
            "errors": error_count
        }

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
        total_orders = frappe.db.count("Sales Invoice", {"shopee_order_sn": ["!=", ""]})
        
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