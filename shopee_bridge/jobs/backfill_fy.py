"""Full fiscal year backfill orchestrator job."""

from typing import Dict, Any
import frappe


def run(company: str, fiscal_year_name: str) -> Dict[str, Any]:
    from ..services import fiscal
    from ..doctype.shopee_sync_log.shopee_sync_log import write_log
    summary: Dict[str, Any] = {"company": company, "fiscal_year": fiscal_year_name}
    try:
        res = fiscal.run_fiscal_year_full_sync(company, fiscal_year_name)
        summary.update(res)
        status = "ok" if res.get("ok") else "fail"
        write_log("backfill_fy", f"fy:{fiscal_year_name}", status, meta=summary)
    except Exception as exc:  # pragma: no cover
        summary["error"] = str(exc)
        write_log("backfill_fy", f"fy:{fiscal_year_name}", "fail", message=str(exc))
    return summary

