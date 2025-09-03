from datetime import datetime, timezone
from .dispatcher import call as _call
from .finance import _create_credit_note_from_existing
from .helpers import _get_si_by_po, _get_so_by_po
from .utils import _create_or_get_customer, _date_from_epoch, _ensure_item_exists, _extract_dates_from_order, _safe_int, _settings, _short_log
from .finance import create_payment_entry_from_shopee

import time
import frappe # pyright: ignore[reportMissingImports] # pyright: ignore[reportMissingImports]
from frappe.utils import flt, cint, nowdate # pyright: ignore[reportMissingImports]


@frappe.whitelist()
def _process_order_to_so(order_sn: str):
    """Ambil detail order Shopee lalu buat Sales Order di ERPNext (dedup by po_no)."""
    s = _settings()

    # --- anti duplikat: cek SO yang punya po_no = order_sn
    existed_so = _get_so_by_po(order_sn)
    if existed_so:
        return {"status": "already_exists", "sales_order": existed_so}

    det = _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(),
        s.partner_key,
        s.shop_id,
        s.access_token,
        {
            "order_sn_list": order_sn,
            "response_optional_fields": (
                "buyer_user_id,buyer_username,recipient_address,"
                "item_list,create_time,pay_time,ship_by_date,days_to_ship,order_status"
            ),
        },
    )
    if det.get("error"):
        frappe.log_error(
            f"Failed to get order detail for {order_sn}: {det.get('message')}",
            "Shopee Order Processing",
        )
        return {"status": "error", "message": det.get("message")}

    lst = (det.get("response") or {}).get("order_list") or []
    if not lst:
        return {"status": "no_data"}
    od = lst[0]

    # Customer
    customer = _create_or_get_customer(od, order_sn)

    # Dates (unified helper)
    _dates = _extract_dates_from_order(od)
    transaction_date = _dates.get("transaction_date")
    delivery_date = _dates.get("delivery_date")

    # Build SO
    so = frappe.new_doc("Sales Order")
    so.customer = customer
    so.order_type = "Sales"
    so.transaction_date = transaction_date
    so.delivery_date = delivery_date

    # PENTING: kedua field ini HARUS diisi untuk dedup dan reference
    so.po_no = order_sn  # field standar untuk dedup
    so.custom_shopee_order_sn = order_sn  # custom field untuk tracking

    so.currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "IDR"
    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if company:
        so.company = company
    default_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
    if default_price_list:
        so.selling_price_list = default_price_list

    order_status = od.get("order_status", "UNKNOWN")
    so.remarks = f"Shopee Order {order_sn} | Status: {order_status}"

    items = od.get("item_list") or []
    if not items:
        return {"status": "no_items"}

    default_warehouse = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    for it in items:
        sku = (it.get("model_sku") or "").strip() or (it.get("item_sku") or "").strip() \
              or f"SHP-{it.get('item_id','UNKNOWN')}-{it.get('model_id','0')}"
        qty = int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased") or 1)
        raw_rate = (it.get("model_discounted_price") or it.get("model_original_price")
                    or it.get("order_price") or it.get("item_price") or "0")
        rate = float(raw_rate)
        base_name = (it.get("item_name") or "").strip()
        model_name = (it.get("model_name") or "").strip()
        item_name = (f"{base_name} - {model_name}".strip(" -") or sku)[:140]

        item_code = _ensure_item_exists(sku, it, rate)

        row = so.append("items", {})
        row.item_code = item_code
        row.item_name = item_name
        row.qty = qty
        row.rate = rate
        row.delivery_date = delivery_date
        if default_warehouse:
            row.warehouse = default_warehouse

    try:
        # Validasi field wajib
        if not so.po_no or not so.custom_shopee_order_sn:
            raise ValueError(f"Missing required fields. po_no: {so.po_no}, custom_shopee_order_sn: {so.custom_shopee_order_sn}")

        so.insert(ignore_permissions=True)
        so.submit()
        return {"status": "created", "sales_order": so.name}
    except Exception as e:
        frappe.log_error(
            f"Failed to create Sales Order for {order_sn}: {e}\n"
            f"Order detail: {frappe.as_json(od)}",
            "Sales Order Creation",
        )
        return {"status": "error", "message": str(e)}


def _process_order_to_si(order_sn: str):
    """Shopee order → Sales Invoice (+ auto Payment Entry). Dedup by po_no."""
    s = _settings()
    esc_n = {}  # pre-initialize to avoid UnboundLocalError

    # --- anti duplikat: cek SI yang punya po_no = order_sn
    existed_si = _get_si_by_po(order_sn)
    if existed_si:
        # Ambil detail order terbaru dari Shopee untuk rekonsiliasi
        det = _call(
            "/api/v2/order/get_order_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {
                "order_sn_list": order_sn,
                "response_optional_fields": (
                    "buyer_user_id,buyer_username,recipient_address,"
                    "item_list,create_time,pay_time,ship_by_date,days_to_ship,order_status"
                ),
            },
        )
        if det.get("error"):
            frappe.logger().warning(f"[Shopee] get_order_detail failed for {order_sn}: {det.get('message')}")
            return {"ok": False, "error": det.get("message")}
        orders = (det.get("response") or {}).get("order_list") or []
        if not orders:
            return {"ok": False, "error": "No order data"}
        od = orders[0]

        # Ambil escrow supaya bisa lihat refund/net
        esc_raw = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {"order_sn": order_sn}
        )
        from .finance import _normalize_escrow_payload
        esc_n = _normalize_escrow_payload(esc_raw) or {}
        refund_amount = flt(esc_n.get("refund_amount"))
        net_escrow = flt(esc_n.get("net_escrow")) if esc_n.get("net_escrow") is not None else flt(esc_n.get("net_amount"))
        net_amount = flt(esc_n.get("net_amount"))

    if existed_si:
        existing_si = frappe.get_doc("Sales Invoice", existed_si)
        order_status = (od.get("order_status") or "").upper()

    # Jika Shopee sudah cancel (explicit), coba batalkan SI & PE, buat CN bila perlu
        if order_status == "CANCELLED":
                # Cancel related Payment Entries first
                try:
                    pe_refs = frappe.get_all(
                        "Payment Entry Reference",
                        filters={"reference_doctype": "Sales Invoice", "reference_name": existing_si.name},
                        fields=["parent"]
                    ) or []
                    for r in pe_refs:
                        pe_name = r.get("parent")
                        if not pe_name:
                            continue
                        try:
                            pe = frappe.get_doc("Payment Entry", pe_name)
                            if getattr(pe, "docstatus", 0) == 1:
                                pe.cancel()
                                frappe.db.commit()
                        except Exception as e_pe:
                            frappe.log_error(f"Cancel Payment Entry {pe_name} before cancelling SI {existing_si.name} failed: {e_pe}", "Shopee SI Reconcile")
                except Exception:
                    pass

                # Create Credit Note bila ada refund atau net_escrow <= 0
                if refund_amount > 0 or net_escrow <= 0:
                    try:
                        _create_credit_note_from_existing(
                            existing_si,
                            order_sn,
                            _extract_dates_from_order(od, esc_n).get("posting_date"),
                            esc_n,
                            reason="Cancelled on Shopee"
                        )
                    except Exception as e3:
                        frappe.log_error(f"Create CN for {existing_si.name} error: {e3}", "Shopee SI Reconcile")

                # Tanda di SI & cancel
                try:
                    if hasattr(existing_si, "custom_shopee_refund_sn"):
                        existing_si.custom_shopee_refund_sn = order_sn
                    existing_si.save(ignore_permissions=True)
                    frappe.db.commit()
                except Exception:
                    pass

                try:
                    if existing_si.docstatus == 1:
                        existing_si.cancel()
                        frappe.db.commit()
                except Exception as e4:
                    frappe.log_error(f"Cancel SI {existing_si.name} error: {e4}", "Shopee SI Reconcile")

                return {"ok": True, "status": "cancelled", "sales_invoice": existing_si.name}

        # Jika masih completed, periksa apakah SI perlu di-amend (items/amount/posting_date)
        # Build desired items from Shopee data
        desired_rows = []
        default_wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")
        for it in (od.get("item_list") or []):
            sku = (it.get("model_sku") or "").strip() or (it.get("item_sku") or "").strip() or f"SHP-{it.get('item_id')}-{it.get('model_id','0')}"
            qty = _safe_int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased"), 1)
            rate = (
                flt(it.get("model_discounted_price"))
                if flt(it.get("model_discounted_price")) > 0 else
                flt(it.get("model_original_price"))
                if flt(it.get("model_original_price")) > 0 else
                flt(it.get("order_price"))
                if flt(it.get("order_price")) > 0 else
                flt(it.get("item_price"))
            )
            try:
                if rate < 1000 and rate * qty < 1000:
                    raw_orig = flt(it.get("model_original_price")) or flt(it.get("model_discounted_price"))
                    for mul in (100, 1000, 10000):
                        if abs(raw_orig - (rate * mul)) < 1:
                            rate = rate * mul
                            break
            except Exception:
                pass
            item_code = _ensure_item_exists(sku, it, rate)
            desired_rows.append({"item_code": item_code, "qty": flt(qty), "rate": flt(rate), "warehouse": default_wh})

            # Compare existing vs desired
            def _rows_to_key(rows):
                return [(r.get("item_code"), flt(r.get("qty") or r.qty if hasattr(r, 'qty') else 0), flt(r.get("rate") or r.rate if hasattr(r, 'rate') else 0)) for r in rows]

            existing_keys = _rows_to_key(existing_si.items)
            desired_keys = _rows_to_key(desired_rows)

            if existing_keys != desired_keys or str(existing_si.posting_date) != str(_extract_dates_from_order(od, esc_n).get("posting_date")):
                # Need to amend: if submitted -> cancel & recreate, else update in-place
                if existing_si.docstatus == 1:
                    # cancel related PE then SI
                    try:
                        pe_refs = frappe.get_all(
                            "Payment Entry Reference",
                            filters={"reference_doctype": "Sales Invoice", "reference_name": existing_si.name},
                            fields=["parent"]
                        ) or []
                        for r in pe_refs:
                            pe_name = r.get("parent")
                            if not pe_name:
                                continue
                            try:
                                pe = frappe.get_doc("Payment Entry", pe_name)
                                if getattr(pe, "docstatus", 0) == 1:
                                    pe.cancel()
                                    frappe.db.commit()
                            except Exception as e_pe:
                                frappe.log_error(f"Cancel Payment Entry {pe_name} failed: {e_pe}", "Shopee SI Reconcile")
                    except Exception:
                        pass
                    try:
                        existing_si.cancel()
                        frappe.db.commit()
                    except Exception as e:
                        frappe.log_error(f"Failed to cancel SI {existing_si.name} before recreation: {e}", "Shopee SI Reconcile")

                    # Create new SI to match Shopee
                    try:
                        si_new = frappe.new_doc("Sales Invoice")
                        si_new.customer = existing_si.customer
                        si_new.posting_date = _extract_dates_from_order(od, esc_n).get("posting_date")
                        si_new.set_posting_time = 1
                        si_new.update_stock = existing_si.update_stock
                        si_new.currency = existing_si.currency
                        si_new.po_no = order_sn
                        si_new.custom_shopee_order_sn = order_sn
                        si_new.company = existing_si.company
                        for r in desired_rows:
                            row = si_new.append("items", {})
                            row.item_code = r["item_code"]
                            row.qty = r["qty"]
                            row.rate = r["rate"]
                            if r.get("warehouse"):
                                row.warehouse = r.get("warehouse")
                        si_new.insert(ignore_permissions=True)
                        si_new.submit()
                        frappe.db.commit()
                    except Exception as e_new:
                        frappe.log_error(f"Failed to create replacement SI for {order_sn}: {e_new}", "Shopee SI Reconcile")
                        return {"ok": False, "error": str(e_new)}

                    # create PE if net_amount > 0
                    if net_amount > 0:
                        pe_exists = frappe.db.exists(
                            "Payment Entry Reference",
                            {"reference_doctype": "Sales Invoice", "reference_name": si_new.name}
                        )
                        if not pe_exists:
                            try:
                                pe_name = create_payment_entry_from_shopee(
                                    si_name=si_new.name,
                                    # pass raw escrow payload to preserve component fees (avoid double-normalize)
                                    escrow=esc_raw,
                                    net_amount=net_amount,
                                    order_sn=order_sn,
                                    posting_ts=_safe_int(esc_n.get("payout_time") or 0),
                                    enqueue=False
                                )
                            except Exception as e_pe2:
                                frappe.log_error(f"Failed to create PE for recreated SI {si_new.name}: {e_pe2}", "Shopee SI Reconcile")

                    return {"ok": True, "status": "amended_recreated", "sales_invoice": si_new.name}
                else:
                    # Update existing draft SI in-place
                    try:
                        existing_si.items = []
                        for r in desired_rows:
                            row = existing_si.append("items", {})
                            row.item_code = r["item_code"]
                            row.qty = r["qty"]
                            row.rate = r["rate"]
                            if r.get("warehouse"):
                                row.warehouse = r.get("warehouse")
                        existing_si.posting_date = _extract_dates_from_order(od, esc_n).get("posting_date")
                        existing_si.set_posting_time = 1
                        existing_si.save(ignore_permissions=True)
                        existing_si.submit()
                        frappe.db.commit()
                    except Exception as e_upd:
                        # fallback if submit fails due to stock: try update_stock=0
                        try:
                            existing_si.reload()
                            existing_si.update_stock = 0
                            existing_si.save()
                            existing_si.submit()
                            frappe.db.commit()
                        except Exception as e2:
                            frappe.log_error(f"Failed to update existing SI {existing_si.name}: {e2}", "Shopee SI Reconcile")
                            return {"ok": False, "error": str(e2)}

                    # Ensure PE exists
                    if net_amount > 0:
                        pe_exists = frappe.db.exists(
                            "Payment Entry Reference",
                            {"reference_doctype": "Sales Invoice", "reference_name": existing_si.name}
                        )
                        if not pe_exists:
                            try:
                                create_payment_entry_from_shopee(
                                    si_name=existing_si.name,
                                    escrow=esc_raw,
                                    net_amount=net_amount,
                                    order_sn=order_sn,
                                    posting_ts=_safe_int(esc_n.get("payout_time") or 0),
                                    enqueue=False
                                )
                            except Exception as e_pe3:
                                frappe.log_error(f"Failed to create PE for updated SI {existing_si.name}: {e_pe3}", "Shopee SI Reconcile")

                    return {"ok": True, "status": "amended", "sales_invoice": existing_si.name}

            # No changes required; ensure PE exists if needed
            if net_amount > 0:
                pe_exists = frappe.db.exists(
                    "Payment Entry Reference",
                    {"reference_doctype": "Sales Invoice", "reference_name": existing_si.name}
                )
                if not pe_exists:
                    try:
                        pe_name = create_payment_entry_from_shopee(
                            si_name=existing_si.name,
                            escrow=esc_raw,
                            net_amount=net_amount,
                            order_sn=order_sn,
                            posting_ts=_safe_int(esc_n.get("payout_time") or 0),
                            enqueue=False
                        )
                        return {"ok": True, "status": "already_exists", "sales_invoice": existing_si.name, "payment_entry": pe_name}
                    except Exception as e_pe4:
                        frappe.log_error(f"Failed to ensure PE for existing SI {existing_si.name}: {e_pe4}", "Shopee SI Reconcile")
        return {"ok": True, "status": "already_exists", "sales_invoice": existing_si.name}
    # ---- NEW SI PATH (existed_si is falsy) ----
    # ensure variables exist for new SI path (only when SI belum ada)
    refund_amount = 0.0
    net_escrow = 0.0
    net_amount = 0.0

    # --- detail order dari Shopee
    det = _call(
        "/api/v2/order/get_order_detail",
        str(s.partner_id).strip(), s.partner_key,
        s.shop_id, s.access_token,
        {
            "order_sn_list": order_sn,
            # Tambah pay_time agar bisa pakai tanggal pembayaran sebagai fallback posting_date
            "response_optional_fields": (
                "buyer_user_id,buyer_username,recipient_address,"
                "item_list,create_time,pay_time,ship_by_date,days_to_ship,order_status"
            ),
        },
    )
    if det.get("error"):
        return {"ok": False, "error": det.get("message")}
    orders = (det.get("response") or {}).get("order_list") or []
    if not orders:
        return {"ok": False, "error": "No order data"}
    od = orders[0]

    # --- migration/stock handling
    update_stock = 1
    if cint(getattr(s, "migration_mode", 0)) == 1:
        update_stock = 0
    elif getattr(s, "migration_cutoff_date", None):
        ct = od.get("create_time")
        if ct:
            order_date = datetime.fromtimestamp(int(ct)).date()
            cutoff = frappe.utils.getdate(s.migration_cutoff_date)
            if order_date < cutoff:
                update_stock = 0

    # --- customer
    customer = _create_or_get_customer(od, order_sn)

    # --- Ambil escrow lebih awal supaya dapat payout_time utk posting_date
    esc_raw = _call(
        "/api/v2/payment/get_escrow_detail",
        str(s.partner_id).strip(), s.partner_key,
        s.shop_id, s.access_token,
        {"order_sn": order_sn}
    )
    esc_resp = (esc_raw.get("response") or {}) if not esc_raw.get("error") else {}
    oi = esc_resp.get("order_income") or {}
    # Unified date extraction (will derive posting_date from payout > pay > create)
    _dates = _extract_dates_from_order(od, esc_resp)
    posting_date_shopee = _dates.get("posting_date")
    payout_ts = _safe_int((esc_resp.get("payout_time") or oi.get("payout_time")))

    # === NORMALISASI TANGGAL POSTING (HARUS SAMA DENGAN TANGGAL MASUK SHOPEE) ===
    # posting_date_shopee saat ini string 'YYYY-MM-DD'. Kita validasi & clamp jika future.
    try:
        today_str = nowdate()
        if posting_date_shopee:
            # Future date guard (kalau timezone mismatch)
            if posting_date_shopee > today_str:
                posting_date_shopee = today_str
        else:
            posting_date_shopee = today_str
    except Exception:
        posting_date_shopee = nowdate()

    # Simpan epoch untuk PE reference jika payout_ts kosong (konversi dari posting_date_shopee)
    if not payout_ts:
        try:
            # interpret posting_date_shopee as local date start-of-day UTC epoch
            dt_tmp = datetime.strptime(posting_date_shopee, "%Y-%m-%d")
            payout_ts = int(dt_tmp.replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            payout_ts = int(time.time())

    # --- build SI dengan posting_date dari Shopee
    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = posting_date_shopee
    si.set_posting_time = 1
    si.update_stock = update_stock
    si.currency = "IDR"

    # PENTING: kedua field ini HARUS diisi untuk dedup dan reference
    si.po_no = order_sn                      # field standar untuk dedup
    si.custom_shopee_order_sn = order_sn     # custom field untuk tracking (UNIQUE)

    company = frappe.db.get_single_value("Global Defaults", "default_company")
    if company:
        si.company = company
    default_wh = frappe.db.get_single_value("Stock Settings", "default_warehouse")

    for it in (od.get("item_list") or []):
        sku = (it.get("model_sku") or "").strip() \
              or (it.get("item_sku") or "").strip() \
              or f"SHP-{it.get('item_id')}-{it.get('model_id','0')}"
        qty = _safe_int(it.get("model_quantity_purchased") or it.get("variation_quantity_purchased"), 1)
        rate = (
            flt(it.get("model_discounted_price"))
            if flt(it.get("model_discounted_price")) > 0 else
            flt(it.get("model_original_price"))
            if flt(it.get("model_original_price")) > 0 else
            flt(it.get("order_price"))
            if flt(it.get("order_price")) > 0 else
            flt(it.get("item_price"))
        )
        # Normalisasi rate: beberapa toko memakai harga jutaan, hindari heuristik salah bagi
        # Jika Shopee mengirim dalam 'cents', biasanya angkanya jauh lebih kecil (misal 1100000 -> sudah rupiah)
        # Aturan: jika rate < 1000 dan ada kemungkinan total > 1e6, kita coba periksa 'price * qty * 100' dsb.
        try:
            if rate < 1000 and rate * qty < 1000:
                # cek apakah dengan mengalikan 100 atau 1000 mendekati item original price fields mentah
                raw_orig = flt(it.get("model_original_price")) or flt(it.get("model_discounted_price"))
                for mul in (100, 1000, 10000):
                    if abs(raw_orig - (rate * mul)) < 1:  # beda kurang dari Rp1 dianggap match
                        rate = rate * mul
                        break
        except Exception:
            pass

        item_code = _ensure_item_exists(sku, it, rate)

        row = si.append("items", {})
        row.item_code = item_code
        row.qty = flt(qty)
        row.rate = flt(rate)
        row.amount = flt(qty) * flt(rate)
        if default_wh:
            row.warehouse = default_wh

    if not si.items:
        # Extra diagnostic logging to understand why Shopee returned no items
        try:
            frappe.logger().warning(
                f"[Shopee SI Flow] No items for order_sn={order_sn} | raw_item_list_len={len(od.get('item_list') or [])} | detail_keys={list(od.keys())}"
            )
        except Exception:
            pass
        frappe.throw(f"Shopee order {order_sn} returned no items. Cannot create Sales Invoice.")

    # --- insert + submit (fallback no-stock)
    try:
        if not si.po_no or not si.custom_shopee_order_sn:
            raise ValueError(
                f"Missing required fields. po_no: {si.po_no}, custom_shopee_order_sn: {si.custom_shopee_order_sn}"
            )
        si.insert(ignore_permissions=True)
        si.submit()
    except Exception as e:
        # fallback: kalau gagal karena stok (string error bervariasi)
        if ("needed in" in str(e) or "insufficient stock" in str(e).lower()) and update_stock:
            si.reload()
            si.update_stock = 0
            si.remarks = f"Shopee order SN {order_sn} (Auto: No Stock)"
            si.po_no = order_sn
            si.custom_shopee_order_sn = order_sn
            si.save()
            si.submit()
        else:
            frappe.log_error(f"Create SI fail {order_sn}: {e}", "Shopee SI Flow")
            return {"ok": False, "error": str(e)}

    # === Escrow → Payment Entry ===
    # Jika escrow call awal error, esc_raw akan punya error dan kita skip PE
    if esc_raw.get("error"):
        frappe.logger().warning(f"[Shopee] escrow_detail fail {order_sn}: {esc_raw.get('message')}")
        return {"ok": True, "sales_invoice": si.name, "note": "No payment entry created"}

    from .finance import _normalize_escrow_payload
    esc_n = _normalize_escrow_payload(esc_raw)
    net_amount = flt(esc_n.get("net_amount"))
    refund_amount = flt(esc_n.get("refund_amount"))

    # --- idempotency: jika SI dg SN ini sudah ada (harusnya yg baru dibuat di atas)
    si_exists = frappe.db.exists("Sales Invoice", {"custom_shopee_order_sn": order_sn})
    if si_exists:
        existing_si = frappe.get_doc("Sales Invoice", si_exists)

        # --- CREATE CREDIT NOTE (Return) untuk existing SI jika perlu
        if refund_amount > 0 or net_amount <= 0:
            # cek apakah sudah ada CN untuk SI ini (idempotent by return_against)
            cn_exists = frappe.db.exists(
                "Sales Invoice",
                {"is_return": 1, "return_against": existing_si.name}
            )
            if not cn_exists:
                try:
                    cn = frappe.new_doc("Sales Invoice")
                    cn.customer = existing_si.customer
                    cn.posting_date = posting_date_shopee  # gunakan tanggal Shopee agar konsisten rekonsiliasi
                    cn.set_posting_time = 1
                    cn.company = existing_si.company
                    cn.currency = existing_si.currency
                    cn.update_stock = existing_si.update_stock

                    # penting untuk return
                    cn.is_return = 1
                    cn.return_against = existing_si.name

                    try:
                        # simpan referensi refund di field berbeda
                        cn.custom_shopee_refund_sn = order_sn
                    except Exception:
                        # kalau custom field belum ada, abaikan diam2
                        pass

                    # po_no untuk CN dibuat unik (hindari bentrok dedup po_no)
                    base_po = f"{order_sn}-RET"
                    cn.po_no = base_po
                    if frappe.db.exists("Sales Invoice", {"po_no": base_po}):
                        cn.po_no = f"{base_po}-{frappe.utils.random_string(4)}"

                    # Copy items dari existing SI (qty harus negatif untuk CN)
                    for item in existing_si.items:
                        cn_item = cn.append("items", {})
                        cn_item.item_code = item.item_code
                        cn_item.qty = -1 * flt(item.qty or 0)
                        cn_item.rate = item.rate
                        if item.warehouse:
                            cn_item.warehouse = item.warehouse
                    # (Shopee fees NOT added here; handled in Payment Entry deductions)
                    if hasattr(cn, "remarks"):
                        cn.remarks = (cn.remarks + "\n" if getattr(cn, 'remarks', '') else "") + "Shopee fees handled in Payment Entry"
                    # Uncheck 'Update Outstanding for Self' if field exists
                    if hasattr(cn, "update_outstanding_for_self"):
                        cn.update_outstanding_for_self = 0
                    cn.insert(ignore_permissions=True)
                    cn.submit()
                    frappe.logger().info(
                        f"[Shopee] Created Credit Note {cn.name} for {order_sn} against {existing_si.name}"
                    )
                except Exception as e:
                    frappe.log_error(
                        message=f"Failed to create Credit Note for {order_sn}: {e}",
                        title="Shopee CN Creation"
                    )

        # lanjut ke PE idempotent (kalau net > 0)
        if net_amount > 0:
            pe_exists = frappe.db.exists(
                "Payment Entry Reference",
                {"reference_doctype": "Sales Invoice", "reference_name": existing_si.name}
            )
            if not pe_exists:
                pe_name = create_payment_entry_from_shopee(
                    si_name=existing_si.name,
                    escrow=esc_raw,
                    net_amount=net_amount,
                    order_sn=order_sn,
                    # Pastikan posting_ts yang diberikan ke PE konsisten dengan SI posting_date
                    posting_ts=_safe_int(esc_n.get("payout_time") or payout_ts or 0),
                    enqueue=False
                )
                return {"ok": True, "status": "already_exists", "sales_invoice": existing_si.name, "payment_entry": pe_name}

        return {"ok": True, "status": "already_exists", "sales_invoice": existing_si.name}

    # Jika SI baru dibuat di blok sebelumnya (variabel `si`), buat CN untuk SI baru jika perlu
    if refund_amount > 0 or net_amount <= 0:
        try:
            cn_exists = frappe.db.exists(
                "Sales Invoice",
                {"is_return": 1, "return_against": si.name}
            )
            if not cn_exists:
                cn = frappe.new_doc("Sales Invoice")
                cn.customer = si.customer
                cn.posting_date = posting_date_shopee
                cn.set_posting_time = 1
                cn.company = si.company
                cn.currency = si.currency
                cn.update_stock = si.update_stock
                cn.is_return = 1
                cn.return_against = si.name
                try:
                    cn.custom_shopee_refund_sn = order_sn
                except Exception:
                    pass
                base_po = f"{order_sn}-RET"
                cn.po_no = base_po
                if frappe.db.exists("Sales Invoice", {"po_no": base_po}):
                    cn.po_no = f"{base_po}-{frappe.utils.random_string(4)}"
                for item in si.items:
                    cn_item = cn.append("items", {})
                    cn_item.item_code = item.item_code
                    cn_item.qty = -1 * flt(item.qty or 0)
                    cn_item.rate = item.rate
                    if item.warehouse:
                        cn_item.warehouse = item.warehouse
                # (Shopee fees NOT added; handled in Payment Entry deductions)
                if hasattr(cn, "remarks"):
                    cn.remarks = (cn.remarks + "\n" if getattr(cn, 'remarks', '') else "") + "Shopee fees handled in Payment Entry"
                if hasattr(cn, "update_outstanding_for_self"):
                    cn.update_outstanding_for_self = 0
                cn.insert(ignore_permissions=True)
                cn.submit()
                frappe.logger().info(f"[Shopee] Created Credit Note {cn.name} for {order_sn} against {si.name}")
        except Exception as e:
            frappe.log_error(message=f"Failed to create Credit Note for {order_sn}: {e}", title="Shopee CN Creation")

    # --- jika sampai sini, gunakan SI yang baru dibuat di atas (si)
    if net_amount > 0:
        pe_exists = frappe.db.exists(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": si.name}
        )
        if not pe_exists:
            pe_name = create_payment_entry_from_shopee(
                si_name=si.name,
                escrow=esc_raw,
                net_amount=net_amount,
                order_sn=order_sn,
                posting_ts=_safe_int(esc_n.get("payout_time") or payout_ts or 0),
                enqueue=False
            )
            return {"ok": True, "sales_invoice": si.name, "payment_entry": pe_name}

    return {"ok": True, "sales_invoice": si.name, "note": "No payment entry created"}


def _normalize_order_status(raw: str | None) -> str:
    """Normalisasi variasi status Shopee -> READY_TO_SHIP / COMPLETED / CANCELLED / OTHER.
    Shopee kadang kirim 'CANCELED' atau 'COMPLETE'."""
    if not raw:
        return "OTHER"
    r = raw.upper().strip()
    if r == "CANCELED":
        r = "CANCELLED"
    if r == "COMPLETE":
        r = "COMPLETED"
    allowed = {"READY_TO_SHIP", "COMPLETED", "CANCELLED"}
    return r if r in allowed else "OTHER"


def _handle_cancelled_order(order_sn: str) -> dict:
    """Handle alur CANCELLED:
    - Jika sudah ada SI: buat Credit Note (CN) jika belum ada & ada refund (escrow refund/net <=0)
    - Jika hanya ada SO: cancel SO
    - Jika tidak ada dokumen: no-op
    """
    result = {"status": "cancelled", "order_sn": order_sn}
    try:
        si_name = _get_si_by_po(order_sn)
        so_name = _get_so_by_po(order_sn)
        # Ambil escrow utk tahu refund
        s = _settings()
        esc_raw = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {"order_sn": order_sn}
        )
        from .finance import _normalize_escrow_payload
        esc_n = _normalize_escrow_payload(esc_raw) if not esc_raw.get("error") else {}
        refund_amount = flt(esc_n.get("refund_amount"))
        net_amount = flt(esc_n.get("net_amount"))
        # Cancel SO kalau ada
        if so_name:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                if so.docstatus == 1:
                    so.cancel()
                    result.setdefault("actions", []).append({"cancel_so": so_name})
            except Exception as e:
                frappe.log_error(f"Cancel SO {so_name} fail: {e}", "Shopee Cancel Flow")
        # Jika ada SI → delegasikan ke _process_order_to_si utk logic CN (agar satu sumber)
        if si_name:
            res_si = _process_order_to_si(order_sn)
            result["sales_invoice"] = si_name
            result["si_result"] = res_si
        else:
            # Tidak ada SI tapi ada refund? (Kemungkinan kecil) → tidak bisa buat CN tanpa SI
            if refund_amount > 0 or net_amount <= 0:
                result["note"] = "Refund detected but no Sales Invoice to credit"
        return result
    except Exception as e:
        frappe.log_error(f"Handle cancelled {order_sn} fail: {e}", "Shopee Cancel Flow")
        return {"status": "error", "error": str(e), "order_sn": order_sn}


@frappe.whitelist()
def _process_order(order_sn: str):
    """Process 1 order SN dengan rules sederhana:
    COMPLETED  -> Sales Invoice + Payment Entry
    READY_TO_SHIP -> Sales Order
    CANCELLED -> reconcile (cancel SO / create CN dari SI)
    Jika status lain: skip.
    Gunakan satu sumber fungsi: _process_order_to_so & _process_order_to_si.
    """
    s = _settings()
    try:
        # Ambil detail untuk ambil status dulu
        det = _call(
            "/api/v2/order/get_order_detail",
            str(s.partner_id).strip(), s.partner_key,
            s.shop_id, s.access_token,
            {
                "order_sn_list": order_sn,
                "response_optional_fields": (
                    "buyer_user_id,buyer_username,recipient_address,"
                    "item_list,create_time,pay_time,ship_by_date,days_to_ship,order_status"
                ),
            },
        )
        if det.get("error"):
            return {"ok": False, "error": det.get("message"), "order_sn": order_sn}
        lst = (det.get("response") or {}).get("order_list") or []
        if not lst:
            return {"ok": False, "error": "No order data", "order_sn": order_sn}
        od = lst[0]
        raw_status = od.get("order_status")
        status_norm = _normalize_order_status(raw_status)
        if status_norm == "READY_TO_SHIP":
            r = _process_order_to_so(order_sn)
            return {"ok": True, "mode": "SO", **(r or {})}
        elif status_norm == "COMPLETED":
            r = _process_order_to_si(order_sn)
            return {"ok": True, "mode": "SI", **(r or {})}
        elif status_norm == "CANCELLED":
            r = _handle_cancelled_order(order_sn)
            return {"ok": True, "mode": "CANCELLED", **(r or {})}
        else:
            return {"ok": True, "mode": "IGNORED", "status": raw_status}
    except Exception as e:
        _short_log(f"Failed to process order {order_sn}: {e}", "Shopee Order Processing")
        return {"ok": False, "error": str(e), "order_sn": order_sn}


def _find_existing_si_by_order_sn(order_sn: str) -> str | None:
    """Cari Sales Invoice existing: prioritas po_no (Customer's PO), fallback custom_shopee_order_sn."""
    if not order_sn:
        return None
    si = frappe.db.get_value("Sales Invoice", {"po_no": order_sn}, "name")
    if si:
        return si
    si_custom = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
    if si_custom:
        return si_custom
    si_purchase = frappe.db.get_value("Sales Invoice", {"purchase_order_number": order_sn}, "name")
    if si_purchase:
        return si_purchase
    return None


@frappe.whitelist()
def complete_order_to_si(order_sn: str):
    """Convert Sales Order Shopee → Sales Invoice + Payment Entry.

    1. Find Sales Order by order_sn
    2. Check for existing SI to prevent duplicates
    3. Create SI from SO with proper fields
    4. Get escrow details from Shopee API
    5. Create Payment Entry if net amount > 0
    """
    try:
        if not order_sn:
            return {"ok": False, "error": "Order SN is required"}

        # Find SO by either custom_shopee_order_sn or po_no
        so_name = frappe.db.get_value("Sales Order",
            filters=[
                ["docstatus", "=", 1],
                [
                    ["custom_shopee_order_sn", "=", order_sn],
                    ["po_no", "=", order_sn]
                ]
            ],
            fieldname="name"
        )
        if not so_name:
            return {"ok": False, "error": f"No submitted Sales Order found for {order_sn}"}

        so = frappe.get_doc("Sales Order", so_name)

        # Check for existing SI by either field to prevent duplicates
        si_name = frappe.db.get_value("Sales Invoice",
            filters=[
                ["docstatus", "=", 1],
                [
                    ["custom_shopee_order_sn", "=", order_sn],
                    ["po_no", "=", order_sn]
                ]
            ],
            fieldname="name"
        )
        if si_name:
            return {"ok": True, "status": "already_invoiced", "sales_invoice": si_name}

        # --- Tentukan posting_date dari data Shopee untuk rekonsiliasi bank ---
        # Urutan prioritas:
        # 1. payout_time (tanggal dana cair / escrow release)
        # 2. pay_time (tanggal buyer bayar)
        # 3. create_time (tanggal order dibuat)
        # 4. nowdate() fallback
        s = _settings()
        esc = _call(
            "/api/v2/payment/get_escrow_detail",
            str(s.partner_id).strip(),
            s.partner_key,
            s.shop_id,
            s.access_token,
            {"order_sn": order_sn}
        )
        escrow_resp = (esc.get("response") or {}) if not esc.get("error") else {}
        oi = escrow_resp.get("order_income") or {}
        payout_ts = _safe_int(escrow_resp.get("payout_time") or oi.get("payout_time"))
        pay_ts = _safe_int(escrow_resp.get("pay_time") or oi.get("pay_time"))
        chosen_ts = payout_ts or pay_ts
        order_ct_ts = 0
        if not chosen_ts:
            # Ambil create_time dari order detail jika perlu
            odet = _call(
                "/api/v2/order/get_order_detail",
                str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token,
                {"order_sn_list": order_sn, "response_optional_fields": "create_time"}
            )
            if not odet.get("error"):
                lst = (odet.get("response") or {}).get("order_list") or []
                if lst:
                    order_ct_ts = _safe_int(lst[0].get("create_time"))
        posting_date_shopee = _date_from_epoch(chosen_ts or order_ct_ts) if (chosen_ts or order_ct_ts) else nowdate()

        # Create SI from SO with proper defaults (gunakan tanggal Shopee)
        si = frappe.get_doc(so).make_sales_invoice()
        si.custom_shopee_order_sn = order_sn
        si.po_no = order_sn  # Ensure both fields are set
        si.posting_date = posting_date_shopee
        si.set_posting_time = 1
        si.update_stock = 0  # Prevent negative stock movement on conversion
        si.insert(ignore_permissions=True)
        si.submit()

        # Net amount dari escrow (pakai payout/escrow amount)
        net = flt(
            (escrow_resp.get("escrow_amount") or oi.get("escrow_amount") or 0)
            or (escrow_resp.get("payout_amount") or oi.get("payout_amount") or 0)
        )
        if net > 0:
            # Use centralized Payment Entry creation function
            from .finance import create_payment_entry_from_shopee
            pe_name = create_payment_entry_from_shopee(
                si_name=si.name,
                escrow=esc,
                net_amount=net,
                order_sn=order_sn,
                posting_ts=chosen_ts,
                enqueue=False
            )
            if pe_name:
                return {"ok": True, "status": "completed", "sales_invoice": si.name, "payment_entry": pe_name}

        return {"ok": True, "status": "invoiced_no_payment", "sales_invoice": si.name}

    except Exception as e:
        frappe.log_error(f"Complete order {order_sn} fail: {e}", "Shopee Complete Order")
        return {"ok": False, "error": str(e)}


@frappe.whitelist()
def cancel_order(order_sn: str):
    """Cancel SO / SI jika order Shopee dibatalkan."""
    cancelled = []

    try:
        so_name = frappe.db.get_value("Sales Order", {"custom_shopee_order_sn": order_sn}, "name")
        if so_name:
            try:
                so = frappe.get_doc("Sales Order", so_name)
                if so.docstatus == 1:
                    so.cancel()
                    cancelled.append(so.name)
            except Exception as e:
                frappe.log_error(f"Cancel SO {so_name} error: {e}")

        si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
        if si_name:
            try:
                si = frappe.get_doc("Sales Invoice", si_name)
                if si.docstatus == 1:
                    si.cancel()
                    cancelled.append(si.name)
            except Exception as e:
                frappe.log_error(f"Cancel SI {si_name} error: {e}")

        return {"ok": True, "cancelled": cancelled}

    except Exception as e:
        frappe.log_error(f"Cancel order {order_sn} fail: {e}", "Shopee Cancel Order")
        return {"ok": False, "error": str(e)}