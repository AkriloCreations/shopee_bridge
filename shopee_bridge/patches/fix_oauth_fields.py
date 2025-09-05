"""
Fix OAuth Fields in Shopee Settings

This patch ensures all required OAuth fields exist in the Shopee Settings doctype.
"""

import frappe
from frappe.model.utils.rename_field import rename_field
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Add missing OAuth fields to Shopee Settings"""
    
    # Get the doctype
    doctype = "Shopee Settings"
    
    try:
        # Check if fields exist, if not create them as custom fields
        meta = frappe.get_meta(doctype)
        existing_fields = [f.fieldname for f in meta.fields]
        
        required_fields = [
            {
                "fieldname": "last_auth_code",
                "fieldtype": "Data", 
                "label": "Authorization Code",
                "description": "Enter the code from OAuth callback (valid for 10 minutes)",
                "insert_after": "shop_id"
            },
            {
                "fieldname": "merchant_id",
                "fieldtype": "Data",
                "label": "Merchant ID", 
                "description": "For merchant apps (optional)",
                "insert_after": "shop_id"
            }
        ]
        
        custom_fields = {}
        
        for field in required_fields:
            if field["fieldname"] not in existing_fields:
                if doctype not in custom_fields:
                    custom_fields[doctype] = []
                custom_fields[doctype].append(field)
                frappe.log_error(f"Adding missing field: {field['fieldname']} to {doctype}")
        
        # Create custom fields if any are missing
        if custom_fields:
            create_custom_fields(custom_fields, update=True)
            frappe.db.commit()
            frappe.log_error(f"Created custom fields for {doctype}: {list(custom_fields[doctype])}")
        
        # Update redirect_url default if it's empty or wrong
        if frappe.db.exists("Shopee Settings", "Shopee Settings"):
            doc = frappe.get_doc("Shopee Settings", "Shopee Settings")
            if not doc.redirect_url or doc.redirect_url == "https://erpdev.managerio.ddns.net/":
                doc.redirect_url = "https://erpdev.managerio.ddns.net/app/shopee-settings"
                doc.save(ignore_permissions=True)
                frappe.db.commit()
                frappe.log_error("Updated redirect_url to settings page")
        
    except Exception as e:
        frappe.log_error(f"Error in fix_oauth_fields patch: {str(e)}", "Shopee OAuth Fields Patch")
        raise