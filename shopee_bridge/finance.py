# --- imports (lengkapi jika modulmu belum mengimpornya) ---
import time, hmac, hashlib, requests, frappe, re  # pyright: ignore[reportMissingImports]
from frappe.utils import get_url, flt, nowdate, cint, add_days, now, format_datetime, get_system_timezone, convert_utc_to_system_timezone, formatdate # pyright: ignore[reportMissingImports]
from datetime import datetime, timedelta, timezone
import json

# --------------------------------------------
# K O N S T A N T A
# --------------------------------------------
LOCK_ERRORS = (
    "deadlock", "lock wait timeout", "locked", "1213", "1205",
    "could not obtain lock", "too many connections"
)
def _settings():
    return frappe.get_single("Shopee Settings")

def _safe_int(v, d=0):
    """Convert value to int with fallback default."""
    try:
        return int(v) if v not in (None, "") else d
    except Exception:
        return d
    
def _date_iso_from_epoch(ts: int | None) -> str:
    """Epoch detik → 'YYYY-MM-DD' (UTC baseline, cukup untuk tanggal dokumen)."""
    if not ts:
        return frappe.utils.nowdate()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()

def _safe_flt(v, d=0.0):
    """Convert value to float with fallback default."""
    try:
        return float(v) if v not in (None, "") else d
    except Exception:
        return d

def _get_or_create_account(account_name, account_type):
    """Get or create account."""
    """Get or create account."""
    if frappe.db.exists("Account", {"account_name": account_name}):
        return account_name
    
    try:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        
        account = frappe.new_doc("Account")
        account.account_name = account_name
        account.account_type = account_type
        account.company = company
        
        # Set parent account based on type
        if account_type == "Bank":
            account.parent_account = "Bank Accounts - " + frappe.db.get_value("Company", company, "abbr")
        elif account_type == "Expense Account":
            account.parent_account = "Expenses - " + frappe.db.get_value("Company", company, "abbr")
        
        account.insert(ignore_permissions=True)
        return account_name
    except Exception as e:
        frappe.log_error(f"Failed to create account {account_name}: {str(e)}", "Shopee Account Creation")
        # Return a default account
        return frappe.db.get_single_value("Accounts Settings", "default_cash_account")

    
def create_payment_entry_from_shopee(
    si_name: str,
    escrow: dict,
    net_amount: float,
    order_sn: str,
    posting_ts: int | None = None,   # epoch dari Shopee (opsional)
    enqueue: bool = False
) -> str | None:
    """
    Buat Payment Entry untuk Sales Invoice 'si_name' berdasarkan escrow Shopee.
    - Sales Invoice tetap nilai GROSS
    - Payment Entry menerima NET, fee-fee Shopee masuk ke 'deductions'
    Return: nama Payment Entry
    """
    if enqueue:
        return frappe.enqueue(
            "my_app.my_module.shopee_finance.create_payment_entry_from_shopee",
            queue="short",
            job_name=f"PE Shopee {order_sn}",
            si_name=si_name,
            escrow=escrow,
            net_amount=net_amount,
            order_sn=order_sn,
            posting_ts=posting_ts,
            enqueue=False
        )

    pe_name = None
    try:
        si = frappe.get_doc("Sales Invoice", si_name)
        if si.docstatus != 1:
            frappe.throw(f"Sales Invoice {si.name} belum Submit.")

        # akun asal/tujuan
        paid_from = si.debit_to  # A/R dari SI
        # buat / ambil akun escrow bank
        paid_to = _get_or_create_account("Shopee (Escrow)", "Bank")

        # mode of payment
        mop = _get_or_create_mode_of_payment("Shopee")

        # --- fees (gunakan helper _safe_flt) ---
        f = _safe_flt
        fees = {
            "commission": f(escrow.get("commission_fee") or escrow.get("seller_commission_fee")),
            "service": f(escrow.get("service_fee") or escrow.get("seller_service_fee")),
            "protection": f(escrow.get("shipping_seller_protection_fee_amount")),
            "shipdiff": f(escrow.get("shipping_fee_difference")),
            "voucher": f(escrow.get("voucher_seller"))
                       + f(escrow.get("coin_cash_back"))
                       + f(escrow.get("voucher_code_seller")),
        }

        # map akun biaya
        acc = {
            "commission": _get_or_create_account("Komisi Shopee", "Expense"),
            "service": _get_or_create_account("Biaya Layanan Shopee", "Expense"),
            "protection": _get_or_create_account("Proteksi Pengiriman Shopee", "Expense"),
            "shipdiff": _get_or_create_account("Selisih Ongkir Shopee", "Expense"),
            "voucher": _get_or_create_account("Voucher Shopee", "Expense"),
        }

        net = _safe_flt(net_amount)
        gross = net + sum(v for v in fees.values() if v > 0)

        posting_date = _date_iso_from_epoch(_safe_int(posting_ts))  # YYYY-MM-DD

        # biaya pusat/lokasi
        cost_center = (si.items and si.items[0].get("cost_center")) or \
                      frappe.db.get_value("Company", si.company, "cost_center")

        # build PE
        pe = frappe.new_doc("Payment Entry")
        pe.company = si.company
        pe.payment_type = "Receive"
        pe.mode_of_payment = mop
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.posting_date = posting_date
        pe.reference_no = order_sn
        pe.reference_date = posting_date
        pe.paid_from = paid_from           # A/R
        pe.paid_to = paid_to               # Shopee (Escrow)
        pe.paid_amount = flt(net)
        pe.received_amount = flt(net)

        # alokasikan ke SI sebesar outstanding atau gross (mana yg lebih kecil)
        alloc = min(gross, flt(si.outstanding_amount or gross))
        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = alloc

        # deductions untuk tiap fee (positif saja)
        for k, v in fees.items():
            if v and v > 0:
                row = pe.append("deductions", {})
                row.account = acc[k]
                row.cost_center = cost_center
                row.amount = flt(v)

        # insert + submit dengan retry (pakai util kamu)
        pe = _insert_submit_with_retry(pe)
        pe_name = pe.name

        frappe.logger().info(
            f"[Shopee] PE {pe.name} created for SI {si.name} | order={order_sn} | net={net} | fees={fees}"
        )
        return pe_name

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee PE Creation Error")
        raise


# -------------------------------------------------------------
# 2) Webhook endpoint (pakai helper _settings dan _sign)
# -------------------------------------------------------------
def _verify_webhook_signature(raw_body: bytes) -> bool:
    """
    Verifikasi sederhana: sebagian webhook Shopee menandatangani raw body
    dengan HMAC-SHA256(partner_key). Jika versimu butuh base string lain,
    cukup ganti implementasi ini — _sign() sudah kamu sediakan.
    """
    s = _settings()
    partner_key = (getattr(s, "partner_key", None) or "").strip()
    if not partner_key:
        return False

    # ambil signature dari header umum
    headers = {k: v for k, v in (frappe.request.headers or {}).items()}
    sig = headers.get("X-Shopee-Signature") or headers.get("x-shopee-signature") \
          or headers.get("Authorization") or headers.get("authorization")
    if not sig:
        return False

    calc = hmac.new(partner_key.encode(), raw_body, hashlib.sha256).hexdigest()
    return sig.lower() == calc.lower()

def _get_or_create_mode_of_payment(mode_name):
    """Get or create mode of payment."""
    if frappe.db.exists("Mode of Payment", mode_name):
        return mode_name
    
    try:
        mode = frappe.new_doc("Mode of Payment")
        mode.mode_of_payment = mode_name
        mode.type = "Electronic"
        mode.insert(ignore_permissions=True)
        return mode_name
    except Exception as e:
        frappe.log_error(f"Failed to create mode of payment {mode_name}: {str(e)}", "Shopee Mode of Payment Creation")
        return "Cash"  # Fallback to default


def _find_si_by_order_sn(order_sn: str) -> str | None:
    # sesuaikan field link order_sn milikmu
    return frappe.db.get_value(
        "Sales Invoice",
        {"custom_shopee_order_sn": order_sn},
        "name"
    )


@frappe.whitelist(allow_guest=True)
def shopee_webhook():
    """
    Set di Shopee Partner Console:
    https://<domain>/api/method/my_app.my_module.shopee_finance.shopee_webhook
    Menangani: order_status_update, payment_update/escrow_settled.
    """
    try:
        raw = frappe.request.data or b""
        data = frappe.parse_json(raw) if raw else (frappe.local.form_dict or {})
        event = (data.get("event") or "").strip()

        # (opsional tapi dianjurkan) validasi signature
        if not _verify_webhook_signature(raw):
            frappe.log_error("Invalid Shopee signature", "Shopee Webhook")
            return {"success": False, "error": "invalid_signature"}

        if event == "order_status_update":
            # taruh logika update SO/DN jika diperlukan
            frappe.logger().info(f"[Shopee] order_status_update: {data.get('order_sn')}")

        elif event in ("payment_update", "escrow_settled", "payout"):
            order_sn = data.get("order_sn")
            si_name = _find_si_by_order_sn(order_sn)
            if not si_name:
                frappe.logger().info(f"[Shopee] no SI for order {order_sn}, skip PE")
                return {"success": True, "message": "no_invoice"}

            net = _safe_flt(data.get("escrow_amount") or data.get("payout_amount"))
            posting_ts = _safe_int(data.get("payout_time") or data.get("update_time"))

            # bikin Payment Entry (enqueue biar non-blocking)
            create_payment_entry_from_shopee(
                si_name=si_name,
                escrow=data,
                net_amount=net,
                order_sn=order_sn,
                posting_ts=posting_ts,
                enqueue=True
            )

        else:
            frappe.logger().info(f"[Shopee] unhandled event: {event}")

        return {"success": True}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Webhook Exception")
        return {"success": False, "error": "server_error"}

def _insert_submit_with_retry(doc, max_tries=3, sleep_base=1.0):
    """
    Insert + submit dengan retry saat kena lock/deadlock.
    Gunakan utk SO/DN/SI.
    """
    last_err = None
    for i in range(max_tries):
        try:
            doc.insert(ignore_permissions=True)
            # commit kecil setelah insert agar kunci cepat lepas
            frappe.db.commit()
            doc.submit()
            frappe.db.commit()
            return doc
        except Exception as e:
            msg = str(e)
            last_err = e
            if any(k.lower() in msg.lower() for k in LOCK_ERRORS):
                frappe.db.rollback()
                time.sleep(sleep_base * (i + 1))  # backoff
                continue
            # error lain: lempar
            raise
    # kalau mentok retry, lempar error terakhir
    raise last_err
