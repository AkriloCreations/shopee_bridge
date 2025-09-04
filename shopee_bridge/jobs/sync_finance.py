"""Hourly escrow batch sync job (finance sync)."""

from typing import Dict, Any
import frappe


def run(hours: int = 1) -> Dict[str, Any]:
    from ..services import finance
    from ..doctype.shopee_sync_log.shopee_sync_log import write_log
    summary: Dict[str, Any] = {"hours": hours, "count": 0, "errors": []}
    try:
        svc = finance.sync_escrow_for_completed_orders(min_age_hours=hours)
        summary.update({
            "count": svc.get("count", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        write_log("sync_finance", f"hours:{hours}", status, meta=summary)
    except Exception as exc:  # pragma: no cover
        summary["errors"].append(str(exc))
        write_log("sync_finance", f"hours:{hours}", "fail", message=str(exc))
    return summary

