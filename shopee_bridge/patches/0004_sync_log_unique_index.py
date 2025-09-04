"""Add unique index for Shopee Sync Log (job, key_ref).

Ensures only one active row per combination of job and key_ref.
"""
import frappe

def execute():
    """Create unique index on Shopee Sync Log."""
    try:
        frappe.db.sql(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            `uniq_shopee_sync_log_job_key`
            ON `tabShopee Sync Log` (job, key_ref)
            """
        )
        print("[Shopee Bridge] Unique index created for Shopee Sync Log (job, key_ref).")
    except Exception as exc:
        frappe.log_error(str(exc), "Shopee Bridge 0004 unique index")
