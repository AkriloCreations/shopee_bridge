from typing import Any, Dict
from frappe.utils import flt, nowdate # pyright: ignore[reportMissingImports]
import datetime
import frappe # pyright: ignore[reportMissingImports]
from .helpers import _get_or_create_bank_account, _get_or_create_expense_account
from .webhook import get_shopee_return_detail, get_shopee_return_list


def _create_credit_note_from_existing(existing_si, order_sn: str, posting_date: str | None, esc_n: dict, reason: str | None = None):
    """Create a Credit Note (Sales Invoice Return) copying items from an existing SI.
    Safe to call multiple times (skips if already exists). Adds Shopee fee rows with description.
    """
    if not existing_si or not getattr(existing_si, 'name', None):
        raise ValueError("existing_si invalid")
    # Idempotent: skip if return already exists
    if frappe.db.exists("Sales Invoice", {"is_return": 1, "return_against": existing_si.name}):
        return None
    cn = frappe.new_doc("Sales Invoice")
    cn.customer = existing_si.customer
    cn.posting_date = posting_date or nowdate()
    cn.set_posting_time = 1
    cn.company = existing_si.company
    cn.currency = existing_si.currency
    cn.update_stock = existing_si.update_stock
    cn.is_return = 1
    cn.return_against = existing_si.name
    if hasattr(cn, "custom_shopee_refund_sn"):
        try:
            cn.custom_shopee_refund_sn = order_sn
        except Exception:
            pass
    base_po = f"{order_sn}-RET"
    cn.po_no = base_po if not frappe.db.exists("Sales Invoice", {"po_no": base_po}) else f"{base_po}-{frappe.utils.random_string(4)}"
    # Copy items (negative qty). If any item_code now missing (renamed/deleted), use a fallback generic item once.
    fallback_used = False
    fallback_code = frappe.db.get_value("Item", {"item_code": ("like", "SHP-FALLBACK%")}, "item_code") or None
    for item in existing_si.items:
        item_code = item.item_code
        if not frappe.db.exists("Item", item_code):
            if not fallback_used:
                if not fallback_code:
                    # create simple fallback item
                    try:
                        fb = frappe.new_doc("Item")
                        fb.item_code = f"SHP-FALLBACK-{frappe.utils.random_string(6)}"
                        fb.item_name = "Shopee Fallback Returned Item"
                        fb.is_stock_item = 0
                        fb.save(ignore_permissions=True)
                        fallback_code = fb.item_code
                    except Exception:
                        pass
                item_code = fallback_code or item.item_code
                fallback_used = True
            else:
                # skip additional missing items to avoid error
                continue
        cn_item = cn.append("items", {})
        cn_item.item_code = item_code
        cn_item.qty = -1 * flt(item.qty or 0)
        cn_item.rate = item.rate
        if item.warehouse:
            cn_item.warehouse = item.warehouse
    # Fees
    fee_keys = [
        ("seller_penalty", "Biaya Penalti Shopee"),
        ("commission_fee", "Komisi Shopee"),
        ("service_fee", "Biaya Layanan Shopee"),
        ("shipping_seller_protection_fee_amount", "Proteksi Pengiriman Shopee"),
        ("voucher_seller", "Voucher Shopee"),
        ("coin_cash_back", "Coin Cashback Shopee"),
    ]
    for key, label in fee_keys:
        fee = flt((esc_n or {}).get(key))
        if fee > 0:
            account = _get_or_create_expense_account(label)
            tax_row = cn.append("taxes", {})
            tax_row.charge_type = "Actual"
            tax_row.account_head = account
            tax_row.tax_amount = -abs(fee)
            tax_row.description = label
    if reason and hasattr(cn, "remarks"):
        cn.remarks = (cn.remarks + "\n" if getattr(cn, 'remarks', '') else "") + f"Auto CN: {reason}"
    if hasattr(cn, "update_outstanding_for_self"):
        try:
            cn.update_outstanding_for_self = 0
        except Exception:
            pass
    try:
        cn.insert(ignore_permissions=True)
        cn.submit()
        frappe.db.commit()
        return cn.name
    except Exception as e:
        # If failure due to returned item mismatch, try framework helper get_return_doc
        msg = str(e)
        if "Returned Item" in msg and "does not exist" in msg:
            try:
                from erpnext.controllers.sales_and_purchase_return import get_return_doc  # type: ignore[reportMissingImports]
                cn2 = get_return_doc(existing_si.doctype, existing_si.name)
                cn2.posting_date = posting_date or nowdate()
                cn2.set_posting_time = 1
                if hasattr(cn2, "custom_shopee_refund_sn"):
                    try:
                        cn2.custom_shopee_refund_sn = order_sn
                    except Exception:
                        pass
                # Remove rows whose item no longer exists
                cleaned = []
                for r in list(cn2.items):
                    if frappe.db.exists("Item", r.item_code):
                        cleaned.append(r)
                if cleaned:
                    cn2.items = cleaned
                else:
                    # inject single fallback row
                    fb_code = fallback_code or "SHP-FALLBACK-RET"
                    if not frappe.db.exists("Item", fb_code):
                        try:
                            fb = frappe.new_doc("Item")
                            fb.item_code = fb_code
                            fb.item_name = "Shopee Fallback Returned Item"
                            fb.is_stock_item = 0
                            fb.save(ignore_permissions=True)
                        except Exception:
                            pass
                    row = cn2.append("items", {})
                    row.item_code = fb_code
                    row.qty = -1
                    row.rate = 0
                cn2.insert(ignore_permissions=True)
                cn2.submit()
                frappe.db.commit()
                return cn2.name
            except Exception as e2:
                frappe.log_error(f"Fallback CN via get_return_doc failed for {existing_si.name}: {e2}", "Shopee CN Fallback")
        frappe.log_error(f"Create CN final failure {existing_si.name}: {e}", "Shopee CN Creation")
        raise


def _get_default_cost_center_for_si(si) -> str:
    """Cari Cost Center default (item SI → Accounts Settings → leaf company)."""
    for it in getattr(si, "items", []):
        if getattr(it, "cost_center", None):
            return it.cost_center
    cc = frappe.db.get_single_value("Accounts Settings", "default_cost_center")
    if cc: return cc
    cc = frappe.db.get_value("Cost Center", {"company": si.company, "is_group": 0}, "name")
    if cc: return cc
    frappe.throw("No Cost Center found. Set default in Accounts Settings or on Sales Invoice items.")


def _normalize_escrow_payload(payload: dict) -> dict:
    """Normalisasi payload escrow Shopee (flat / response.order_income) + flag refund."""
    frappe.logger().info(f"[Shopee Escrow Debug] Raw payload: {payload}")
    root = (payload or {}).get("response") or (payload or {})
    oi = root.get("order_income") or {}
    frappe.logger().info(f"[Shopee Escrow Debug] Order income: {oi}")

    payout_amount = flt(root.get("payout_amount") or oi.get("payout_amount"))
    escrow_amount = flt(
        oi.get("escrow_amount_after_adjustment")
        or oi.get("escrow_amount")
        or root.get("escrow_amount")
    )
    refund_amount = flt(root.get("refund_amount") or oi.get("refund_amount") or oi.get("refund_to_buyer_amount"))
    reverse_shipping_fee = flt(oi.get("reverse_shipping_fee"))
    shipping_rebate = flt(oi.get("shopee_shipping_rebate"))
    return_to_seller = flt(oi.get("return_to_seller_amount"))

    # net default + kurangi refund bila ada
    net_amount = (payout_amount or escrow_amount)
    if refund_amount > 0:
        net_amount = flt(net_amount - refund_amount)

    commission_fee = flt(oi.get("commission_fee") or oi.get("commission"))
    service_fee = flt(oi.get("service_fee") or oi.get("transaction_fee") or oi.get("seller_transaction_fee")) + flt(oi.get("credit_card_transaction_fee") or oi.get("credit_card_fee"))
    protection_fee = flt(oi.get("delivery_seller_protection_fee_premium_amount") or oi.get("protection_fee") or oi.get("shipping_seller_protection_fee"))
    shipping_fee_difference = reverse_shipping_fee - shipping_rebate
    voucher_seller = flt(oi.get("voucher_from_seller") or oi.get("voucher_seller"))
    voucher_shopee = flt(oi.get("voucher_from_shopee") or oi.get("voucher_shopee"))
    coin_cash_back = flt(oi.get("coins") or oi.get("coin_cash_back"))
    voucher_code_seller = flt(oi.get("voucher_code_seller"))
    credit_card_fee = flt(oi.get("credit_card_transaction_fee") or oi.get("credit_card_fee"))
    final_shipping_fee = flt(oi.get("final_shipping_fee"))  # can be negative; use abs later when creating PE

    payout_time = root.get("payout_time") or root.get("update_time")
    is_refund = (refund_amount > 0) or (net_amount <= 0)

    return {
        "net_amount": net_amount,
        "escrow_amount": escrow_amount,
        "payout_amount": payout_amount,
        "refund_amount": refund_amount,
        "commission_fee": commission_fee,
        "service_fee": service_fee,
        "shipping_seller_protection_fee_amount": protection_fee,
        "shipping_fee_difference": shipping_fee_difference,
        "voucher_seller": voucher_seller,
        "voucher_from_shopee": voucher_shopee,
        "coin_cash_back": coin_cash_back,
        "voucher_code_seller": voucher_code_seller,
        "credit_card_transaction_fee": credit_card_fee,
    "final_shipping_fee": final_shipping_fee,
        "payout_time": payout_time,
        "is_refund": is_refund,
        "return_to_seller_amount": return_to_seller,
        "reverse_shipping_fee": reverse_shipping_fee,
        "shipping_rebate": shipping_rebate,
    }


def create_refund_journal_from_shopee(si_name: str, norm_payload: dict, order_sn: str) -> str | None:
    """Create a Credit Note to record refund when net <= 0 or payout negative.

    The CN will post as return against the original SI, reducing its outstanding.

    Returns: CN name or None
    """
    try:
        si = frappe.get_doc("Sales Invoice", si_name)
    except Exception:
        return None

    refund_amount = flt(norm_payload.get("refund_amount") or 0)
    net = flt(norm_payload.get("net_amount") or 0)
    escrow = flt(norm_payload.get("escrow_amount") or 0)
    amount = refund_amount if refund_amount > 0 else max(0.0, escrow - net)
    if amount <= 0:
        amount = abs(net) or 0.0
    if amount <= 0:
        return None

    # Check if CN already exists
    cn_exists = frappe.db.exists("Sales Invoice", {"return_against": si_name, "is_return": 1})
    if cn_exists:
        return cn_exists

    try:
        cn = frappe.new_doc("Sales Invoice")
        cn.customer = si.customer
        cn.posting_date = frappe.utils.nowdate()
        cn.set_posting_time = 1
        cn.company = si.company
        cn.currency = si.currency
        cn.update_stock = 0
        cn.is_return = 1
        cn.return_against = si_name
        try:
            cn.custom_shopee_refund_sn = order_sn
        except Exception:
            pass
        base_po = f"{order_sn}-RET"
        cn.po_no = base_po if not frappe.db.exists("Sales Invoice", {"po_no": base_po}) else f"{base_po}-{frappe.utils.random_string(4)}"

        # Copy items from original SI with negative qty
        for item in si.items:
            cn_item = cn.append("items", {})
            cn_item.item_code = item.item_code
            cn_item.qty = -1 * flt(item.qty or 0)
            cn_item.rate = item.rate
            if item.warehouse:
                cn_item.warehouse = item.warehouse

        # Add Shopee fees as tax/charge rows if any
        extra_fees = [
            ("commission_fee", "Komisi Shopee"),
            ("service_fee", "Biaya Layanan Shopee"),
            ("shipping_seller_protection_fee_amount", "Proteksi Pengiriman Shopee"),
            ("voucher_seller", "Voucher Seller Shopee"),
            ("coin_cash_back", "Coin Cashback Shopee"),
        ]
        for key, name in extra_fees:
            fee = flt(norm_payload.get(key))
            if fee > 0:
                account = _get_or_create_expense_account(name)
                tax_row = cn.append("taxes", {})
                tax_row.charge_type = "Actual"
                tax_row.account_head = account
                tax_row.tax_amount = -abs(fee)

        # Ensure update_outstanding_for_self is unchecked
        if hasattr(cn, "update_outstanding_for_self"):
            cn.update_outstanding_for_self = 1

        cn.insert(ignore_permissions=True)
        cn.submit()
        frappe.db.commit()
        frappe.logger().info(f"[Shopee] Created Credit Note {cn.name} for refund {order_sn} against {si_name}")
        return cn.name
    except Exception as e:
        frappe.log_error(f"Failed to create refund CN for {order_sn}: {e}", "Shopee Refund CN")
        return None


def create_payment_entry_from_shopee(si_name: str, escrow: dict, net_amount: float,
                                     order_sn: str, posting_ts: int | None = None, enqueue: bool = True):
    """Create a Payment Entry reflecting Shopee escrow payout and fees.

    Logic:
      - paid_amount = Sales Invoice grand_total
      - received_amount = escrow net (payout after fees)
      - deductions rows = individual Shopee fees (commission, service, etc.)
      - reference_date & posting_date = payout date (uang masuk)
    """
    # Defensive: SI must exist. If missing and we have order_sn, try to build via complete_order_to_si.
    if not si_name:
        try:
            frappe.logger().warning(f"[PE Debug] {order_sn}: si_name missing → attempting auto SI creation via complete_order_to_si")
            from .orders import complete_order_to_si  # local import to avoid cycles at module import
            res = complete_order_to_si(order_sn)
            if isinstance(res, dict):
                si_name = res.get("sales_invoice") or si_name
                if not si_name and res.get("status") == "already_invoiced":
                    si_name = res.get("sales_invoice")
        except Exception as auto_e:
            frappe.logger().error(f"[PE Debug] {order_sn}: auto create SI failed: {auto_e}")
    if not si_name:
        frappe.logger().error(f"[PE Debug] {order_sn}: abort PE, SI still missing after attempt")
        return None
    # Pre-initialize to avoid UnboundLocalError if an early reference happens on failure paths
    esc_n = {}
    try:
        si = frappe.get_doc("Sales Invoice", si_name)

        # Accept either raw escrow payload (with nested response/order_income) OR an already-normalized dict
        if escrow and ("net_amount" in escrow and any(k in escrow for k in ("commission_fee", "service_fee"))):
            esc_n = escrow  # already normalized
        else:
            esc_n = _normalize_escrow_payload(escrow) or {}
        actual_net = flt(esc_n.get("net_amount") or esc_n.get("payout_amount") or net_amount)

        if actual_net <= 0:
            # Use Shopee return API to fetch return details for this order
            return_list = get_shopee_return_list(page_size=50, status="REFUND")
            return_sn = None
            if return_list and isinstance(return_list, dict):
                for ret in return_list.get("response", {}).get("return_list", []):
                    if ret.get("order_sn") == order_sn:
                        return_sn = ret.get("return_sn")
                        break
            return_detail = None
            if return_sn:
                return_detail = get_shopee_return_detail(return_sn)
                frappe.logger().info(f"[PE Debug] Shopee return detail for {order_sn}: {return_detail}")
            # Use normalized payload for refund journal
            norm_payload = esc_n.copy()
            if return_detail and isinstance(return_detail, dict):
                norm_payload.update(return_detail.get("response", {}))
            # Create Credit Note (Sales Invoice Return) linked to SI
            cn_name = create_refund_journal_from_shopee(si_name, norm_payload, order_sn)
            if cn_name:
                frappe.logger().info(f"[PE Debug] Created Credit Note {cn_name} for refund/return {order_sn}")
                return cn_name
            frappe.logger().info(f"[PE Debug] {order_sn}: skip PE (actual_net={actual_net}) - refund CN not created")
            return None

        # Determine payout timestamp (tanggal uang masuk). Prioritization:
        # release_time > complete_time > payout_time (normalized/raw) > provided posting_ts > update_time
        raw = escrow if isinstance(escrow, dict) else {}
        raw_release = raw.get("release_time") or raw.get("escrow_release_time")
        raw_complete = raw.get("complete_time")
        raw_payout = raw.get("payout_time") or esc_n.get("payout_time")
        raw_update = raw.get("update_time") or esc_n.get("update_time")
        order_provided = posting_ts
        ordered = [raw_release, raw_complete, raw_payout, order_provided, raw_update]
        payout_ts = None
        for cand in ordered:
            try:
                if cand and int(cand) > 0:
                    payout_ts = int(cand)
                    break
            except Exception:
                continue
        debug_times = {
            "release_time": raw_release,
            "complete_time": raw_complete,
            "payout_time": raw_payout,
            "arg_posting_ts": order_provided,
            "update_time": raw_update,
            "chosen": payout_ts,
        }
        frappe.logger().info(f"[PE DateDebug] {order_sn}: times={debug_times}")
        if payout_ts:
            try:
                posting_date = datetime.datetime.fromtimestamp(payout_ts).date().isoformat()
            except Exception:
                posting_date = nowdate()
        else:
            # Fallback: gunakan tanggal Sales Invoice (permintaan user) kalau tidak ada payout ts
            si_fallback = str(getattr(si, "posting_date", "") or "")
            if not si_fallback:
                posting_date = nowdate()
                frappe.logger().info(f"[PE DateDebug] {order_sn}: fallback to today (SI missing posting_date)")
            else:
                posting_date = si_fallback
                frappe.logger().info(f"[PE DateDebug] {order_sn}: using SI posting_date fallback {posting_date}")
        if posting_date > nowdate():
            posting_date = nowdate()

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = si.customer
        pe.company = si.company
        pe.posting_date = posting_date
        pe.reference_no = order_sn
        # Ensure reference_date ALWAYS matches payout posting_date
        pe.reference_date = posting_date
        pe.remarks = f"Shopee Order {order_sn} Payment (Auto-created)"

        receivable = (frappe.db.get_value("Company", si.company, "default_receivable_account") or
                       getattr(si, "debit_to", None) or
                       frappe.db.get_value("Account", {"company": si.company, "account_type": "Receivable"}, "name"))
        bank_acc = _get_or_create_bank_account("Shopee (Escrow)")
        if not receivable or not bank_acc:
            frappe.logger().error(f"[PE Debug] {order_sn}: missing accounts receivable={receivable} bank={bank_acc}")
            return None
        pe.paid_from = receivable
        pe.paid_to = bank_acc
        pe.mode_of_payment = "Shopee"

        gross_amount = flt(si.grand_total)
        pe.paid_amount = gross_amount
        pe.received_amount = actual_net

        ref = pe.append("references", {})
        ref.reference_doctype = "Sales Invoice"
        ref.reference_name = si.name
        ref.allocated_amount = gross_amount

        # Gather deductions
        components = [
            ("commission_fee", "Komisi Shopee"),
            ("service_fee", "Biaya Layanan Shopee"),
            ("shipping_seller_protection_fee_amount", "Proteksi Pengiriman Shopee"),
            ("shipping_fee_difference", "Selisih Ongkir Shopee"),
            ("voucher_seller", "Voucher Shopee (Seller)"),
            ("coin_cash_back", "Coin Cashback Shopee"),
        ]
        try:
            deductions_cc = _get_default_cost_center_for_si(si)
        except Exception:
            deductions_cc = None
        for key, label in components:
            amt = flt(esc_n.get(key) or 0)
            if amt > 0:
                acc = _get_or_create_expense_account(label)
                if not acc:
                    continue
                row = pe.append("deductions", {})
                row.account = acc
                row.amount = amt
                row.description = f"{label} - {order_sn}"
                if deductions_cc and hasattr(row, "cost_center"):
                    row.cost_center = deductions_cc

        # Explicit shipping fee (final_shipping_fee) – Shopee sometimes sends negative value meaning cost to seller
        try:
            fsf = esc_n.get("final_shipping_fee")
            if fsf is not None and flt(fsf) != 0:
                shipping_cost = abs(flt(fsf))  # treat as positive deduction
                acc_ship = _get_or_create_expense_account("Biaya Ongkir Shopee")
                if acc_ship and shipping_cost > 0:
                    row = pe.append("deductions", {})
                    row.account = acc_ship
                    row.amount = shipping_cost
                    row.description = f"Biaya Ongkir Shopee (final_shipping_fee) - {order_sn}"
                    if deductions_cc and hasattr(row, "cost_center"):
                        row.cost_center = deductions_cc
        except Exception:
            pass

        expected = flt(gross_amount - actual_net)
        total_deduct = sum(flt(d.amount) for d in pe.deductions)
        diff = flt(expected - total_deduct)
        if abs(diff) > 1:
            acc = _get_or_create_expense_account("Biaya Shopee Lainnya")
            if acc:
                row = pe.append("deductions", {})
                row.account = acc
                row.amount = diff
                row.description = f"Penyesuaian Biaya Shopee - {order_sn}"
                if deductions_cc and hasattr(row, "cost_center"):
                    row.cost_center = deductions_cc
                total_deduct += diff

        calc_received = flt(gross_amount - total_deduct)
        if abs(calc_received - actual_net) > 1:
            frappe.logger().warning(f"[PE Debug] {order_sn}: mismatch calc_received={calc_received} net={actual_net}")

        pe.insert(ignore_permissions=True)
        pe.submit()
        frappe.logger().info(f"[PE Debug] Created PE {pe.name} for {order_sn} gross={gross_amount} net={actual_net} deductions={total_deduct}")
        return pe.name
    except Exception as e:
        try:
            esc_dump = frappe.as_json(escrow)[:500]
        except Exception:
            esc_dump = str(escrow)[:500]
        frappe.log_error(f"PE creation failed {order_sn}: {e}\nEscrow: {esc_dump}", "Shopee PE Error")
        return None


def _find_so_by_sn(order_sn: str) -> Dict[str, Any]:
    """Find Sales Order by order_sn, checking both po_no and custom_shopee_order_sn fields."""
    if not order_sn:
        return {"exists": False}

    # Check po_no
    so_po = frappe.db.get_value("Sales Order", {"po_no": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_po:
        return {"exists": True, "name": so_po.name, "po_no": so_po.po_no, "custom_shopee_order_sn": so_po.custom_shopee_order_sn, "modified": so_po.modified, "match_field": "po_no"}

    # Check custom_shopee_order_sn
    so_custom = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_custom:
        return {"exists": True, "name": so_custom.name, "po_no": so_custom.po_no, "custom_shopee_order_sn": so_custom.custom_shopee_order_sn, "modified": so_custom.modified, "match_field": "custom_shopee_order_sn"}

    # Check purchase_order_number (if present)
    so_purchase = frappe.db.get_value("Sales Order", {"purchase_order_number": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if so_purchase:
        return {"exists": True, "name": so_purchase.name, "po_no": so_purchase.po_no, "custom_shopee_order_sn": so_purchase.custom_shopee_order_sn, "modified": so_purchase.modified, "match_field": "purchase_order_number"}

    return {"exists": False}


def _find_si_by_sn(order_sn: str) -> Dict[str, Any]:
    """Find Sales Invoice by order_sn, checking both po_no and custom_shopee_order_sn fields."""
    if not order_sn:
        return {"exists": False}

    # Check po_no
    si_po = frappe.db.get_value("Sales Invoice", {"po_no": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_po:
        return {"exists": True, "name": si_po.name, "po_no": si_po.po_no, "custom_shopee_order_sn": si_po.custom_shopee_order_sn, "modified": si_po.modified, "match_field": "po_no"}

    # Check custom_shopee_order_sn
    si_custom = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_custom:
        return {"exists": True, "name": si_custom.name, "po_no": si_custom.po_no, "custom_shopee_order_sn": si_custom.custom_shopee_order_sn, "modified": si_custom.modified, "match_field": "custom_shopee_order_sn"}

    # Check purchase_order_number (if present)
    si_purchase = frappe.db.get_value("Sales Invoice", {"purchase_order_number": order_sn}, ["name", "po_no", "custom_shopee_order_sn", "modified"], as_dict=True)
    if si_purchase:
        return {"exists": True, "name": si_purchase.name, "po_no": si_purchase.po_no, "custom_shopee_order_sn": si_purchase.custom_shopee_order_sn, "modified": si_purchase.modified, "match_field": "purchase_order_number"}

    return {"exists": False}