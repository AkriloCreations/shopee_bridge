"""Incremental shipping (logistics) sync job (stub)."""
import frappe
from typing import Dict, Any
import time



def run(minutes: int = 30) -> Dict[str, Any]:
    now_ts = int(time.time())
    from_ts = now_ts - minutes * 60
    summary: Dict[str, Any] = {
        "minutes": minutes,
        "window_from": from_ts,
        "window_to": now_ts,
        "updates_found": 0,
        "processed": 0,
        "errors": [],
    }
    from ..services import logistics
    try:
        svc = logistics.sync_shipping_status(updated_since_minutes=minutes)
        summary.update({
            "updates_found": svc.get("updates_found", 0),
            "processed": svc.get("updates_processed", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        # Write summary log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "category": "sync_shipping",
            "ref": f"batch_{from_ts}_{now_ts}",
            "status": "DONE" if status == "ok" else "ERROR",
            "payload_json": frappe.as_json(summary),
            "created_epoch": now_ts
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as exc:  # pragma: no cover
        msg = str(exc)
        summary["errors"].append(msg)
        # Write error log
        log_doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "category": "sync_shipping",
            "ref": f"error_{from_ts}_{now_ts}",
            "status": "ERROR",
            "error_message": msg,
            "payload_json": frappe.as_json(summary),
            "created_epoch": now_ts
        })
        log_doc.insert(ignore_permissions=True)
        frappe.db.commit()
    return summary

