import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now
from shopee_bridge import auth

def _get_settings():
    """Fetch Shopee Settings Single Doc (cached)."""
    return frappe.get_cached_doc("Shopee Settings", "Shopee Settings")

def _validate_settings(settings):
    """Ensure required Shopee credentials are present."""
    missing = []
    for field in ["partner_id", "partner_key", "redirect_url"]:
        if not getattr(settings, field, None):
            missing.append(field)
    if missing:
        raise frappe.ValidationError(_("Missing Shopee Settings fields: {0}").format(", ".join(missing)))

def _set_last_auth_error(msg: str):
    """Set last_auth_error on Shopee Settings Single."""
    settings = frappe.get_doc("Shopee Settings", "Shopee Settings")
    settings.last_auth_error = msg
    settings.save(ignore_permissions=True)
    frappe.db.commit()

@frappe.whitelist()
def connect_to_shopee(scopes=None):
    """
    Generate Shopee OAuth authorize URL for connecting a shop.

    Args:
        scopes (list[str] | None): Shopee API scopes to request.

    Returns:
        dict: {"url": "..."} Shopee OAuth URL.

    Raises:
        frappe.ValidationError: If required settings are missing.
    """
    try:
        settings = _get_settings()
        _validate_settings(settings)
        scopes = scopes or ["shop.basic.info", "order", "payment", "returns", "logistics"]
        url = auth.build_authorize_url(scopes)
        return {"url": url}
    except Exception as e:
        _set_last_auth_error(str(e))
        frappe.log_error(message=str(e), title="Shopee Connect Error")
        return {"error": str(e)}

@frappe.whitelist()
def oauth_callback(**kwargs):
    """
    Handle Shopee OAuth callback and store tokens.

    Args:
        **kwargs: Callback parameters from Shopee.

    Returns:
        dict: Result of OAuth handling.

    Side Effects:
        Updates Shopee Settings with tokens or error.
    """
    try:
        settings = _get_settings()
        _validate_settings(settings)
        # TODO: Implement auth.handle_oauth_callback in shopee_bridge.auth
        result = auth.handle_oauth_callback(kwargs)
        return result
    except Exception as e:
        _set_last_auth_error(str(e))
        frappe.log_error(message=str(e), title="Shopee OAuth Callback Error")
        return {"error": str(e)}

@frappe.whitelist()
def test_shopee_connection():
    """
    Test Shopee API connection using current tokens.

    Returns:
        dict: Shop info or error.

    Side Effects:
        Updates last_auth_error on failure.
    """
    try:
        settings = _get_settings()
        _validate_settings(settings)
        # TODO: Implement auth.get_shop_info in shopee_bridge.auth
        result = auth.get_shop_info()
        return result
    except Exception as e:
        _set_last_auth_error(str(e))
        frappe.log_error(message=str(e), title="Shopee Test Connection Error")
        return {"error": str(e)}