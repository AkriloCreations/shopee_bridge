#!/usr/bin/env python3
"""
Debug script untuk menganalisis mengapa Payment Entry tidak terbuat saat sync_orders_range
"""

def debug_sync_pe_issues(order_sn_list=None, time_from=None, time_to=None):
    """
    Debug lengkap untuk masalah PE tidak terbuat saat sync_orders_range
    """
    try:
        import frappe
        from shopee_bridge.api import _settings, _call, _safe_int, _norm_esc
        from shopee_bridge.webhook import _normalize_escrow_payload, create_payment_entry_from_shopee
        from frappe.utils import flt

        s = _settings()
        if not s.access_token:
            return {"error": "No access token found"}

        # Jika tidak ada order_sn_list, ambil dari recent COMPLETED orders
        if not order_sn_list:
            if not time_from or not time_to:
                import time
                time_to = int(time.time())
                time_from = time_to - (7 * 24 * 3600)  # 7 hari terakhir

            resp = _call(
                "/api/v2/order/get_order_list",
                str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token,
                {
                    "time_range_field": "update_time",
                    "time_from": time_from,
                    "time_to": time_to,
                    "page_size": 20,
                    "order_status": "COMPLETED"
                }
            )

            if resp.get("error"):
                return {"error": f"Failed to get order list: {resp.get('message')}"}

            orders = resp.get("response", {}).get("order_list", [])
            order_sn_list = [o.get("order_sn") for o in orders if o.get("order_sn")]

        if not order_sn_list:
            return {"error": "No COMPLETED orders found"}

        results = []
        for order_sn in order_sn_list[:10]:  # Batasi 10 order untuk debugging
            result = {
                "order_sn": order_sn,
                "issues": [],
                "recommendations": []
            }

            # 1. Check apakah SI ada
            si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
            if not si_name:
                result["issues"].append("Sales Invoice tidak ditemukan")
                result["recommendations"].append("Jalankan manual_sync_order untuk membuat SI")
                results.append(result)
                continue

            result["sales_invoice"] = si_name

            # 2. Check apakah PE sudah ada
            pe_exists = frappe.db.exists(
                "Payment Entry Reference",
                {"reference_doctype": "Sales Invoice", "reference_name": si_name}
            )

            if pe_exists:
                pe_ref = frappe.get_doc("Payment Entry Reference", {
                    "reference_doctype": "Sales Invoice",
                    "reference_name": si_name
                })
                result["payment_entry"] = pe_ref.parent
                result["issues"].append("Payment Entry sudah ada")
                results.append(result)
                continue

            # 3. Check escrow data
            esc = _call(
                "/api/v2/payment/get_escrow_detail",
                str(s.partner_id).strip(), s.partner_key,
                s.shop_id, s.access_token,
                {"order_sn": order_sn}
            )

            if esc.get("error"):
                result["issues"].append(f"Escrow API error: {esc.get('message')}")
                result["recommendations"].append("Check Shopee API connection dan order_sn validity")
                results.append(result)
                continue

            # 4. Normalize escrow data
            esc_norm = _normalize_escrow_payload(esc)
            net_amount = flt(esc_norm.get("net_amount"))

            if net_amount <= 0:
                result["issues"].append(f"Net amount <= 0: {net_amount}")
                result["recommendations"].append("Order mungkin refund atau belum settled")
                results.append(result)
                continue

            result["net_amount"] = net_amount
            result["escrow_data"] = {
                "commission_fee": esc_norm.get("commission_fee"),
                "service_fee": esc_norm.get("service_fee"),
                "payout_time": esc_norm.get("payout_time")
            }

            # 5. Coba buat PE
            try:
                pe_name = create_payment_entry_from_shopee(
                    si_name=si_name,
                    escrow=esc_norm,
                    net_amount=net_amount,
                    order_sn=order_sn,
                    posting_ts=_safe_int(esc_norm.get("payout_time")),
                    enqueue=False
                )

                if pe_name:
                    result["payment_entry"] = pe_name
                    result["recommendations"].append("Payment Entry berhasil dibuat")
                else:
                    result["issues"].append("create_payment_entry_from_shopee mengembalikan None")
                    result["recommendations"].append("Check Chart of Accounts setup dan akun expense")

            except Exception as e:
                result["issues"].append(f"PE creation error: {str(e)}")
                result["recommendations"].append("Check error logs untuk detail lebih lanjut")

            results.append(result)

        return {
            "success": True,
            "debugged_orders": len(results),
            "results": results,
            "summary": {
                "total_orders": len(results),
                "with_si": len([r for r in results if "sales_invoice" in r]),
                "with_pe": len([r for r in results if "payment_entry" in r]),
                "with_issues": len([r for r in results if r["issues"]]),
                "common_issues": _summarize_issues(results)
            }
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "traceback": frappe.get_traceback()
        }

def _summarize_issues(results):
    """Summarize common issues found"""
    issues_count = {}
    for result in results:
        for issue in result["issues"]:
            issues_count[issue] = issues_count.get(issue, 0) + 1

    return sorted(issues_count.items(), key=lambda x: x[1], reverse=True)

def fix_missing_payment_entries(order_sn_list=None):
    """
    Fix missing Payment Entries untuk orders yang sudah ada SI-nya
    """
    try:
        import frappe
        from shopee_bridge.api import _settings, _call, _safe_int
        from shopee_bridge.webhook import _normalize_escrow_payload, create_payment_entry_from_shopee
        from frappe.utils import flt

        s = _settings()
        if not s.access_token:
            return {"error": "No access token found"}

        # Get orders yang perlu diperbaiki
        if not order_sn_list:
            # Cari SI yang outstanding_amount > 0 dan punya custom_shopee_order_sn
            sis = frappe.get_all(
                "Sales Invoice",
                filters={
                    "docstatus": 1,
                    "outstanding_amount": [">", 0],
                    "custom_shopee_order_sn": ["!=", ""]
                },
                fields=["name", "custom_shopee_order_sn", "outstanding_amount"],
                limit=50
            )
            order_sn_list = [si.custom_shopee_order_sn for si in sis]

        if not order_sn_list:
            return {"message": "No orders found that need Payment Entry"}

        fixed = []
        skipped = []
        errors = []

        for order_sn in order_sn_list:
            try:
                # Check SI
                si_name = frappe.db.get_value("Sales Invoice", {"custom_shopee_order_sn": order_sn}, "name")
                if not si_name:
                    skipped.append({"order_sn": order_sn, "reason": "No SI found"})
                    continue

                # Check existing PE
                pe_exists = frappe.db.exists(
                    "Payment Entry Reference",
                    {"reference_doctype": "Sales Invoice", "reference_name": si_name}
                )
                if pe_exists:
                    skipped.append({"order_sn": order_sn, "reason": "PE already exists"})
                    continue

                # Get escrow
                esc = _call(
                    "/api/v2/payment/get_escrow_detail",
                    str(s.partner_id).strip(), s.partner_key,
                    s.shop_id, s.access_token,
                    {"order_sn": order_sn}
                )

                if esc.get("error"):
                    errors.append({"order_sn": order_sn, "error": f"Escrow API: {esc.get('message')}"})
                    continue

                # Normalize dan create PE
                esc_norm = _normalize_escrow_payload(esc)
                net_amount = flt(esc_norm.get("net_amount"))

                if net_amount <= 0:
                    skipped.append({"order_sn": order_sn, "reason": f"Net amount <= 0: {net_amount}"})
                    continue

                pe_name = create_payment_entry_from_shopee(
                    si_name=si_name,
                    escrow=esc_norm,
                    net_amount=net_amount,
                    order_sn=order_sn,
                    posting_ts=_safe_int(esc_norm.get("payout_time")),
                    enqueue=False
                )

                if pe_name:
                    fixed.append({
                        "order_sn": order_sn,
                        "sales_invoice": si_name,
                        "payment_entry": pe_name,
                        "net_amount": net_amount
                    })
                else:
                    errors.append({"order_sn": order_sn, "error": "PE creation returned None"})

            except Exception as e:
                errors.append({"order_sn": order_sn, "error": str(e)})

        return {
            "success": True,
            "fixed": fixed,
            "skipped": skipped,
            "errors": errors,
            "summary": {
                "total_processed": len(order_sn_list),
                "fixed_count": len(fixed),
                "skipped_count": len(skipped),
                "error_count": len(errors)
            }
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "traceback": frappe.get_traceback()
        }

# Usage instructions
USAGE_INSTRUCTIONS = """
Untuk menggunakan debug script ini:

1. Jalankan di ERPNext Console:
   exec(open('/home/akrilo/erpnext/apps/shopee_bridge/debug_sync_pe.py').read())

2. Debug masalah PE:
   result = debug_sync_pe_issues()
   print(result)

3. Fix missing PE:
   result = fix_missing_payment_entries()
   print(result)

4. Debug order tertentu:
   result = debug_sync_pe_issues(order_sn_list=['ORDER_SN_1', 'ORDER_SN_2'])
   print(result)
"""

if __name__ == "__main__":
    print("Debug Sync Payment Entry Issues")
    print("=" * 50)
    print(USAGE_INSTRUCTIONS)
