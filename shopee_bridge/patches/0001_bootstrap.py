# shopee_bridge/patches/0001_bootstrap.py
from __future__ import annotations

import json
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

TEXTY = {
    "Data", "Small Text", "Text", "Long Text", "Text Editor",
    "Markdown Editor", "HTML Editor", "Link", "Select", "Code", "JSON",
}

def _has_field(dt: str, field: str) -> bool:
    try:
        return any(df.fieldname == field for df in frappe.get_meta(dt).fields)
    except Exception:
        return False

def _sanitize_doc_strings(doc) -> None:
    meta = frappe.get_meta(doc.doctype)
    for df in meta.fields:
        if df.fieldtype in TEXTY and doc.get(df.fieldname) is None:
            doc.set(df.fieldname, "[]" if df.fieldtype == "JSON" else "")
        elif df.fieldtype in ("Table", "Table MultiSelect"):
            rows = doc.get(df.fieldname) or []
            try:
                child_meta = frappe.get_meta(df.options)
            except Exception:
                child_meta = None
            if not child_meta:
                continue
            for row in rows:
                for cdf in child_meta.fields:
                    if cdf.fieldtype in TEXTY and row.get(cdf.fieldname) is None:
                        row.set(cdf.fieldname, "[]" if cdf.fieldtype == "JSON" else "")

def _ensure_module_def(name: str) -> None:
    if not frappe.db.exists("Module Def", {"name": name}):
        doc = frappe.get_doc({"doctype": "Module Def", "module_name": name, "custom": 1})
        _sanitize_doc_strings(doc)
        doc.insert(ignore_permissions=True)

def _ensure_workspace(module_name: str, ws_name: str, seq: int = 998) -> None:
    # Workspace schema berubah antar versi → reload jika ada
    try:
        frappe.reload_doc("desk", "doctype", "workspace")
    except Exception:
        pass

    dt = "Workspace"
    ws = frappe.get_doc(dt, ws_name) if frappe.db.exists(dt, ws_name) else frappe.new_doc(dt)
    if not ws.get("name"):
        ws.name = ws_name
        ws.flags.name_set = True

    def set_if(field: str, value):
        if _has_field(dt, field):
            cur = ws.get(field)
            if cur is None or (isinstance(cur, str) and not cur.strip()):
                ws.set(field, value)

    set_if("title", module_name or ws_name)
    set_if("label", module_name or ws_name)
    if _has_field(dt, "module"):
        ws.module = module_name
    if _has_field(dt, "public"):
        ws.public = 1
    if _has_field(dt, "is_hidden"):
        ws.is_hidden = 0
    if _has_field(dt, "description") and ws.get("description") is None:
        ws.description = ""
    if _has_field(dt, "icon") and ws.get("icon") is None:
        ws.icon = ""

    # urutan (pakai field yang tersedia)
    if _has_field(dt, "sequence_id"):
        ws.sequence_id = seq
    if _has_field(dt, "sequence"):
        ws.sequence = seq

    # content harus STRING JSON (bukan list)
    if _has_field(dt, "content"):
        ws.content = json.dumps([
            {"type": "shortcut", "label": "Shopee", "items": [
                {"label": "Shopee Settings", "type": "DocType", "link_to": "DocType/Shopee Settings"}
            ]}
        ])

    _sanitize_doc_strings(ws)
    ws.flags.ignore_mandatory = True
    ws.save(ignore_permissions=True)

def _ensure_single_settings():
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
        _sanitize_doc_strings(doc)
        doc.insert(ignore_permissions=True)

def execute():
    """Bootstrap Shopee Bridge (sekali jalan, idempotent)."""
    # 0) (opsional) bersihkan workspace JSON/fixtures lama yang berpotensi konflik
    #    → sengaja tidak menghapus file; patch ini hanya membuat dokumen yang aman.

    # 1) Custom Fields minimum yang dibutuhkan
    fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data",
                 insert_after="title", unique=1, reqd=0, in_standard_filter=1),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data",
                 insert_after="shopee_order_sn"),
            dict(fieldname="buyer_username", label="Shopee Buyer Username", fieldtype="Data",
                 insert_after="buyer_user_id"),
            dict(fieldname="shopee_sync_hash", label="Shopee Sync Hash", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Sales Invoice": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1,
                 in_standard_filter=1),
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
    try:
        create_custom_fields(fields, ignore_validate=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge create_custom_fields")

    # 2) Module & Workspace
    try:
        _ensure_module_def("Shopee Bridge")
        _ensure_workspace("Shopee Bridge", "Shopee Bridge", seq=998)  # adjust bila mau posisi lain
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge ensure workspace")

    # 3) Single Settings
    try:
        _ensure_single_settings()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge seed Shopee Settings")

    frappe.db.commit()
    print("[Shopee Bridge] Patch bootstrap complete.")
