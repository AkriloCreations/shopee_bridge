"""Ensure Shopee Sync Log DocType exists (re-create if orphaned).

If migration pruned it as orphan (because consolidated patch ran before model sync),
this patch forces a reload from JSON definition. Idempotent.
"""
from __future__ import annotations

import frappe


def execute():  # noqa: D401
    try:
        frappe.reload_doc("shopee_bridge", "doctype", "shopee_sync_log")
    except Exception as exc:  # pragma: no cover
        frappe.log_error(str(exc), "Shopee Bridge 0002 reload sync log")
        return
    if not frappe.db.exists("DocType", "Shopee Sync Log"):
        # Fallback: create minimal meta (should not normally happen since reload adds it)
        try:
            meta = frappe.get_doc({
                "doctype": "DocType",
                "name": "Shopee Sync Log",
                "module": "Shopee Bridge",
                "custom": 0,
                "issingle": 0,
            })
            meta.insert(ignore_permissions=True)
        except Exception as exc:  # pragma: no cover
            frappe.log_error(str(exc), "Shopee Bridge 0002 create minimal sync log meta")
            return
    print("[Shopee Bridge] Sync Log DocType ensured (0002).")
