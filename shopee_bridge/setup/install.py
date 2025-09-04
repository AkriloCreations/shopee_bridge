from frappe import _
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def after_install():
    """
    Post-install hook for Shopee Bridge app.

    Tasks:
    1. Ensure Shopee Settings Single doctype exists (auto-created on first save).
    2. Create required Custom Fields on core doctypes if not present.
    3. Print a clear success message.

    Idempotency:
    - Custom fields are only created if missing.
    - No duplicate writes.

    Raises:
        Logs errors to frappe.log_error and prints failure message.
    """
    custom_fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1, idx=1, insert_after="order_id"),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data", insert_after="shopee_order_sn"),
            dict(fieldname="shopee_sync_hash", label="Shopee Sync Hash", fieldtype="Data", insert_after="buyer_user_id"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime", insert_after="shopee_sync_hash"),
        ],
        "Sales Invoice": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1, idx=1, insert_after="order_id"),
            dict(fieldname="escrow_synced", label="Shopee Escrow Synced", fieldtype="Check", insert_after="shopee_order_sn"),
            dict(fieldname="escrow_synced_at", label="Shopee Escrow Synced At", fieldtype="Datetime", insert_after="escrow_synced"),
            dict(fieldname="escrow_fee_total", label="Shopee Escrow Fee Total", fieldtype="Currency", insert_after="escrow_synced_at"),
            dict(fieldname="escrow_net", label="Shopee Escrow Net", fieldtype="Currency", insert_after="escrow_fee_total"),
            dict(fieldname="payout_batch_id", label="Shopee Payout Batch ID", fieldtype="Data", insert_after="escrow_net"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime", insert_after="payout_batch_id"),
        ],
        "Delivery Note": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", idx=1, insert_after="order_id"),
            dict(fieldname="package_number", label="Shopee Package Number", fieldtype="Data", idx=1, insert_after="shopee_order_sn"),
            dict(fieldname="tracking_number", label="Shopee Tracking Number", fieldtype="Data", idx=1, insert_after="package_number"),
            dict(fieldname="status_pickup", label="Shopee Pickup Status", fieldtype="Data", insert_after="tracking_number"),
            dict(fieldname="status_delivery", label="Shopee Delivery Status", fieldtype="Data", insert_after="status_pickup"),
            dict(fieldname="delivered_at", label="Shopee Delivered At", fieldtype="Datetime", insert_after="status_delivery"),
        ],
        "Customer Issue": [
            dict(fieldname="return_sn", label="Shopee Return SN", fieldtype="Data", unique=1, idx=1, insert_after="issue_type"),
            dict(fieldname="shopee_payload_json", label="Shopee Payload JSON", fieldtype="Long Text", insert_after="return_sn"),
        ],
    }

    try:
        # Shopee Settings Single doctype: nothing to do, auto-created on first save

        # Create custom fields
        create_custom_fields(custom_fields, update=True)

        print(_("Shopee Bridge install: Custom fields ensured."))
    except Exception as e:
        frappe.log_error(message=str(e), title="Shopee Bridge after_install error")
        print(_("Shopee Bridge install failed: {0}").format(e))