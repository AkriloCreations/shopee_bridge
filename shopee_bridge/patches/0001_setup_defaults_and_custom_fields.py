"""Idempotent setup of Shopee Bridge defaults and required Custom Fields.

This patch:
 1. Ensures the Single doctype record for "Shopee Settings" exists.
 2. Sets default values (region, webhook toggles) if empty/None.
 3. Creates required Custom Fields on core doctypes (idempotent).
 4. Optionally creates Customer Issue custom fields if that DocType exists.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint
from frappe import DuplicateEntryError, DoesNotExistError

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def _ensure_single_settings_record():
    """Insert the single doctype row if it does not yet exist."""
    try:
        frappe.get_doc("Shopee Settings")  # will load (single)
    except DoesNotExistError:
        # create empty single
        try:
            frappe.get_doc({"doctype": "Shopee Settings"}).insert(ignore_permissions=True)
        except DuplicateEntryError:
            # created concurrently elsewhere – ignore
            pass


def _apply_default_settings():
    ss = frappe.get_doc("Shopee Settings")
    changed = False
    if not ss.get("region"):
        ss.region = "ID"  # Indonesia default
        changed = True
    if ss.get("webhook_live_enabled") in (None, ""):
        ss.webhook_live_enabled = 0
        changed = True
    if ss.get("webhook_test_enabled") in (None, ""):
        ss.webhook_test_enabled = 0
        changed = True
    if changed:
        ss.save(ignore_permissions=True)


def _create_custom_fields():
    # Base custom field map
    custom_field_map = {
        "Sales Order": [
            {
                "fieldname": "shopee_order_sn",
                "label": "Shopee Order SN",
                "fieldtype": "Data",
                "unique": 1,
                "reqd": 0,
                "insert_after": "customer",
                "description": "Shopee Order Serial Number",
                "idx": 1,
            },
            {"fieldname": "buyer_user_id", "fieldtype": "Data", "label": "Buyer User ID"},
            {"fieldname": "shopee_sync_hash", "fieldtype": "Data", "label": "Shopee Sync Hash"},
            {"fieldname": "last_pushed_update_time", "fieldtype": "Datetime", "label": "Last Pushed Update Time"},
        ],
        "Sales Invoice": [
            {
                "fieldname": "shopee_order_sn",
                "fieldtype": "Data",
                "label": "Shopee Order SN",
                "unique": 1,
                "idx": 1,
            },
            {"fieldname": "escrow_synced", "fieldtype": "Check", "label": "Escrow Synced", "default": 0},
            {"fieldname": "escrow_synced_at", "fieldtype": "Datetime", "label": "Escrow Synced At"},
            {"fieldname": "escrow_fee_total", "fieldtype": "Currency", "label": "Escrow Fee Total"},
            {"fieldname": "escrow_net", "fieldtype": "Currency", "label": "Escrow Net"},
            {"fieldname": "payout_batch_id", "fieldtype": "Data", "label": "Payout Batch ID"},
            {"fieldname": "last_pushed_update_time", "fieldtype": "Datetime", "label": "Last Pushed Update Time"},
        ],
        "Delivery Note": [
            {"fieldname": "shopee_order_sn", "fieldtype": "Data", "label": "Shopee Order SN", "idx": 1},
            {"fieldname": "package_number", "fieldtype": "Data", "label": "Package Number", "idx": 1},
            {"fieldname": "tracking_number", "fieldtype": "Data", "label": "Tracking Number", "idx": 1},
            {"fieldname": "status_pickup", "fieldtype": "Data", "label": "Pickup Status"},
            {"fieldname": "status_delivery", "fieldtype": "Data", "label": "Delivery Status"},
            {"fieldname": "delivered_at", "fieldtype": "Datetime", "label": "Delivered At"},
        ],
    }

    # Optional Customer Issue fields
    if frappe.db.exists("DocType", "Customer Issue"):
        custom_field_map["Customer Issue"] = [
            {"fieldname": "return_sn", "fieldtype": "Data", "label": "Return SN", "unique": 1, "idx": 1},
            {"fieldname": "shopee_payload_json", "fieldtype": "Long Text", "label": "Shopee Payload JSON"},
        ]

    # Idempotent creation (create_custom_fields handles existing gracefully)
    create_custom_fields(custom_field_map, ignore_validate=True)


def execute():  # frappe standard patch entrypoint
    _ensure_single_settings_record()
    _apply_default_settings()
    _create_custom_fields()
    print("Shopee Bridge defaults/custom fields ensured")
