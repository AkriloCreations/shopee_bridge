"""Bank reconciliation job (strict policy)."""

from typing import Dict, Any
import frappe


def run(days_back: int = 2) -> Dict[str, Any]:
    from ..services import finance
    from ..shopee_bridge.doctype.shopee_sync_log.shopee_sync_log import ShopeeSyncLog
    summary: Dict[str, Any] = {"days_back": days_back}
    try:
        res = finance.reconcile_bank_strict(days_back=days_back)
        summary.update(res)
        status = "ok"
        ShopeeSyncLog.write_log("reconcile_bank", f"days_back:{days_back}", status, meta=summary)
    except Exception as exc:  # pragma: no cover
        summary["error"] = str(exc)
        ShopeeSyncLog.write_log("reconcile_bank", f"days_back:{days_back}", "fail", message=str(exc))
    return summary

