"""Bank reconciliation job (strict policy)."""

from typing import Dict, Any
import frappe


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
            "sync_type": "reconcile_bank",
            "status": status,
            "details": frappe.as_json(summary),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # pragma: no cover
        summary["error"] = str(exc)
        # Write error log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "reconcile_bank",
            "status": "fail",
            "error_message": str(exc),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary

