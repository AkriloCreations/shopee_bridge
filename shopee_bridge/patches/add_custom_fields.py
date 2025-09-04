# shopee_bridge/patches/add_custom_fields.py
from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
    """Create/ensure custom fields needed by Shopee Bridge. Idempotent."""
    fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", insert_after="title",
                 unique=1, reqd=0, in_standard_filter=1),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data", insert_after="shopee_order_sn"),
            dict(fieldname="shopee_sync_hash", label="Shopee Sync Hash", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Sales Invoice": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1, in_standard_filter=1),
            dict(fieldname="escrow_synced", label="Shopee Escrow Synced", fieldtype="Check", default=0),
            dict(fieldname="escrow_synced_at", label="Shopee Escrow Synced At", fieldtype="Datetime"),
            dict(fieldname="escrow_fee_total", label="Shopee Fee Total", fieldtype="Currency"),
            dict(fieldname="escrow_net", label="Shopee Net Payout", fieldtype="Currency"),
            dict(fieldname="payout_batch_id", label="Shopee Payout Batch ID", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Delivery Note": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="package_number", label="Shopee Package Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="tracking_number", label="Shopee Tracking Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="status_pickup", label="Shopee Pickup Status", fieldtype="Data"),
            dict(fieldname="status_delivery", label="Shopee Delivery Status", fieldtype="Data"),
            dict(fieldname="delivered_at", label="Shopee Delivered At", fieldtype="Datetime"),
        ],
    }

    create_custom_fields(fields, ignore_validate=True)
    frappe.db.commit()
