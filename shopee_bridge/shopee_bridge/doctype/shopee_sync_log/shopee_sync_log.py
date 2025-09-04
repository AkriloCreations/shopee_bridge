import frappe
import json
from datetime import datetime

def write_log(
    job: str,
    key: str,
    status: str,
    message: str = "",
    meta: dict | None = None,
    payload_hash: str | None = None,
    keep: int = 200,
) -> str:
    """Append a line to the Single Shopee Sync Log document.

    Since the doctype is now Single, we keep a rolling tail of recent log
    lines inside the Long Text field ``log_tail`` for quick inspection.
    Structured / aggregated info can be passed via ``meta`` which will be
    dumped into ``payload_sample`` (overwritten each call) for debugging.
    """
    # Access single doc
    doc = frappe.get_doc("Shopee Sync Log")  # single

    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {job} | {key} | {status} | {message[:300]}".strip()

    tail = (doc.log_tail or "").splitlines() if getattr(doc, "log_tail", None) else []
    tail.append(line)
    # trim
    if len(tail) > keep:
        tail = tail[-keep:]

    doc.log_tail = "\n".join(tail)
    if meta:
        # store latest meta snapshot
        try:
            doc.payload_sample = json.dumps(meta, ensure_ascii=False, indent=2)[:100000]
        except Exception:  # pragma: no cover
            doc.payload_sample = str(meta)[:100000]
    doc.notes = (doc.notes or "") if hasattr(doc, "notes") else ""
    doc.last_updated = datetime.utcnow()
    # update counters if provided in meta
    if isinstance(meta, dict):
        for fld in ["total", "success", "failed"]:
            if fld in meta and hasattr(doc, fld):
                setattr(doc, fld, int(meta.get(fld) or 0))
    frappe.db.set_value(
        "Shopee Sync Log",
        None,  # single
        {
            "log_tail": doc.log_tail,
            "payload_sample": getattr(doc, "payload_sample", None),
            "notes": doc.notes,
            "total": getattr(doc, "total", None),
            "success": getattr(doc, "success", None),
            "failed": getattr(doc, "failed", None),
            "last_updated": doc.last_updated,
        },
        update_modified=True,
    )
    return "Shopee Sync Log"