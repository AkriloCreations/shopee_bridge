# shopee_bridge/setup/install.py

from frappe import _
import frappe
import json
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Field types yang wajib string/JSON (bukan None)
TEXTY = {
    "Data", "Small Text", "Text", "Long Text", "Text Editor",
    "Markdown Editor", "HTML Editor", "Link", "Select", "Code", "JSON"
}


def has_field(doctype: str, fieldname: str) -> bool:
    """Cek apakah sebuah field ada pada skema doctype (lintas-versi)."""
    return any(df.fieldname == fieldname for df in frappe.get_meta(doctype).fields)


def sanitize_doc_strings(doc) -> bool:
    """Pastikan SEMUA field teks/JSON (termasuk child) tidak None."""
    meta = frappe.get_meta(doc.doctype)
    changed = False

    # parent fields
    for df in meta.fields:
        if df.fieldtype in TEXTY:
            val = getattr(doc, df.fieldname, None)
            if val is None:
                setattr(doc, df.fieldname, "[]" if df.fieldtype == "JSON" else "")
                changed = True
        elif df.fieldtype in ("Table", "Table MultiSelect"):
            rows = doc.get(df.fieldname) or []
            if not rows:
                continue
            child_meta = frappe.get_meta(df.options)
            for row in rows:
                for cdf in child_meta.fields:
                    if cdf.fieldtype in TEXTY and row.get(cdf.fieldname) is None:
                        row.set(cdf.fieldname, "[]" if cdf.fieldtype == "JSON" else "")
                        changed = True

    return changed


def ensure_module_def(mod_name: str):
    """Pastikan Module Def ada (dipakai Workspace/Desk)."""
    if not frappe.db.exists("Module Def", {"name": mod_name}):
        frappe.get_doc({
            "doctype": "Module Def",
            "module_name": mod_name,
            "custom": 1
        }).insert(ignore_permissions=True)


def ensure_workspace(mod_name: str, ws_name: str):
    """Buat/perbarui Workspace secara aman lintas-versi."""
    dt = "Workspace"
    ws = frappe.get_doc(dt, ws_name) if frappe.db.exists(dt, ws_name) else frappe.new_doc(dt)
    if not ws.get("name"):
        ws.name = ws_name
        ws.flags.name_set = True

    # Versi terbaru mewajibkan 'title'; beberapa versi pakai 'label'
    if has_field(dt, "title") and not (ws.get("title") or "").strip():
        ws.title = mod_name or ws_name
    if has_field(dt, "label") and not (ws.get("label") or "").strip():
        ws.label = mod_name or ws_name

    if has_field(dt, "module"):
        ws.module = mod_name
    if has_field(dt, "public"):
        ws.public = 1
    if has_field(dt, "is_hidden"):
        ws.is_hidden = 0
    if has_field(dt, "description") and ws.get("description") is None:
        ws.description = ""
    if has_field(dt, "icon") and ws.get("icon") is None:
        ws.icon = ""

    # Konten minimal (shortcut) — hanya jika field ada
    if has_field(dt, "content"):
        ws.content = json.dumps([
            {"type": "shortcut", "label": "Shopee", "items": [
                {"label": "Shopee Settings", "type": "DocType", "link_to": "DocType/Shopee Settings"}
            ]}
        ])

    # Guard terakhir: tidak ada field teks/JSON yang None
    sanitize_doc_strings(ws)
    ws.save(ignore_permissions=True)


def after_install():
    """Idempotent post-install: create custom fields, ensure module/workspace, seed empty Shopee Settings.

    Requirements (per user request):
    - Use the exact custom fields spec from patch add_custom_fields.execute() plus buyer_username for Sales Order.
    - Ignore duplicates / safely retryable.
    - Optionally create single doctype record Shopee Settings with blank token fields if missing.
    - Print clear completion message.
    """

    # Copy from patches/add_custom_fields.py (kept aligned) + buyer_username insertion.
    fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", insert_after="title", unique=1, reqd=0, in_standard_filter=1),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data", insert_after="shopee_order_sn"),
            dict(fieldname="buyer_username", label="Shopee Buyer Username", fieldtype="Data", insert_after="buyer_user_id"),
            dict(fieldname="shopee_sync_hash", label="Shopee Sync Hash", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Sales Invoice": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1, in_standard_filter=1),
            dict(fieldname="escrow_synced", label="Shopee Escrow Synced", fieldtype="Check", default=0),
            dict(fieldname="escrow_synced_at", label="Shopee Escrow Synced At", fieldtype="Datetime"),
            dict(fieldname="escrow_fee_total", label="Shopee Fee Total", fieldtype="Currency"),
            dict(fieldname="escrow_net", label="Shopee Net Payout", fieldtype="Currency"),
            dict(fieldname="payout_batch_id", label="Shopee Payout Batch ID", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Delivery Note": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="package_number", label="Shopee Package Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="tracking_number", label="Shopee Tracking Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="status_pickup", label="Shopee Pickup Status", fieldtype="Data"),
            dict(fieldname="status_delivery", label="Shopee Delivery Status", fieldtype="Data"),
            dict(fieldname="delivered_at", label="Shopee Delivered At", fieldtype="Datetime"),
        ],
    }

    mod = "Shopee Bridge"

    try:
        # 1. Custom fields (ignore validation to avoid break on existing)
        try:
            create_custom_fields(fields, ignore_validate=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge create_custom_fields")

        # 2. Ensure Module & Workspace (for Desk visibility)
        try:
            ensure_module_def(mod)
            ensure_workspace(mod, mod)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge ensure workspace")

        # 3. Seed single Shopee Settings doc with empty token fields if not existing
        try:
            if not frappe.db.exists("Shopee Settings"):
                doc = frappe.get_doc({
                    "doctype": "Shopee Settings",
                    "partner_id": 0,
                    "partner_key": "",
                    "region": "",
                    "redirect_url": "",
                    "access_token": "",
                    "refresh_token": "",
                    "token_expires_at": None,
                })
                sanitize_doc_strings(doc)
                doc.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge seed Shopee Settings")

        frappe.db.commit()
        print("[Shopee Bridge] Install complete: custom fields, workspace & defaults ensured.")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge after_install fatal")
        print("[Shopee Bridge] Install encountered errors. Check Error Log.")
        raise
