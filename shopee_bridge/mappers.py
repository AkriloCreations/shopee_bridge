"""
Shopee Bridge pure mappers: Shopee → ERPNext rows (no frappe writes).
"""
from typing import Dict, Any

def map_order_to_sales_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Shopee order dict to ERPNext Sales Order row.
    Args:
        order: Shopee order dict.
    Returns:
        ERPNext Sales Order dict.
    """
    # TODO: Implement mapping logic
    pass

def map_return_to_customer_issue(return_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Shopee return dict to ERPNext Customer Issue row.
    Args:
        return_payload: Shopee return dict.
    Returns:
        ERPNext Customer Issue dict.
    """
    # TODO: Implement mapping logic
    pass
