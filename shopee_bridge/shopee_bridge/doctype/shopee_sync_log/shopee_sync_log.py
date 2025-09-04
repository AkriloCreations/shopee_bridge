import frappe, json
from datetime import datetime

def write_log(
    job: str,
    key: str,
    status: str,
    message: str = "",
    meta: dict | None = None,
    payload_hash: str | None = None,
    keep_tail: int = 200,
) -> str:
    """Create / update a Shopee Sync Log row (standard DocType).

    Behavior:
    - One row per (job, key) combination per run; if existing open row with same status found
      we append tail, else create new row.
    - Maintains rolling tail (max keep_tail lines) in `log_tail`.
    - Stores aggregated counters from meta.
    """
    if not job:
        job = "unknown"
    if not key:
        key = "n/a"
    existing_name = frappe.db.get_value(
        "Shopee Sync Log",
        {"job": job, "key_ref": key},
        "name",
    )
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {status} | {message[:300]}".strip()
    if existing_name:
        doc = frappe.get_doc("Shopee Sync Log", existing_name)
        tail = (doc.log_tail or "").splitlines()
        tail.append(line)
        if len(tail) > keep_tail:
            tail = tail[-keep_tail:]
        doc.log_tail = "\n".join(tail)
        doc.status = status
    else:
        doc = frappe.get_doc({
            "doctype": "Shopee Sync Log",
            "job": job,
            "key_ref": key,
            "sync_type": meta.get("sync_type") if isinstance(meta, dict) else "orders",
            "status": status,
            "log_tail": line,
        })
    # meta snapshot
    if isinstance(meta, dict):
        try:
            doc.payload_sample = json.dumps(meta, ensure_ascii=False, indent=2)[:100000]
        except Exception:
            doc.payload_sample = str(meta)[:100000]
        for fld in ["total", "success", "failed"]:
            if fld in meta:
                setattr(doc, fld, int(meta.get(fld) or 0))
        if meta.get("sync_type"):
            doc.sync_type = meta.get("sync_type")
    doc.last_updated = datetime.utcnow()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return doc.name