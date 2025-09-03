from shopee_bridge.api import _norm_esc
from .dispatcher import call as _call
from .utils import _settings
from .finance import create_payment_entry_from_shopee
import frappe # pyright: ignore[reportMissingImports]

order_sn = "250722RWKPDRXA"
s = _settings()
esc = _call("/api/v2/payment/get_escrow_detail", str(s.partner_id).strip(), s.partner_key, s.shop_id, s.access_token, {"order_sn": order_sn})
print("Escrow:", esc)
esc_n = _norm_esc(esc)
print("Norm:", esc_n)
si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
print("SI:", si_name)
create_payment_entry_from_shopee(si_name=si_name, escrow=esc, net_amount=esc_n.get("net_amount"), order_sn=order_sn, posting_ts=esc_n.get("payout_time"), enqueue=False)

