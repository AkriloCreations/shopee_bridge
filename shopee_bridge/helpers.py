import time
import frappe # pyright: ignore[reportMissingImports]
from frappe.utils import cint # pyright: ignore[reportMissingImports]
from .dispatcher import call as _call
from .auth import refresh_if_needed
from .orders import _process_order_to_si
from .utils import _settings, sync_items  # pyright: ignore[reportMissingImports]


def _fit140(s: str) -> str:
    """Truncate string to 140 characters."""
    return ((s or "").strip())[:140]


def _compose_item_name(base_name: str, model_name: str = None) -> str:
    """Compose item name from base and model names."""
    base = (base_name or "").strip()
    mdl = (model_name or "").strip()
    if base and mdl:
        return f"{base} - {mdl}"
    return base or mdl or ""


def _normalize_rate(x) -> float:
    """Normalize rate value, handling micro units."""
    try:
        v = float(x or 0)
        # Shopee kadang kirim micro units untuk sebagian region
        if v > 1_000_000:
            v = v / 100000
        return v
    except Exception:
        return 0.0


def _get_so_by_po(order_sn: str) -> str | None:
    return frappe.db.get_value("Sales Order", {"po_no": order_sn}, "name") # pyright: ignore[reportUndefinedVariable]


def _get_si_by_po(order_sn: str) -> str | None:
    return frappe.db.get_value("Sales Invoice", {"po_no": order_sn}, "name") # pyright: ignore[reportUndefinedVariable]


def _handle_api_error(error_response: dict, operation: str, order_sn: str = "") -> bool:
    """Handle API errors and return True if should retry, False if should skip."""
    if not error_response.get("error"):
        return False

    error_msg = error_response.get('message') or error_response.get('error') or 'Unknown API error'

    # Check for rate limiting
    if 'rate limit' in error_msg.lower() or 'too many requests' in error_msg.lower():
        frappe.logger().warning(f"[Shopee API] Rate limited on {operation} for {order_sn or 'N/A'}, sleeping for 60 seconds") # pyright: ignore[reportUndefinedVariable]
        import time
        time.sleep(60)
        return True  # Should retry

    # Check for authentication errors
    if 'invalid token' in error_msg.lower() or 'unauthorized' in error_msg.lower():
        frappe.logger().error(f"[Shopee API] Authentication error on {operation}: {error_msg}") # pyright: ignore[reportUndefinedVariable]
        return False  # Don't retry

    # Log other errors
    frappe.log_error(f"[Shopee API] {operation} failed for {order_sn or 'N/A'}: {error_msg}", "Shopee API Error")
    return False  # Don't retry for other errors
    """Cari Sales Order existing: prioritas po_no (Customer's PO), fallback custom_shopee_order_sn."""
    if not order_sn:
        return None
    so = frappe.db.get_value("Sales Order", {"po_no": order_sn}, "name")
    if so:
        return so
    so_custom = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
    if so_custom:
        return so_custom
    so_purchase = frappe.db.get_value("Sales Order", {"purchase_order_number": order_sn}, "name")
    if so_purchase:
        return so_purchase
    return None


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
            frappe.db.commit() # pyright: ignore[reportUndefinedVariable]

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
    so_rows = frappe.get_all( # pyright: ignore[reportUndefinedVariable]
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
                    error_msg = str(e)[:50] + "..." if len(str(e)) > 50 else str(e)
                    frappe.log_error(f"Process order {order_sn}: {error_msg}", f"Order {order_sn[:8]}")
                    frappe.logger().warning(f"✗ Exception {order_sn}: {error_msg}")

                # Throttle
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
            offset = response.get("next_offset", offset + batch_size)
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