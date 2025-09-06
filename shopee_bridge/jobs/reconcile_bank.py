"""Bank reconciliation job (strict policy)."""

from typing import Dict, Any
import frappe
import time


def run(days_back: int = 2) -> Dict[str, Any]:
    from ..services import finance
    summary: Dict[str, Any] = {"days_back": days_back}
    try:
        res = finance.reconcile_bank_strict(days_back=days_back)
        summary.update(res)
        status = "ok"
        # Write summary log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "category": "reconcile_bank",
            "ref": f"batch_{days_back}d",
            "status": "DONE",
            "payload_json": frappe.as_json(summary),
            "created_epoch": int(time.time())
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # pragma: no cover
        summary["error"] = str(exc)
        # Write error log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "category": "reconcile_bank",
            "ref": f"error_{days_back}d",
            "status": "ERROR",
            "error_message": str(exc),
            "payload_json": frappe.as_json(summary),
            "created_epoch": int(time.time())
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary

