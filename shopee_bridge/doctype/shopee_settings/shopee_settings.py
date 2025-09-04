"""
Shopee Settings doctype server methods.
Whitelisted endpoints for OAuth, connection test, and Shopee integration setup.
"""

import frappe
from frappe import _
from frappe.model.document import get_cached_doc

@frappe.whitelist()
def connect_to_shopee(scopes=None):
    """
    Start Shopee OAuth flow and return authorize URL.
    Args:
        scopes (list[str]|None): Shopee OAuth scopes.
    Returns:
        dict: {"url": "..."}
    Raises:
        Sets last_auth_error on Shopee Settings if validation fails or error occurs.
    """
    try:
        doc = frappe.get_cached_doc("Shopee Settings", "Shopee Settings")
        required = [doc.partner_id, doc.partner_key, doc.redirect_url]
        if not all(required):
            msg = _("Shopee Settings missing required fields: partner_id, partner_key, redirect_url.")
            doc.last_auth_error = msg
            doc.save()
            frappe.db.commit()
            return {"error": msg}
        # TODO: Import and call shopee_bridge.auth.build_authorize_url
        # from shopee_bridge import auth
        # url = auth.build_authorize_url(scopes or [])
        url = "TODO: build_authorize_url"
        return {"url": url}
    except Exception as e:
        doc = frappe.get_cached_doc("Shopee Settings", "Shopee Settings")
        doc.last_auth_error = str(e)
        doc.save()
        frappe.db.commit()
        return {"error": str(e)}

@frappe.whitelist()
def oauth_callback(**kwargs):
    """
    Handle Shopee OAuth callback and store tokens.
    Args:
        **kwargs: Callback parameters from Shopee.
    Returns:
        dict: Result from auth.handle_oauth_callback
    Raises:
        Sets last_auth_error on Shopee Settings if error occurs.
    """
    try:
        # TODO: Import and call shopee_bridge.auth.handle_oauth_callback
        # from shopee_bridge import auth
        # result = auth.handle_oauth_callback(kwargs)
        result = {"result": "TODO: handle_oauth_callback"}
        return result
    except Exception as e:
        doc = frappe.get_cached_doc("Shopee Settings", "Shopee Settings")
        doc.last_auth_error = str(e)
        doc.save()
        frappe.db.commit()
        return {"error": str(e)}

@frappe.whitelist()
def test_shopee_connection():
    """
    Test Shopee API connection and return shop info.
    Returns:
        dict: Shop info or error.
    Raises:
        Sets last_auth_error on Shopee Settings if error occurs.
    """
    try:
        # TODO: Import and call shopee_bridge.auth.get_shop_info
        # from shopee_bridge import auth
        # info = auth.get_shop_info()
        info = {"result": "TODO: get_shop_info"}
        return info
    except Exception as e:
        doc = frappe.get_cached_doc("Shopee Settings", "Shopee Settings")
        doc.last_auth_error = str(e)
        doc.save()
        frappe.db.commit()
        return {"error": str(e)}
