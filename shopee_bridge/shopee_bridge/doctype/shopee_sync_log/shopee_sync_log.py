import frappe
from frappe.model.document import Document
import time

class ShopeeSyncLog(Document):
    """
    Controller for Shopee Sync Log doctype.
    Provides helpers for logging sync operations.
    """

    @staticmethod
    def write_log(sync_type, reference, status, message=None, meta=None):
        """
        Write a log entry to the Shopee Sync Log.

        :param sync_type: Type of sync operation (e.g., 'sync_orders', 'sync_shipping').
        :param reference: Reference identifier for the log entry.
        :param status: Status of the operation ('success', 'failed').
        :param message: Optional error message or additional details.
        :param meta: Optional metadata dictionary.
        """
        log = frappe.new_doc("Shopee Sync Log")
        log.category = sync_type
        log.ref = reference
        log.status = status.upper() if status in ["new", "done", "error"] else "ERROR"
        log.error_message = message
        log.payload_json = frappe.as_json(meta) if meta else None
        log.created_epoch = int(time.time())
        log.insert(ignore_permissions=True)
        frappe.db.commit()
