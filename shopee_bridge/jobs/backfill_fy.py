"""Full fiscal year backfill orchestrator job."""

from typing import Dict, Any
import frappe # type: ignore


def run(company: str, fiscal_year_name: str) -> Dict[str, Any]:
    from ..services import fiscal
    summary: Dict[str, Any] = {"company": company, "fiscal_year": fiscal_year_name}
    try:
        res = fiscal.run_fiscal_year_full_sync(company, fiscal_year_name)
        summary.update(res)
        status = "ok" if res.get("ok") else "fail"
        # Write summary log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "backfill_fy",
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
            "sync_type": "backfill_fy",
            "status": "fail",
            "error_message": str(exc),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary

