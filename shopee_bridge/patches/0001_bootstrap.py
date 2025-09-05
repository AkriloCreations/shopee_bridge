# shopee_bridge/patches/0001_bootstrap.py
from __future__ import annotations

import json
import os
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import scrub
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
    """Ensure Module Def exists properly for ERPNext module registration."""
    
    # Check if Module Def already exists
    if not frappe.db.exists("Module Def", name):
        print(f"Creating Module Def for '{name}'...")
        doc = frappe.get_doc({
            "doctype": "Module Def", 
            "name": name,
            "module_name": name,
            "custom": 1
        })
        _sanitize_doc_strings(doc)
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        print(f"Module Def '{name}' created successfully")
    else:
        print(f"Module Def '{name}' already exists")
    
    # Ensure module folder exists at correct path
    try:
        app_path = frappe.get_app_path("shopee_bridge")
        scrubbed_name = scrub(name)  # "Shopee Bridge" -> "shopee_bridge" 
        module_path = os.path.join(app_path, scrubbed_name)
        
        if not os.path.isdir(module_path):
            print(f"ERROR: Module folder missing at {module_path}")
            return
            
        # Clear cache to reload modules
        frappe.clear_cache()
        
        # Test module path resolution
        resolved_path = frappe.get_module_path(name)
        print(f"Module path resolved: {resolved_path}")
        
    except Exception as e:
        print(f"Module path resolution failed: {e}")
        frappe.log_error(
            f"Module path error for '{name}': {str(e)}",
            "Shopee Bridge Module Setup"
        )

def _ensure_workspace(module_name: str, ws_name: str, seq: int = 998) -> None:
    """Ensure workspace exists with proper shortcuts."""
    print(f"Setting up workspace: {ws_name}")
    
    # Reload workspace doctype to handle schema changes
    try:
        frappe.reload_doc("desk", "doctype", "workspace")
    except Exception:
        pass

    dt = "Workspace"
    
    # Get existing workspace or create new one
    if frappe.db.exists(dt, ws_name):
        ws = frappe.get_doc(dt, ws_name)
        print(f"Found existing workspace: {ws_name}")
    else:
        ws = frappe.new_doc(dt)
        ws.name = ws_name
        ws.flags.name_set = True
        print(f"Creating new workspace: {ws_name}")

    # Set basic workspace properties
    ws.title = module_name or ws_name
    ws.label = module_name or ws_name
    ws.module = module_name
    ws.public = 1
    ws.is_hidden = 0
    ws.sequence_id = seq
    
    # Ensure description and icon fields are set
    if not ws.get("description"):
        ws.description = ""
    if not ws.get("icon"):
        ws.icon = ""

    # Set content with shortcuts (JSON string format)
    shortcuts_content = [
        {"type": "shortcut", "label": "Shopee", "items": [
            {"label": "Shopee Settings", "type": "DocType", "link_to": "Shopee Settings"},
            {"label": "Webhook Inbox", "type": "DocType", "link_to": "List/Shopee Webhook Inbox"},
            {"label": "Customer Issues", "type": "DocType", "link_to": "List/Customer Issue"},
        ]}
    ]
    ws.content = json.dumps(shortcuts_content)
    print(f"Set workspace content: {ws.content}")

    # Clear any existing shortcuts and add new ones
    ws.shortcuts = []
    
    # Add shortcuts to the child table
    shortcuts_to_add = [
        ("Shopee Settings", "Shopee Settings", "Form"),
        ("Webhook Inbox", "Shopee Webhook Inbox", "List"),
        ("Customer Issues", "Customer Issue", "List"),
    ]
    
    for label, doctype, view in shortcuts_to_add:
        # Only add if the DocType exists
        if frappe.db.exists("DocType", doctype):
            shortcut = ws.append("shortcuts", {})
            shortcut.label = label
            shortcut.type = "DocType"
            shortcut.link_to = doctype
            shortcut.doc_view = view
            shortcut.color = "Grey"
            print(f"Added shortcut: {label} -> {doctype}")
        else:
            print(f"Skipped shortcut {label}: DocType {doctype} not found")

    # Save the workspace
    _sanitize_doc_strings(ws)
    ws.flags.ignore_mandatory = True
    ws.flags.ignore_permissions = True
    
    try:
        ws.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"Workspace '{ws_name}' saved successfully with {len(ws.shortcuts)} shortcuts")
    except Exception as e:
        print(f"Error saving workspace: {e}")
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge Workspace Save Error")

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

# ---------------- Consolidated extra logic from removed patches (0002-0009) -------------- #

def _workspace_json_shortcuts():
    """Ensure JSON content block contains desired shortcuts (idempotent) - REMOVED Sync Log."""
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
    
    # REMOVED Sync Log from NEED array
    NEED = [
        ("Shopee Settings", "DocType", "Shopee Settings"),
        ("Webhook Inbox", "DocType", "List/Shopee Webhook Inbox"),
        ("Customer Issues", "DocType", "List/Customer Issue"),
    ]
    
    # Remove Sync Log if it exists
    items[:] = [it for it in items if isinstance(it, dict) and it.get("label") != "Sync Log"]
    
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

def _ensure_workspace_shortcuts_child():
    """Ensure child table shortcuts align (replaces logic from 0005,0006,0009) - REMOVED Sync Log."""
    if not frappe.db.exists("Workspace", "Shopee Bridge"):
        return
    ws = frappe.get_doc("Workspace", "Shopee Bridge")
    
    # REMOVED Sync Log from TARGETS
    TARGETS = [
        ("Shopee Settings", "Shopee Settings", "Form"),
        ("Webhook Inbox", "Shopee Webhook Inbox", "List"),
        ("Customer Issues", "Customer Issue", "List"),
    ]
    
    # Remove existing Sync Log shortcuts if they exist
    shortcuts = ws.get("shortcuts") or []
    ws.set("shortcuts", [sc for sc in shortcuts if sc.get("label") != "Sync Log"])
    
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

def _ensure_workspace_shortcut():
    """Ensure Shopee Settings workspace shortcut exists."""
    if not frappe.db.exists("Workspace Shortcut", {"link_to": "Shopee Settings"}):
        frappe.get_doc({
            "doctype": "Workspace Shortcut",
            "type": "DocType",
            "link_to": "Shopee Settings",
            "label": "Shopee Settings",
        }).insert(ignore_permissions=True)

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

    # 2) Module Definition - CRITICAL FIRST
    print("Step 2: Setting up Module Definition...")
    try:
        _ensure_module_def("Shopee Bridge")
    except Exception as e:
        print(f"ERROR in module definition: {e}")
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge Module Setup")

    # 3) Single Settings - Before Workspace (needed for shortcuts)
    print("Step 3: Setting up Shopee Settings...")
    try:
        _ensure_single_settings()
    except Exception as e:
        print(f"ERROR in settings setup: {e}")
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge Settings Setup")

    # 4) Workspace - After Module Def and Settings exist
    print("Step 4: Setting up Workspace...")
    try:
        _ensure_workspace("Shopee Bridge", "Shopee Bridge", seq=998)
    except Exception as e:
        print(f"ERROR in workspace setup: {e}")
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge Workspace Setup")

    # 5) Additional workspace shortcuts (legacy compatibility)  
    print("Step 5: Ensuring additional shortcuts...")
    try:
        _workspace_json_shortcuts()
        _ensure_workspace_shortcuts_child()
        _ensure_workspace_shortcut()
    except Exception as e:
        print(f"ERROR in additional shortcuts: {e}")
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge Additional Shortcuts")

    # Final commit and success message
    frappe.db.commit()
    print("=" * 60)
    print("[Shopee Bridge] Bootstrap patch completed successfully!")
    print(f"- Module 'Shopee Bridge' registered")
    print(f"- Workspace created with shortcuts")
    print(f"- Custom fields added to Sales Order, Sales Invoice, Delivery Note")
    print(f"- Shopee Settings initialized")
    print("=" * 60)