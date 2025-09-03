from datetime import datetime, timedelta, timezone
import hmac
import hashlib
import base64
import time
import re
import frappe # pyright: ignore[reportMissingImports]
from zoneinfo import ZoneInfo
from frappe.utils import format_datetime, formatdate, convert_utc_to_system_timezone, nowdate, flt, cint # pyright: ignore[reportMissingImports]
from .dispatcher import call as _call
from .auth import _call, exchange_code, refresh_if_needed
from .finance import _get_default_cost_center_for_si
from .helpers import _compose_item_name, _fit140, _get_or_create_expense_account, _get_si_by_po, _get_so_by_po, _normalize_rate, _upsert_price, migrate_completed_orders_execute
from .orders import _process_order, _process_order_to_si, _process_order_to_so
from .helpers import _get_or_create_bank_account
from .webhook import _get_live_push_key

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


def _get_default_stock_uom() -> str:
    """Get default stock UOM, prefer Pcs over Nos."""
    # Priority order: Pcs > Unit > Nos > fallback
    preferred_uoms = ["Pcs", "Unit", "Nos"]

    for uom in preferred_uoms:
        if frappe.db.exists("UOM", uom):
            return uom

    # Ultimate fallback
    return "Pcs"


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


def _date_from_epoch(ts):
    """Return 'YYYY-MM-DD' dari epoch detik. Fallback: nowdate()."""
    try:
        return datetime.utcfromtimestamp(int(ts)).date().isoformat()
    except Exception:
        return frappe.utils.nowdate()


def _get_or_create_mode_of_payment(name: str) -> str:
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    mop_name = name if frappe.db.exists("Mode of Payment", name) else \
        frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": name}).insert(ignore_permissions=True).name

    # map akun ke company
    exists = frappe.db.exists("Mode of Payment Account", {"parent": mop_name, "company": company})
    if not exists:
        bank_acc = _get_or_create_bank_account("Shopee (Escrow)", "Bank")
        mop = frappe.get_doc("Mode of Payment", mop_name)
        row = mop.append("accounts", {})
        row.company = company
        row.default_account = bank_acc
        mop.save(ignore_permissions=True)
    return mop_name


def _create_or_get_customer(order_detail: dict, order_sn: str | None = None):
    """Create/get Customer dari detail order Shopee.
    Selalu gunakan buyer_username asli tanpa edit (kecuali yang di-mask '****').
    Suffix unik: 4 digit terakhir phone → 4 digit terakhir buyer_user_id → 6 char dari order_sn → "0000".
    Bisa dipanggil _create_or_get_customer(order_detail) saja.
    """
    order_sn = (order_sn or order_detail.get("order_sn") or "").strip()

    addr = order_detail.get("recipient_address") or {}
    phone = (addr.get("phone") or "").strip()

    # username bisa dimasking "****", maka abaikan
    buyer_username = (order_detail.get("buyer_username") or "").strip()
    if buyer_username == "****":
        buyer_username = ""

    buyer_user_id = str(order_detail.get("buyer_user_id") or "").strip()

    # Base name: HANYA buyer_username (tanpa edit) → buyer-id → fallback "buyer"
    if buyer_username:
        # Gunakan buyer_username asli tanpa cleaning/editing
        clean_name = buyer_username[:20]  # Hanya batasi panjang
    elif buyer_user_id:
        clean_name = f"buyer-{buyer_user_id}"
    else:
        clean_name = "buyer"

    # Suffix unik
    phone_digits = re.sub(r"\D", "", phone)
    if phone_digits:
        suffix = phone_digits[-4:]
    elif buyer_user_id:
        suffix = buyer_user_id[-4:]
    else:
        sn_clean = re.sub(r"[^A-Z0-9]", "", (order_sn or "").upper())
        suffix = sn_clean[:6] if len(sn_clean) >= 6 else (sn_clean.ljust(6, "0") if sn_clean else "0000")

    customer_name = f"SHP-{clean_name}"

    # Sudah ada? langsung pakai
    if frappe.db.exists("Customer", {"customer_name": customer_name}):
        return customer_name

    # Buat Customer baru
    customer = frappe.new_doc("Customer")
    customer.customer_name = customer_name
    customer.customer_group = "All Customer Groups"
    customer.customer_type = "Individual"
    customer.territory = "All Territories"
    customer.insert(ignore_permissions=True)

    # Buat Address kalau ada
    if addr and (addr.get("full_address") or addr.get("city")):
        try:
            address = frappe.new_doc("Address")
            address.address_title = customer_name
            address.address_type = "Shipping"
            address.address_line1 = (addr.get("full_address") or addr.get("city") or "")[:140]
            address.city = (addr.get("city") or addr.get("state") or "")[:140]
            address.country = addr.get("country") or "Indonesia"
            if phone:
                address.phone = phone
            address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
            address.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                f"Failed to create address for {customer_name}: {e}",
                "Customer Address Creation",
            )

    return customer_name


def _extract_dates_from_order(order_detail: dict, escrow_detail: dict | None = None) -> dict:
    """Unified date derivation for Shopee orders.

    Returns dict with keys:
      - transaction_date: payment date if available else create date else today
      - delivery_date: ship_by_date else create+days_to_ship else transaction_date
      - posting_date: payout_time (escrow) else pay_time else create_time else today
      - raw: original numeric timestamps for trace/debug
    """
    try:
        create_ts = _safe_int(order_detail.get("create_time"))
        pay_ts = _safe_int(order_detail.get("pay_time"))
        ship_by_ts = _safe_int(order_detail.get("ship_by_date"))
        days_to_ship = _safe_int(order_detail.get("days_to_ship"))

        payout_ts = 0
        if escrow_detail:
            payout_ts = _safe_int(
                escrow_detail.get("payout_time")
                or (escrow_detail.get("order_income") or {}).get("payout_time")
            )

        transaction_date = _date_from_epoch(pay_ts or create_ts or 0)

        if ship_by_ts:
            delivery_date = _date_from_epoch(ship_by_ts)
        elif create_ts and days_to_ship:
            try:
                delivery_date = (datetime.utcfromtimestamp(create_ts).date() + timedelta(days=days_to_ship)).isoformat()
            except Exception:
                delivery_date = transaction_date
        else:
            delivery_date = transaction_date

        posting_date = _date_from_epoch(payout_ts or pay_ts or create_ts or 0)

        return {
            "transaction_date": transaction_date,
            "delivery_date": delivery_date,
            "posting_date": posting_date,
            "raw": {
                "create_time": create_ts,
                "pay_time": pay_ts,
                "ship_by_date": ship_by_ts,
                "days_to_ship": days_to_ship,
                "payout_time": payout_ts,
            },
        }
    except Exception:
        today = nowdate()
        return {
            "transaction_date": today,
            "delivery_date": today,
            "posting_date": today,
            "raw": {},
        }


def _cfg_defaults():
    """Ambil default tanpa bergantung pada field yang mungkin tidak ada."""

    # Item Group - simple fallback
    item_group = _default_item_group()

    # Stock UOM - PERBAIKAN: prefer Pcs
    stock_uom = _get_default_stock_uom()  # ← Gunakan fungsi helper

    # Price List - cari yang selling=1
    price_list = None
    if frappe.db.exists("Price List", "Standard Selling"):
        price_list = "Standard Selling"
    else:
        price_lists = frappe.db.get_list("Price List",
            filters={"selling": 1, "enabled": 1},
            pluck="name", limit=1)
        price_list = price_lists[0] if price_lists else None

    # Warehouse & Company
    default_wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    company = frappe.db.get_single_value("Global Defaults", "default_company")

    return {
        "item_group": item_group,
        "stock_uom": stock_uom,
        "price_list": price_list,
        "default_warehouse": default_wh,
        "company": company,
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


def _upsert_item(item_code: str,
                 item_name: str,
                 item_group: str,
                 stock_uom: str,
                 standard_rate: float = 0.0,
                 meta: dict = None) -> str:
    """
    Buat/update Item master dengan fallback yang aman untuk semua field.
    - item_code & item_name dipotong 140
    - full name taruh di description (jika ada)
    - mapping shopee ke custom fields:
      custom_model_sku, custom_shopee_item_id, custom_shopee_model_id
    Return: item.name yang dipakai
    """
    meta = meta or {}
    code140 = _fit140(item_code)
    name140 = _fit140(item_name)

    # Gunakan fallback yang aman untuk item_group
    if not item_group:
        item_group = _default_item_group()

    # Pastikan item group ada
    if not frappe.db.exists("Item Group", item_group):
        try:
            ig = frappe.new_doc("Item Group")
            ig.item_group_name = item_group
            ig.parent_item_group = "All Item Groups"
            ig.is_group = 0
            ig.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(f"Failed to create item group {item_group}: {str(e)}", "Shopee Item Group")
            item_group = "All Item Groups"  # Ultimate fallback

    # Pastikan stock_uom ada
    if not stock_uom:
        stock_uom = _get_default_stock_uom()

    if frappe.db.exists("Item", code140):
        item = frappe.get_doc("Item", code140)
        if name140 and item.item_name != name140:
            item.item_name = name140
        if meta.get("description"):
            item.description = meta["description"]

        # Set custom fields dengan pengecekan apakah field ada
        for field_name, field_value in [
            ("custom_model_sku", meta.get("custom_model_sku", "")),
            ("custom_shopee_item_id", str(meta.get("custom_shopee_item_id", ""))),
            ("custom_shopee_model_id", str(meta.get("custom_shopee_model_id", "")))
        ]:
            try:
                if hasattr(item, field_name):
                    setattr(item, field_name, field_value)
            except Exception:
                pass

        try:
            if standard_rate and float(standard_rate) > 0:
                item.standard_rate = float(standard_rate)
        except Exception:
            pass

        item.save(ignore_permissions=True)
        frappe.db.commit()
        return item.name

    # Create new item
    item = frappe.new_doc("Item")
    item.item_code = code140
    item.item_name = name140
    item.item_group = item_group
    item.stock_uom = stock_uom
    item.is_stock_item = 1
    item.is_sales_item = 1
    item.maintain_stock = 1

    if meta.get("description"):
        item.description = meta["description"]

    # Set custom fields dengan pengecekan apakah field ada
    for field_name, field_value in [
        ("custom_model_sku", meta.get("custom_model_sku", "")),
        ("custom_shopee_item_id", str(meta.get("custom_shopee_item_id", ""))),
        ("custom_shopee_model_id", str(meta.get("custom_shopee_model_id", "")))
    ]:
        try:
            if hasattr(item, field_name):
                setattr(item, field_name, field_value)
        except Exception:
            pass

    try:
        if standard_rate and float(standard_rate) > 0:
            item.standard_rate = float(standard_rate)
    except Exception:
        pass

    item.insert(ignore_permissions=True)
    frappe.db.commit()
    return item.name


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


@frappe.whitelist()
def sync_items(hours: int = 720, status: str = "NORMAL"):
    """
    Sinkron Item Shopee -> ERPNext.
    - Code: model_sku (jika ada) else SHP-<item_id> / SHP-<item_id>-<model_id>
    - Name: "Judul Produk - Nama Varian" (max 140)
    - Mapping custom fields: custom_model_sku, custom_shopee_item_id, custom_shopee_model_id
    """
    import time

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
                        name_140 = _fit140(full_name or base_name or sku)
                        if not name_140:
                            name_140 = _fit140(sku)

                        rate = _normalize_rate(
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
                            full_name  = _compose_item_name(base_name, model_name)
                            name_140   = _fit140(full_name if full_name else sku)
                            rate       = _normalize_rate(m.get("price") or m.get("original_price"))

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


@frappe.whitelist()
def sync_orders_range(time_from: int, time_to: int, page_size: int = 50, order_status: str | None = None):
    """Backfill sync Shopee orders persis logika `sync_recent_orders` tapi memakai rentang waktu eksplisit.
    - READY_TO_SHIP  => buat Sales Order
    - COMPLETED      => buat Sales Invoice (+ Payment Entry jika belum ada)
    - CANCELLED/IN_CANCEL => batalkan SO/SI & buat Credit Note jika ada refund
    Optional filter order_status, fallback multi-status sama seperti recent sync.
    """
    s = _settings()
    if not getattr(s, "access_token", ""):
        frappe.throw("Access token required. Please authenticate with Shopee first.")

    # Validasi rentang
    if not time_from or not time_to:
        frappe.throw("time_from & time_to required")
    time_from = int(time_from)
    time_to = int(time_to)
    if time_from > time_to:
        frappe.throw("Invalid time range")
    MAX_SECONDS = 15 * 24 * 3600
    if (time_to - time_from) > MAX_SECONDS:
        frappe.throw("Time range cannot exceed 15 days")

    # Refresh token kalau perlu
    try:
        if callable(globals().get("refresh_if_needed")):
            refresh_if_needed()
    except Exception:
        pass

    # Human readable untuk log
    try:
        from datetime import datetime, timezone
        human_from = datetime.fromtimestamp(time_from, tz=timezone.utc).isoformat()
        human_to = datetime.fromtimestamp(time_to, tz=timezone.utc).isoformat()
    except Exception:
        human_from = str(time_from)
        human_to = str(time_to)

    frappe.logger().info(f"[Shopee Backfill] Window update_time: {time_from} → {time_to} ({human_from} → {human_to})")

    # Status list (samakan dengan sync_recent_orders). Only explicit CANCELLED should trigger cancel actions.
    BASE_STATUSES = ("READY_TO_SHIP", "COMPLETED", "CANCELLED")
    st_in = (order_status or "").strip().upper()
    if st_in and st_in not in ("ALL", "*"):
        STATUSES = (st_in,)
    else:
        STATUSES = BASE_STATUSES

    stats = {"SO": 0, "SI": 0, "PE": 0, "CANCELLED": 0, "errors": 0, "api_calls": 0}
    processed_order_sns: set[str] = set()
    highest_ut = int(getattr(s, "last_success_update_time", 0) or 0)

    def _pull(status: str, time_field: str) -> list[dict]:
        items, cursor = [], ""
        while True:
            params = {
                "time_range_field": time_field,
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
                error_msg = resp.get('message') or resp.get('error') or 'Unknown API error'
                # Check for rate limiting
                if 'rate limit' in error_msg.lower() or 'too many requests' in error_msg.lower():
                    frappe.logger().warning(f"[Shopee Backfill] Rate limited, sleeping for 60 seconds")
                    import time
                    time.sleep(60)
                    continue
                frappe.log_error(
                    f"get_order_list[{status},{time_field}] {resp.get('error')} - {error_msg}",
                    "Shopee Backfill"
                )
                raise Exception(error_msg)
            body = resp.get("response") or {}
            batch = body.get("order_list", []) or []
            items.extend(batch)
            if body.get("more"):
                cursor = body.get("next_cursor") or ""
                if not cursor:
                    break
            else:
                break
        frappe.logger().info(f"[Shopee Backfill] Pulled {len(items)} orders [{status}] with {time_field}")
        return items

    def _ensure_payment(order_sn: str):
        # Cari SI by custom_shopee_order_sn atau po_no
        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if not si_name:
            si_name = frappe.db.get_value("Sales Invoice", {"po_no": order_sn}, "name")
        # Jika tetap tidak ada, coba auto-build via complete_order_to_si (hanya jika SO ada)
        if not si_name:
            try:
                so_exists = frappe.db.exists("Sales Order", {"po_no": order_sn}) or \
                            frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
                if so_exists:
                    from .orders import complete_order_to_si  # local import to avoid circular at module import
                    rebuild = complete_order_to_si(order_sn)
                    if isinstance(rebuild, dict) and rebuild.get("sales_invoice"):
                        si_name = rebuild.get("sales_invoice")
                        stats["SI"] += 1
                        frappe.logger().info(f"[Shopee Backfill] Auto-created SI {si_name} during ensure_payment {order_sn}")
            except Exception as auto_e:
                frappe.logger().warning(f"[Shopee Backfill] Auto SI creation failed for {order_sn}: {auto_e}")
        if not si_name:
            return  # tidak bisa lanjut buat PE
        pe_exists = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si_name}
        )
        if pe_exists:
            return
        esc = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {"order_sn": order_sn}
        )
        stats["api_calls"] += 1
        if esc.get("error"):
            error_msg = esc.get('message') or esc.get('error') or 'Unknown escrow error'
            if 'rate limit' in error_msg.lower() or 'too many requests' in error_msg.lower():
                frappe.logger().warning(f"[Shopee Backfill] Escrow rate limited for {order_sn}, skipping payment creation")
                return
            frappe.logger().warning(f"[Shopee Backfill] escrow_detail fail {order_sn}: {error_msg}")
            return
        try:
            from .finance import _normalize_escrow_payload
            esc_n = _normalize_escrow_payload(esc)
            net_amount = flt(esc_n.get("net_amount"))
            from .finance import create_payment_entry_from_shopee
            pe_name = create_payment_entry_from_shopee(
                si_name=si_name,
                escrow=esc,
                net_amount=net_amount,
                order_sn=order_sn,
                posting_ts=esc_n.get("payout_time"),
                enqueue=False
            )
            if pe_name:
                stats["PE"] += 1
        except Exception as e:
            stats["errors"] += 1
            frappe.log_error(f"Ensure payment {order_sn} fail: {e}", "Shopee Backfill Payment")

    def _already_exists(order_sn: str) -> bool:
        if not order_sn:
            return False
        so_exists = frappe.db.exists("Sales Order", {"po_no": order_sn}) or \
                   frappe.db.exists("Sales Order", {"custom_shopee_order_sn": order_sn})
        si_exists = frappe.db.exists("Sales Invoice", {"po_no": order_sn}) or \
                   frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
        return bool(so_exists or si_exists)

    def _ensure_po_no_filled(doc, order_sn: str):
        try:
            if hasattr(doc, "po_no") and not getattr(doc, "po_no", None):
                doc.po_no = order_sn
            if hasattr(doc, "custom_shopee_order_sn") and not getattr(doc, "custom_shopee_order_sn", None):
                doc.custom_shopee_order_sn = order_sn
        except Exception:
            pass

    # Pass utama update_time
    for status in STATUSES:
        try:
            orders = _pull(status, "update_time")
        except Exception as e:
            stats["errors"] += 1
            frappe.logger().warning(f"[Shopee Backfill] Skip status {status} due to error: {e}")
            continue
        for o in orders:
            order_sn = o.get("order_sn")
            if not order_sn:
                continue
            try:
                processed_order_sns.add(order_sn)
                ut = int(o.get("update_time") or 0)
                if ut > highest_ut:
                    highest_ut = ut
                if _already_exists(order_sn):  # dedup
                    if status == "COMPLETED":
                        # Jika SI hilang (dihapus manual) tapi SO masih ada, buat ulang SI
                        si_missing = not (frappe.db.exists("Sales Invoice", {"po_no": order_sn}) or \
                                         frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}))
                        if si_missing:
                            try:
                                frappe.logger().info(f"[Shopee Backfill] Rebuilding missing SI for completed order {order_sn}")
                                res = _process_order_to_si(order_sn) or {}
                                if res.get("sales_invoice") or res.get("ok"):
                                    stats["SI"] += 1
                                    _ensure_payment(order_sn)
                                else:
                                    # fallback tetap coba ensure payment kalau ternyata SI tercipta oleh race condition
                                    _ensure_payment(order_sn)
                                continue
                            except Exception as ep2:
                                stats["errors"] += 1
                                frappe.log_error(f"Rebuild SI (dedup) {order_sn} fail: {ep2}", "Shopee Backfill SI Recreate")
                                # Lanjut ke ensure payment attempt walau gagal rebuild
                        # Pastikan PE jika SI ada tapi PE belum dibuat
                        try:
                            _ensure_payment(order_sn)
                        except Exception as ep:
                            stats["errors"] += 1
                            frappe.log_error(f"Ensure payment (dedup) {order_sn} fail: {ep}", "Shopee Backfill Payment")
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
                    # Escrow detail utk refund
                    esc = _call(
                        "/api/v2/payment/get_escrow_detail",
                        str(s.partner_id).strip(), s.partner_key,
                        s.shop_id, s.access_token,
                        {"order_sn": order_sn}
                    )
                    stats["api_calls"] += 1
                    if esc.get("error"):
                        error_msg = esc.get('message') or esc.get('error') or 'Unknown escrow error'
                        if 'rate limit' in error_msg.lower() or 'too many requests' in error_msg.lower():
                            frappe.logger().warning(f"[Shopee Backfill] Escrow rate limited for cancelled order {order_sn}, skipping refund processing")
                            continue
                        frappe.logger().warning(f"[Shopee Backfill] escrow_detail fail for cancelled {order_sn}: {error_msg}")
                        continue
                    from .finance import _normalize_escrow_payload
                    esc_n = _normalize_escrow_payload(esc)
                    refund_amount = flt(esc_n.get("refund_amount"))
                    so_name = _get_so_by_po(order_sn)
                    si_name = _get_si_by_po(order_sn)
                    if so_name:
                        try:
                            so = frappe.get_doc("Sales Order", so_name)
                            if so.docstatus == 1:
                                so.cancel()
                                stats["CANCELLED"] += 1
                        except Exception as e2:
                            stats["errors"] += 1
                            frappe.log_error(f"Cancel SO {so_name} error: {e2}")
                    if si_name:
                        si = frappe.get_doc("Sales Invoice", si_name)

                        # Jika ada Payment Entry yang terkait, coba batalkan dulu agar SI bisa dibatalkan
                        try:
                            pe_refs = frappe.get_all(
                                "Payment Entry Reference",
                                filters={"reference_doctype": "Sales Invoice", "reference_name": si_name},
                                fields=["parent"]
                            ) or []
                            for r in pe_refs:
                                pe_name = r.get("parent")
                                if not pe_name:
                                    continue
                                try:
                                    pe = frappe.get_doc("Payment Entry", pe_name)
                                    if getattr(pe, "docstatus", 0) == 1:
                                        pe.cancel()
                                        frappe.db.commit()
                                except Exception as e_pe:
                                    stats["errors"] += 1
                                    frappe.log_error(f"Cancel Payment Entry {pe_name} before cancelling SI {si_name} failed: {e_pe}")
                        except Exception:
                            # Non-fatal: lanjutkan ke pembuatan CN / cancel SI
                            pass

                        # Credit Note bila ada refund
                        if refund_amount > 0:
                            cn_exists = frappe.db.exists("Sales Invoice", {"return_against": si_name, "docstatus": 1})
                            if not cn_exists:
                                try:
                                    cn = frappe.new_doc("Sales Invoice")
                                    cn.customer = si.customer
                                    cn.posting_date = nowdate()
                                    cn.set_posting_time = 1
                                    cn.is_return = 1
                                    cn.return_against = si_name
                                    cn.currency = si.currency
                                    cn.update_stock = 0
                                    try:
                                        cn.custom_shopee_refund_sn = order_sn
                                    except Exception:
                                        pass
                                    base_po = f"{order_sn}-RET"
                                    cn.po_no = base_po if not frappe.db.exists("Sales Invoice", {"po_no": base_po}) else f"{base_po}-{frappe.utils.random_string(4)}"
                                    for item in si.items:
                                        cn_item = cn.append("items", {})
                                        cn_item.item_code = item.item_code
                                        cn_item.qty = -1 * flt(item.qty or 0)
                                        cn_item.rate = item.rate
                                        if item.warehouse:
                                            cn_item.warehouse = item.warehouse
                                    cn.insert(ignore_permissions=True)
                                    cn.submit()
                                    frappe.db.commit()
                                except Exception as e3:
                                    stats["errors"] += 1
                                    frappe.log_error(f"Create CN for {si_name} error: {e3}")

                        # Simpan tanda/field pada SI agar terlihat di UI bahwa Shopee sudah cancel (jika tidak bisa dibatalkan programmatically)
                        try:
                            if hasattr(si, "custom_shopee_refund_sn"):
                                si.custom_shopee_refund_sn = order_sn
                            # simpan perubahan kecil walau docstatus == 0
                            si.save(ignore_permissions=True)
                            frappe.db.commit()
                        except Exception:
                            pass

                        try:
                            if si.docstatus == 1:
                                si.cancel()
                                stats["CANCELLED"] += 1
                        except Exception as e4:
                            stats["errors"] += 1
                            frappe.log_error(f"Cancel SI {si_name} error: {e4}")
            except Exception as e:
                stats["errors"] += 1
                frappe.log_error(f"Process {order_sn} [{status}] fail: {e}", "Shopee Backfill Loop")
                # Add delay to prevent overwhelming the system
                import time
                time.sleep(0.1)

    # Fallback create_time jika tidak ada hasil & tidak ada error
    if stats["SO"] + stats["SI"] + stats["CANCELLED"] == 0 and stats["errors"] == 0:
        frappe.logger().info("[Shopee Backfill] No orders via update_time, retrying with create_time window...")
        for status in STATUSES:
            try:
                orders = _pull(status, "create_time")
            except Exception as e:
                stats["errors"] += 1
                frappe.logger().warning(f"[Shopee Backfill] (fallback) Skip status {status}: {e}")
                continue
            for o in orders:
                order_sn = o.get("order_sn")
                if not order_sn:
                    continue
                try:
                    processed_order_sns.add(order_sn)
                    ct = int(o.get("create_time") or 0)
                    if ct > highest_ut:
                        highest_ut = ct
                    if _already_exists(order_sn):
                        if status == "COMPLETED":
                            si_missing = not (frappe.db.exists("Sales Invoice", {"po_no": order_sn}) or \
                                             frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn}))
                            if si_missing:
                                try:
                                    frappe.logger().info(f"[Shopee Backfill] (fallback) Rebuilding missing SI for completed order {order_sn}")
                                    res = _process_order_to_si(order_sn) or {}
                                    if res.get("sales_invoice") or res.get("ok"):
                                        stats["SI"] += 1
                                        _ensure_payment(order_sn)
                                    else:
                                        _ensure_payment(order_sn)
                                    continue
                                except Exception as ep2:
                                    stats["errors"] += 1
                                    frappe.log_error(f"(fallback) Rebuild SI (dedup) {order_sn} fail: {ep2}", "Shopee Backfill SI Recreate")
                            try:
                                _ensure_payment(order_sn)
                            except Exception as ep:
                                stats["errors"] += 1
                                frappe.log_error(f"(fallback) Ensure payment (dedup) {order_sn} fail: {ep}", "Shopee Backfill Payment")
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
                        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
                        if so_name:
                            try:
                                so = frappe.get_doc("Sales Order", so_name)
                                if so.docstatus == 1:
                                    so.cancel()
                                    stats["CANCELLED"] += 1
                            except Exception as e2:
                                stats["errors"] += 1
                                frappe.log_error(f"(fallback) Cancel SO {so_name} error: {e2}")
                        if si_name:
                            try:
                                si = frappe.get_doc("Sales Invoice", si_name)
                                if si.docstatus == 1:
                                    si.cancel()
                                    stats["CANCELLED"] += 1
                            except Exception as e3:
                                stats["errors"] += 1
                                frappe.log_error(f"(fallback) Cancel SI {si_name} error: {e3}")
                except Exception as e:
                    stats["errors"] += 1
                    frappe.log_error(f"(fallback) Process {order_sn} [{status}] fail: {e}", "Shopee Backfill Loop")

    # Update watermark bila maju
    if highest_ut > (getattr(s, "last_success_update_time", 0) or 0):
        s.last_success_update_time = highest_ut
        try:
            s.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass

    total_processed = stats["SO"] + stats["SI"] + stats["CANCELLED"]
    frappe.logger().info(f"[Shopee Backfill] DONE processed={total_processed} errors={stats['errors']} api_calls={stats['api_calls']}")
    return {
        "from": time_from,
        "to": time_to,
        "processed_orders": total_processed,  # numeric untuk UI
        "processed": {"SO": stats["SO"], "SI": stats["SI"], "PE": stats["PE"], "CANCELLED": stats["CANCELLED"]},
        "errors": stats["errors"],
        "last_update_time": highest_ut,
        "api_calls": stats["api_calls"],
        "success": (total_processed > 0 and stats["errors"] == 0) or (stats["errors"] == 0),
        "window": {"from_iso": human_from, "to_iso": human_to},
        "statuses": list(STATUSES),
        "range_mode": True,
        "order_sns": list(processed_order_sns),  # untuk dedup akurat di wrapper
    }


@frappe.whitelist()
def migrate_orders_from(start_timestamp=None, year=None,
                        chunk_days=10, page_size=50,
                        order_status=None):
    """Backfill / migrasi orders Shopee dari titik waktu (default 1 Jan tahun berjalan) sampai sekarang.
       Window per chunk <=15 hari memanggil sync_orders_range. Dedup 100% via set order_sns.
    """
    try:
        # Defensive coercion: frappe.call sering kirim semua arg sebagai string
        def _to_int_or_none(v):
            if v in (None, "", "null", "None"):  # treat empty as None
                return None
            try:
                return int(v)  # works for str or int
            except Exception:
                return None

        # Coerce numeric params
        start_timestamp = _to_int_or_none(start_timestamp)
        year = _to_int_or_none(year)
        try:
            chunk_days = int(chunk_days)
        except Exception:
            chunk_days = 10
        try:
            page_size = int(page_size)
        except Exception:
            page_size = 50

        s = _settings()
        if not getattr(s, "access_token", ""):
            frappe.throw("Access token required. Please authenticate with Shopee first.")

        if chunk_days <= 0:
            frappe.throw("chunk_days must be > 0")
        if chunk_days > 15:
            frappe.throw("chunk_days cannot exceed 15 (Shopee API limit)")

        try:
            if callable(globals().get("refresh_if_needed")):
                refresh_if_needed()
        except Exception:
            pass

        from datetime import datetime, timezone
        import time as _t

        now_ts = int(_t.time())
        if start_timestamp is not None:
            start_ts = int(start_timestamp)
            start_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        else:
            if year is None:
                year = datetime.fromtimestamp(now_ts, tz=timezone.utc).year
            start_dt = datetime(year, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            start_ts = int(start_dt.timestamp())
        if start_ts > now_ts:
            frappe.throw("Start time is in the future")

        chunk_sec = int(chunk_days * 24 * 3600)

        windows: list[dict] = []
        agg_processed = {"SO": 0, "SI": 0, "PE": 0, "CANCELLED": 0}
        agg_errors = 0
        agg_api_calls = 0
        total_orders = 0
        all_order_sns: set[str] = set()
        last_update_time = int(getattr(s, "last_success_update_time", 0) or 0)

        cur_start = start_ts
        while cur_start <= now_ts:
            cur_end = min(cur_start + chunk_sec - 1, now_ts)
            try:
                res = sync_orders_range(
                    time_from=cur_start,
                    time_to=cur_end,
                    page_size=page_size,
                    order_status=order_status,
                ) or {}
                processed_break = res.get("processed") or {}
                for k in agg_processed.keys():
                    agg_processed[k] += int(processed_break.get(k, 0) or 0)
                agg_errors += int(res.get("errors", 0) or 0)
                agg_api_calls += int(res.get("api_calls", 0) or 0)
                total_orders += int(res.get("processed_orders", 0) or 0)
                for osn in (res.get("order_sns") or []):
                    if osn:
                        all_order_sns.add(osn)
                lut = int(res.get("last_update_time", 0) or 0)
                if lut > last_update_time:
                    last_update_time = lut
                windows.append({
                    "from": cur_start,
                    "to": cur_end,
                    "processed_orders": res.get("processed_orders"),
                    "errors": res.get("errors"),
                    "api_calls": res.get("api_calls"),
                })
            except Exception as e:
                agg_errors += 1
                frappe.log_error(f"Window {cur_start}->{cur_end} failed: {e}", "Shopee Migrate From")
                windows.append({"from": cur_start, "to": cur_end, "error": str(e)})
            cur_start = cur_end + 1

        result = {
            "year": year,
            "from": start_ts,
            "to": now_ts,
            "from_iso": start_dt.isoformat(),
            "to_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            "chunk_days": chunk_days,
            "windows": windows,
            "processed_total": len(all_order_sns),
            "raw_processed_total": total_orders,
            "processed_breakdown": agg_processed,
            "errors": agg_errors,
            "api_calls": agg_api_calls,
            "last_update_time": last_update_time,
            "success": agg_errors == 0,
            "range_mode": True,
            "migrate_from": True,
            "order_status_filter": order_status,
            "start_timestamp_param": start_timestamp,
            "unique_order_sns": len(all_order_sns),
        }
        frappe.logger().info(f"[Shopee Migrate From] DONE start={start_ts} windows={len(windows)} "
                             f"processed_raw={total_orders} unique={len(all_order_sns)} errors={agg_errors}")
        return result
    except Exception as e:
        frappe.log_error(f"migrate_orders_from failed: {str(e)}", "Shopee Migrate From")
        return {"success": False, "error": str(e)}


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

            from .utils import _insert_submit_with_retry
            _insert_submit_with_retry(new_pe)
            fixed += 1

        except Exception as e:
            frappe.log_error(f"Repair PE for {si_row.name} failed: {e}", "Shopee Repair PE")
            errors += 1

    return {"fixed": fixed, "skipped": skipped, "errors": errors}


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


# ===== Helper: Create Credit Note from existing SI (idempotent) =====
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