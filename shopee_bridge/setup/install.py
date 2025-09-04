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
    # Custom fields to add to core doctypes
    custom_fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1, idx=1, insert_after="order_id"),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data", insert_after="shopee_order_sn"),
            dict(fieldname="buyer_username", label="Shopee Buyer Username", fieldtype="Data", insert_after="buyer_user_id"),
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
    }

    try:
        # Shopee Settings Single doctype: nothing to do, auto-created on first save

        # Create custom fields (skip errors)
        try:
            create_custom_fields(custom_fields, update=True)
        except Exception as cf_err:
            frappe.log_error(message=str(cf_err), title="Shopee Bridge custom fields error")

        # Sanitize existing Workspace documents
        import json

        TEXTY = {"Data","Small Text","Text","Long Text","Text Editor","Markdown Editor","HTML Editor","Link","Select","Code","JSON"}

        def has_field(doctype, fieldname):
            meta = frappe.get_meta(doctype)
            return any(df.fieldname == fieldname for df in meta.fields)

        def sanitize_doc(doctype, name):
            meta = frappe.get_meta(doctype)
            doc = frappe.get_doc(doctype, name)
            changed = False
            # Critical fields
            if has_field(doctype, "label") and not (doc.get("label") or "").strip():
                doc.label = doc.name; changed = True
            if has_field(doctype, "content") and doc.get("content") is None:
                doc.content = "[]"; changed = True
            if has_field(doctype, "description") and doc.get("description") is None:
                doc.description = ""; changed = True
            if has_field(doctype, "icon") and doc.get("icon") is None:
                doc.icon = ""; changed = True
            # Sanitize text fields
            for df in meta.fields:
                if df.fieldtype in TEXTY and getattr(doc, df.fieldname, None) is None:
                    setattr(doc, df.fieldname, "[]" if df.fieldtype == "JSON" else "")
                    changed = True
                elif df.fieldtype in ("Table", "Table MultiSelect"):
                    rows = doc.get(df.fieldname) or []
                    if rows:
                        child_meta = frappe.get_meta(df.options)
                        for row in rows:
                            for cdf in child_meta.fields:
                                if cdf.fieldtype in TEXTY and row.get(cdf.fieldname) is None:
                                    row.set(cdf.fieldname, "[]" if cdf.fieldtype == "JSON" else "")
                                    changed = True
            if changed:
                doc.save(ignore_permissions=True)
            return changed

        fixed = 0
        for nm in frappe.get_all("Workspace", pluck="name"):
            try:
                if sanitize_doc("Workspace", nm):
                    fixed += 1
            except Exception:
                pass
        frappe.db.commit()

    # Ensure Module Def exists for menu
        MOD = "Shopee Bridge"
        if not frappe.db.exists("Module Def", {"name": MOD}):
            frappe.get_doc({"doctype": "Module Def", "module_name": MOD, "custom": 1}).insert(ignore_permissions=True)
            frappe.db.commit()

        # Create or update Workspace for Shopee Bridge
        NAME = "Shopee Bridge"
        if frappe.db.exists("Workspace", NAME):
            ws = frappe.get_doc("Workspace", NAME)
        else:
            ws = frappe.new_doc("Workspace")
            ws.name = NAME
            ws.flags.name_set = True
        ws.label = MOD
        ws.module = MOD
        ws.category = "Modules"
        ws.public = 1
        ws.is_hidden = 0
        ws.description = ""
        ws.icon = ""
        ws.content = json.dumps([
            {"type": "shortcut", "label": "Shopee", "items": [
                {"label": "Shopee Settings", "type": "DocType", "link_to": "DocType/Shopee Settings"}
            ]}
        ])
        ws.save(ignore_permissions=True)
        frappe.db.commit()

        # Success message
        print(_("Shopee Bridge install: Custom fields and workspace ensured."))
    except Exception as e:
        frappe.log_error(message=str(e), title="Shopee Bridge after_install error")
        print(_("Shopee Bridge install failed: {0}").format(e))