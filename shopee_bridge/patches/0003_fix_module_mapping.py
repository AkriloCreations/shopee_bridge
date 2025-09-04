"""Repair patch for 'Module Shopee Bridge not found'.

Actions (idempotent):
 - Ensure Module Def 'Shopee Bridge' exists (custom=0 if already there)
 - Update modules.txt if missing name line
 - For each target DocType, force module field to 'Shopee Bridge'
 - Clear local module cache & reload doctypes
 - Re-sync workspace if exists
"""
from __future__ import annotations

import frappe, os, json
from frappe.modules import get_module_path

MODULE = "Shopee Bridge"
DOCTYPES = [
    "Shopee Settings",
    "Shopee Webhook Inbox",
    "Shopee Sync Log",
    "Customer Issue",  # optional/custom
]


def _ensure_module_def():
    if frappe.db.exists("Module Def", MODULE):
        md = frappe.get_doc("Module Def", MODULE)
        if getattr(md, "custom", 0):
            # make it standard so framework treats it as core
            md.custom = 0
            md.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Module Def",
            "module_name": MODULE,
            "custom": 0,
        }).insert(ignore_permissions=True)


def _fix_modules_txt():
    try:
        mod_path = get_module_path("shopee_bridge")
        mt_path = os.path.join(mod_path, "modules.txt")
        if os.path.exists(mt_path):
            with open(mt_path, "r", encoding="utf-8") as fh:
                lines = [l.rstrip("\n") for l in fh]
            if MODULE not in [l.strip() for l in lines if l.strip()]:
                lines.append(MODULE)
                with open(mt_path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join([l for l in lines if l.strip()]) + "\n")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge patch: fix modules.txt")


def _reassign_doctypes():
    for dn in DOCTYPES:
        if not frappe.db.exists("DocType", dn):
            continue
        try:
            dt = frappe.get_doc("DocType", dn)
            if dt.module != MODULE:
                dt.module = MODULE
                dt.save(ignore_permissions=True)
            # ensure not custom if shipping with app
            if dn.startswith("Shopee") and getattr(dt, "custom", 0):
                dt.custom = 0
                dt.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(f"Failed to repair module link for {dn}", "Shopee Bridge patch")


def _reload():
    for dn in DOCTYPES:
        try:
            frappe.reload_doc("shopee_bridge", "doctype", frappe.scrub(dn))
        except Exception:
            pass
    # Clear module cache
    frappe.clear_cache()


def _sync_workspace():
    if not frappe.db.exists("DocType", "Workspace"):
        return
    try:
        ws_name = MODULE
        if frappe.db.exists("Workspace", ws_name):
            ws = frappe.get_doc("Workspace", ws_name)
        else:
            ws = frappe.new_doc("Workspace")
            ws.name = ws_name
        ws.module = MODULE
        if hasattr(ws, "public"):
            ws.public = 1
        ws.label = MODULE
        # Provide minimal content if empty
        if not ws.get("content") and "content" in ws.meta.get_fieldnames():
            ws.content = json.dumps([
                {"type": "shortcut", "label": "Shopee Settings", "link_to": "Shopee Settings", "doctype": "Shopee Settings"},
                {"type": "shortcut", "label": "Webhook Inbox", "link_to": "List/Shopee Webhook Inbox", "doctype": "Shopee Webhook Inbox"},
                {"type": "shortcut", "label": "Sync Log", "link_to": "List/Shopee Sync Log", "doctype": "Shopee Sync Log"},
            ])
        ws.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge patch workspace repair")


def execute():
    _ensure_module_def()
    _fix_modules_txt()
    _reassign_doctypes()
    _reload()
    _sync_workspace()
    frappe.db.commit()
    print("Shopee Bridge module mapping repaired")
