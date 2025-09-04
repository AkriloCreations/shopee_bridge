"""Ensure Shopee Bridge workspace and DocTypes are visible on Desk.

Idempotent operations:
 - Ensure Module Def exists
 - Reload critical DocTypes (settings, inbox, sync log)
 - Mark them standard (custom=0) if needed
 - Re-create Workspace record from JSON file if field layout missing
"""

from __future__ import annotations

import json
import os
import frappe
from frappe.modules import get_module_path

MODULE = "Shopee Bridge"
WORKSPACE_NAME = "Shopee Bridge"
DOCTYPES = [
    "Shopee Settings",
    "Shopee Webhook Inbox",
    "Shopee Sync Log",
]


def execute():
    # 1. Module Def
    if not frappe.db.exists("Module Def", MODULE):
        frappe.get_doc({"doctype": "Module Def", "module_name": MODULE, "custom": 1}).insert(ignore_permissions=True)

    # 2. Reload doctypes if present in app (ignore errors)
    for dn in DOCTYPES:
        try:
            # module name for reload is app python module, we use app label path
            frappe.reload_doc("shopee_bridge", "doctype", frappe.scrub(dn))
        except Exception:
            pass
        try:
            if frappe.db.exists("DocType", dn):
                dt = frappe.get_doc("DocType", dn)
                if getattr(dt, "custom", 0):
                    dt.custom = 0
                    dt.save(ignore_permissions=True)
        except Exception:
            pass

    # 3. Workspace sync from JSON if workspace doctype installed
    if frappe.db.exists("DocType", "Workspace"):
        try:
            ws_path = os.path.join(
                get_module_path("shopee_bridge"), "workspace", "shopee_bridge", "shopee_bridge.json"
            )
            if os.path.exists(ws_path):
                with open(ws_path) as fh:
                    data = json.load(fh)
                # Ensure required keys
                data.setdefault("doctype", "Workspace")
                data.setdefault("public", 1)
                data.setdefault("module", MODULE)
                data.setdefault("label", MODULE)
                name = data.get("name") or WORKSPACE_NAME
                data["name"] = name
                if frappe.db.exists("Workspace", name):
                    doc = frappe.get_doc("Workspace", name)
                    for k, v in data.items():
                        if k in ("name", "doctype"):  # skip immutable
                            continue
                        doc.set(k, v)
                else:
                    doc = frappe.get_doc(data)
                doc.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge patch workspace sync")

    frappe.db.commit()
    print("Shopee Bridge workspace & doctypes synced")
