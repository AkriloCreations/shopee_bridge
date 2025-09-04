import frappe
import json
import hashlib
from datetime import datetime

def write_log(
    job: str,
    key: str,
    status: str,
    message: str = "",
    meta: dict | None = None,
    payload_hash: str | None = None
) -> str:
    """
    Insert a Shopee Sync Log record for tracking sync jobs and errors.

    Args:
        job (str): Name of the sync job (e.g., 'sync_orders').
        key (str): Idempotency key for the record (e.g., order_sn).
        status (str): Status of the job ('ok', 'fail', 'skip').
        message (str, optional): Log message or error details.
        meta (dict, optional): Additional metadata to serialize as JSON.
        payload_hash (str, optional): SHA1 hash of the payload for deduplication.

    Returns:
        str: The name of the created Shopee Sync Log document.

    Idempotency:
        Each log is unique per (job, key, payload_hash, started_at).
        This function does not deduplicate logs.

    Raises:
        frappe.ValidationError: If required fields are missing.
    """
    doc = frappe.get_doc({
        "doctype": "Shopee Sync Log",
        "job": job,
        "key": key,
        "status": status,
        "message": message,
        "payload_hash": payload_hash or "",
        "meta_json": json.dumps(meta or {}, ensure_ascii=False),
        "started_at": datetime.utcnow(),
        "ended_at": datetime.utcnow()
    })
    doc.insert(ignore_permissions=True)
    return doc.name