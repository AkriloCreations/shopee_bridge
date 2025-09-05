import frappe
from frappe.model.document import Document
from frappe.utils import convert_utc_to_system_timezone
import pytz

class ShopeeSettings(Document):
    def validate(self):
        if not self.partner_id or not self.partner_key or not self.redirect_url:
            frappe.throw("Partner ID, Partner Key, dan Redirect URL wajib diisi.")

        if self.token_expires_at:
            # token_expires_at is now always int epoch UTC
            try:
                expiry_epoch = int(self.token_expires_at)
                from datetime import datetime
                import pytz
                now_epoch = int(datetime.now().timestamp())
                dt_utc = datetime.utcfromtimestamp(expiry_epoch)
                jakarta = pytz.timezone("Asia/Jakarta")
                dt_jakarta = dt_utc.astimezone(jakarta)
                wib_str = dt_jakarta.strftime('%d-%m-%Y %H:%M:%S')
                if expiry_epoch < now_epoch:
                    frappe.msgprint(f"Access Token sudah expired (WIB: {wib_str}). Silakan refresh.")
                else:
                    frappe.msgprint(f"Access Token valid! (WIB: {wib_str})")
            except Exception as e:
                frappe.msgprint(f"Gagal membaca expiry token: {e}")

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
