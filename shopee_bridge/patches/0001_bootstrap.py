# shopee_bridge/patches/0001_bootstrap.py
from __future__ import annotations

import json
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from datetime import datetime

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
    """Ensure Module Def exists and module path is available (with manual fallback).

    Jika get_module_path gagal tetapi folder scrubbed memang ada di app path, kita anggap OK
    (registry mungkin belum ter-refresh). Kalau tidak ada, log error untuk perhatian manual.
    """
    if not frappe.db.exists("Module Def", {"name": name}):
        doc = frappe.get_doc({"doctype": "Module Def", "module_name": name, "custom": 1})
        _sanitize_doc_strings(doc)
        doc.insert(ignore_permissions=True)
    try:
        frappe.clear_cache()
        frappe.get_module_path(name)
    except Exception:
        from frappe.utils import scrub
        import os
        try:
            app_path = frappe.get_app_path("shopee_bridge")
            candidate = os.path.join(app_path, scrub(name))
            if os.path.isdir(candidate):
                return
        except Exception:  # pragma: no cover
            pass
        frappe.log_error(
            f"Module path unresolved for '{name}'. Pastikan modules.txt & folder scrub '{scrub(name)}' ada.",
            "Shopee Bridge 0001 bootstrap"
        )

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
        # Full default shortcut set (idempotent later patches will just adjust if needed)
        ws.content = json.dumps([
            {"type": "shortcut", "label": "Shopee", "items": [
                {"label": "Shopee Settings", "type": "DocType", "link_to": "Shopee Settings"},
                {"label": "Webhook Inbox", "type": "DocType", "link_to": "List/Shopee Webhook Inbox"},
                {"label": "Sync Log", "type": "DocType", "link_to": "List/Shopee Sync Log"},
                {"label": "Customer Issues", "type": "DocType", "link_to": "List/Customer Issue"},
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

# ---------------- Consolidated extra logic from removed patches (0002-0009) -------------- #

def _workspace_json_shortcuts():
    """Ensure JSON content block contains desired shortcuts (idempotent)."""
    dt = "Workspace"
    if not frappe.db.exists(dt, "Shopee Bridge"):
        return
    ws = frappe.get_doc(dt, "Shopee Bridge")
    raw = ws.content or "[]"
    try:
        content = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        content = []
    if not isinstance(content, list):
        content = []
    group = None
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "shortcut" and blk.get("label") == "Shopee":
            group = blk; break
    if not group:
        group = {"type": "shortcut", "label": "Shopee", "items": []}
        content.append(group)
    items = group.setdefault("items", [])
    if not isinstance(items, list):
        group["items"] = items = []
    NEED = [
        ("Shopee Settings", "DocType", "Shopee Settings"),
        ("Webhook Inbox", "DocType", "List/Shopee Webhook Inbox"),
        ("Sync Log", "DocType", "List/Shopee Sync Log"),
        ("Customer Issues", "DocType", "List/Customer Issue"),
    ]
    changed = False
    by_label = {it.get("label"): it for it in items if isinstance(it, dict)}
    for label, typ, link in NEED:
        cur = by_label.get(label)
        if cur:
            if cur.get("type") != typ or cur.get("link_to") != link:
                cur["type"] = typ; cur["link_to"] = link; changed = True
        else:
            items.append({"label": label, "type": typ, "link_to": link}); changed = True
    if changed:
        ws.content = json.dumps(content)
        ws.save(ignore_permissions=True)
        frappe.db.commit()

def _ensure_sync_log_standard():
    """Ensure Shopee Sync Log DocType exists as standard (non-single) and migrated.

    Consolidates logic from patches 0007, 0008. Final desired state is standard table.
    """
    dt = "Shopee Sync Log"
    # If DocType doesn't exist yet, rely on JSON file & model sync; just attempt reload.
    if not frappe.db.exists("DocType", dt):
        try:
            frappe.reload_doc("shopee_bridge", "doctype", "shopee_sync_log")
        except Exception:
            return
    meta = frappe.get_meta(dt)
    if meta.issingle:
        # fetch previous single data
        try:
            single_vals = frappe.db.get_singles_dict(dt) or {}
        except Exception:
            single_vals = {}
        frappe.db.set_value("DocType", dt, "issingle", 0, update_modified=False)
        try:
            frappe.reload_doc("shopee_bridge", "doctype", "shopee_sync_log")
        except Exception:
            return
        try:
            doc = frappe.get_doc({
                "doctype": dt,
                "sync_type": single_vals.get("sync_type") or "orders",
                "status": single_vals.get("status") or "success",
                "job": single_vals.get("job") or "bootstrap",
                "key_ref": single_vals.get("key_ref") or "initial",
                "from_ts": single_vals.get("from_ts"),
                "to_ts": single_vals.get("to_ts"),
                "total": single_vals.get("total") or 0,
                "success": single_vals.get("success") or 0,
                "failed": single_vals.get("failed") or 0,
                "notes": single_vals.get("notes"),
                "payload_sample": single_vals.get("payload_sample"),
                "log_tail": single_vals.get("log_tail"),
                "last_updated": single_vals.get("last_updated"),
            })
            doc.insert(ignore_permissions=True)
        except Exception:  # pragma: no cover
            pass
        frappe.db.commit()

def _ensure_workspace_shortcuts_child():
    """Ensure child table shortcuts align (replaces logic from 0005,0006,0009)."""
    if not frappe.db.exists("Workspace", "Shopee Bridge"):
        return
    ws = frappe.get_doc("Workspace", "Shopee Bridge")
    TARGETS = [
        ("Shopee Settings", "Shopee Settings", "Form"),
        ("Webhook Inbox", "Shopee Webhook Inbox", "List"),
        ("Sync Log", "Shopee Sync Log", "List"),  # final list view after standardization
        ("Customer Issues", "Customer Issue", "List"),
    ]
    rows = {sc.get("label"): sc for sc in ws.get("shortcuts") or []}
    changed = False
    for label, doctype, view in TARGETS:
        # skip if doctype missing
        if not frappe.db.exists("DocType", doctype):
            continue
        cur = rows.get(label)
        if cur:
            if (cur.get("link_to") != doctype) or (cur.get("doc_view") != view):
                cur.link_to = doctype; cur.doc_view = view; changed = True
        else:
            r = ws.append("shortcuts", {})
            r.label = label; r.type = "DocType"; r.link_to = doctype; r.doc_view = view
            changed = True
    if changed:
        ws.save(ignore_permissions=True)
        frappe.db.commit()

# Extend execute to perform consolidation extras
old_execute = execute
def execute():  # type: ignore
    old_execute()
    try:
        _workspace_json_shortcuts()
        _ensure_sync_log_standard()
        _ensure_workspace_shortcuts_child()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge consolidated bootstrap follow-ups")
    print("[Shopee Bridge] Consolidated bootstrap (shortcuts + sync log) ensured.")
