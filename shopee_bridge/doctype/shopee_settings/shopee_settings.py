import frappe
from frappe.model.document import Document


class ShopeeSettings(Document):
    def validate(self):  # minimal required field checks
        missing = []
        if not self.partner_id:
            missing.append("Partner ID")
        if not self.partner_key:
            missing.append("Partner Key")
        if not self.redirect_url:
            missing.append("Redirect URL")
        if missing:
            frappe.throw("; ".join(missing) + " wajib diisi")

    # --- Whitelisted helpers ---

    @frappe.whitelist()
    def connect_to_shopee(self, scopes: list[str] | None = None):  # type: ignore[override]
        """Return authorize URL payload for initiating OAuth."""
        try:
            from shopee_bridge import auth

            scopes_list = scopes or []
            url = auth.build_authorize_url(self.partner_id, self.redirect_url, scopes_list)  # type: ignore[arg-type]
            return {"ok": True, "authorize_url": url}
        except Exception as exc:  # pragma: no cover
            self._record_auth_error(str(exc))
            return {"ok": False, "error": str(exc)}

    @frappe.whitelist()
    def oauth_callback(self, **params):  # type: ignore[override]
        """Handle OAuth callback params and store token fields (minimal)."""
        try:
            from shopee_bridge import auth

            res = auth.handle_oauth_callback(params)
            # Persist basic token data if returned
            for k in ("access_token", "refresh_token", "expire_in", "shop_id", "scopes"):
                if k in res and hasattr(self, k if k != "expire_in" else "token_expires_at"):
                    if k == "expire_in":
                        try:
                            self.token_expires_at = frappe.utils.add_to_date(frappe.utils.now(), seconds=int(res[k]))
                        except Exception:
                            pass
                    else:
                        setattr(self, k, res[k])
            self.last_auth_error = None
            self.save(ignore_permissions=True)
            return {"ok": True, "data": res}
        except Exception as exc:  # pragma: no cover
            self._record_auth_error(str(exc))
            return {"ok": False, "error": str(exc)}

    @frappe.whitelist()
    def test_shopee_connection(self):  # type: ignore[override]
        """Attempt lightweight shop info query (safe)."""
        try:
            from shopee_bridge import auth

            info = auth.get_shop_info(self.access_token, self.shop_id) if (self.access_token and self.shop_id) else {}
            return {"ok": True, "info": info}
        except Exception as exc:  # pragma: no cover
            self._record_auth_error(str(exc))
            return {"ok": False, "error": str(exc)}

    # --- Internal helpers ---
    def _record_auth_error(self, msg: str) -> None:
        try:
            self.last_auth_error = msg[:500]
            self.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(message=msg, title="Shopee Auth Error Persist Failure")
