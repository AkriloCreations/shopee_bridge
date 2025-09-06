"""Hourly escrow batch sync job (finance sync)."""

from typing import Dict, Any
import frappe


def run(hours: int = 1) -> Dict[str, Any]:
    from ..services import finance
    summary: Dict[str, Any] = {"hours": hours, "count": 0, "errors": []}
    try:
        svc = finance.sync_escrow_for_completed_orders(min_age_hours=hours)
        summary.update({
            "count": svc.get("count", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        # Write log entry using frappe.get_doc instead of DocType import
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "sync_finance",
            "status": status,
            "error_message": None,
            "details": frappe.as_json(summary),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # pragma: no cover
        summary["errors"].append(str(exc))
        # Write error log entry
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "sync_finance",
            "status": "fail",
            "error_message": str(exc),
            "details": frappe.as_json(summary),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary


def sync_recent_escrow(hours: int = 24) -> dict:
    """Sync escrow for orders from the last N hours.

    Args:
        hours: Number of hours to look back

    Returns:
        Dict with orders_scanned and escrow_logged counts
    """
    from ..services import orders, finance
    from .. import helpers

    since_epoch = helpers.now_epoch() - hours * 3600
    order_sns = orders.get_order_list(since_epoch, helpers.now_epoch())
    orders_scanned = len(order_sns)
    escrow_logged = 0

    for sn in order_sns:
        try:
            escrow = finance.get_escrow_detail("", "", 0, sn)
            if escrow and not escrow.get("error"):
                finance.log_escrow("", sn, escrow)
                escrow_logged += 1
        except Exception as e:
            frappe.log_error(f"Escrow sync failed for {sn}: {e}", "Shopee Escrow Sync")

    return {"orders_scanned": orders_scanned, "escrow_logged": escrow_logged}

