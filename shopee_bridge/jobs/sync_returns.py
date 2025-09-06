"""Incremental returns sync job (stub orchestrator)."""

from typing import Dict, Any
import time
import frappe


def run(minutes: int = 30) -> Dict[str, Any]:
    now_ts = int(time.time())
    from_ts = now_ts - minutes * 60
    summary: Dict[str, Any] = {
        "minutes": minutes,
        "window_from": from_ts,
        "window_to": now_ts,
        "returns_found": 0,
        "processed": 0,
        "errors": [],
    }
    from ..services import returns as returns_service
    try:
        # Use service incremental stub
        svc = returns_service.sync_returns_incremental(updated_since_minutes=minutes)
        summary.update({
            "returns_found": svc.get("returns_found", 0),
            "processed": svc.get("returns_processed", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        # Write summary log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "sync_returns",
            "status": status,
            "details": frappe.as_json(summary),
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # pragma: no cover
        msg = str(exc)
        summary["errors"].append(msg)
        # Write error log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "sync_type": "sync_returns",
            "status": "fail",
            "error_message": msg,
            "timestamp": frappe.utils.now()
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary

