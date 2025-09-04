import frappe
from frappe.model.document import Document

class ShopeeSettings(Document):
    def validate(self):
        if not self.partner_id or not self.partner_key or not self.redirect_url:
            frappe.throw("Partner ID, Partner Key, dan Redirect URL wajib diisi.")
        
        # Properly handle token expiration comparison
        if self.token_expires_at:
            from datetime import datetime
            if isinstance(self.token_expires_at, str):
                token_expires = frappe.utils.get_datetime(self.token_expires_at)
            else:
                token_expires = self.token_expires_at
                
            current_time = frappe.utils.now_datetime()
            if token_expires < current_time:
                frappe.msgprint("Access Token sudah expired, silakan refresh.")

@frappe.whitelist()
def connect_to_shopee(scopes: list[str] | None = None) -> dict:
    try:
        from shopee_bridge import auth
        return {"ok": True, "url": auth.build_authorize_url(scopes or [])}
    except Exception as e:
        _save_last_error(str(e)); return {"ok": False, "error": str(e)}

@frappe.whitelist(allow_guest=True)
def oauth_callback(**params) -> dict:
    try:
        from shopee_bridge import auth
        auth.handle_oauth_callback(params); return {"ok": True}
    except Exception as e:
        _save_last_error(str(e)); return {"ok": False, "error": str(e)}

@frappe.whitelist()
def test_shopee_connection() -> dict:
    try:
        from shopee_bridge import auth
        info = auth.get_shop_info() if hasattr(auth, "get_shop_info") else {}
        return {"ok": True, "info": info}
    except Exception as e:
        _save_last_error(str(e)); return {"ok": False, "error": str(e)}

def _save_last_error(msg: str) -> None:
    try:
        ss = frappe.get_cached_doc("Shopee Settings")
        ss.db_set("last_auth_error", msg, update_modified=False)
    except Exception:
        pass
