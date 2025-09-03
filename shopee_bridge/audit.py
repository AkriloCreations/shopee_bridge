import frappe
from datetime import datetime, timedelta
from .api import complete_order_to_si

def audit_shopee_orders_for_month(year: int, month: int, auto_fix: bool = True):
    """
    Audit and sync all Shopee orders for a given month.
    For each order with status COMPLETED:
      - Ensure Sales Invoice exists
      - Ensure Payment Entry exists
      - If missing, auto-fix (create) if auto_fix=True
    Returns a report of discrepancies and actions taken.
    """
    # Calculate start/end date for the month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(year, month + 1, 1) - timedelta(days=1)
    start_ts = int(start_date.replace(hour=0, minute=0, second=0).timestamp())
    end_ts = int(end_date.replace(hour=23, minute=59, second=59).timestamp())

    # Get all Shopee orders with COMPLETED status in this range
    # Assume custom_shopee_order_sn is set on Sales Order
    so_list = frappe.get_list(
        "Sales Order",
        filters={
            "custom_shopee_order_sn": ["!=", ""],
            "transaction_date": ["between", [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]],
            "docstatus": 1
        },
        fields=["name", "custom_shopee_order_sn", "transaction_date"]
    )

    report = []
    for so in so_list:
        order_sn = so["custom_shopee_order_sn"]
        si_name = frappe.db.get_value("Sales Invoice", {
            "custom_shopee_order_sn": order_sn,
            "docstatus": 1
        }, "name")
        pe_exists = False
        if si_name:
            pe_exists = frappe.db.exists(
                "Payment Entry Reference",
                {"reference_doctype": "Sales Invoice", "reference_name": si_name}
            )
        status = {
            "order_sn": order_sn,
            "sales_order": so["name"],
            "sales_invoice": si_name,
            "payment_entry_exists": bool(pe_exists)
        }
        if not si_name or not pe_exists:
            if auto_fix:
                result = complete_order_to_si(order_sn)
                status["auto_fixed"] = result
            else:
                status["auto_fixed"] = None
        report.append(status)

    return report