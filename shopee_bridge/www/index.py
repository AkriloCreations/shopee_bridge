# apps/shopee_bridge/shopee_bridge/www/index.py
import frappe
from frappe.utils import get_url

def get_context(context):
    # Shopee akan redirect ke base domain: https://erp.managerio.ddns.net/?code=...&shop_id=...
    code = frappe.form_dict.get("code")
    shop_id = frappe.form_dict.get("shop_id")

    if code and shop_id:
        # teruskan ke API oauth_callback (allow_guest=True) untuk tukar token
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = get_url(
            f"/api/method/shopee_bridge.api.oauth_callback?code={code}&shop_id={shop_id}"
        )
        return context  # stop di sini (akan redirect)

    # tidak ada parameter Shopee -> biarkan user lihat homepage standar
    # arahkan ke desk (optional) bila kamu pakai ERPNext full
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = get_url("/app")
    return context

