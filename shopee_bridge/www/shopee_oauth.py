import frappe
from frappe.utils import get_url

def get_context(context):
    # Ambil query string dari Shopee (code, shop_id, dsb)
    code = frappe.form_dict.get("code")
    shop_id = frappe.form_dict.get("shop_id")

    if code and shop_id:
        # forward ke server method oauth_callback
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = (
            get_url("/api/method/shopee_bridge.api.oauth_callback")
            + f"?code={code}&shop_id={shop_id}"
        )
    else:
        context.message = "Invalid Shopee redirect"
    return context

