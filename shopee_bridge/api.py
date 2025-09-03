"""
Shopee Bridge API endpoints for ERPNext. Only input validation and service/job calls.
"""
from typing import Any, Dict, List

def connect_to_shopee(scopes: List[str]) -> str:
    """
    Initiate OAuth connection to Shopee.
    Args:
        scopes: List of OAuth scopes.
    Returns:
        Redirect URL for Shopee authorization.
    """
    # TODO: Validate scopes, call auth.build_authorize_url
    pass

def oauth_callback(**params) -> Dict[str, Any]:
    """
    Handle OAuth callback from Shopee.
    Args:
        params: Callback parameters.
    Returns:
        Success/failure dict.
    """
    # TODO: Validate params, call auth.handle_oauth_callback
    pass

def test_shopee_connection() -> Dict[str, Any]:
    """
    Test Shopee API connection using current settings.
    Returns:
        Success/failure dict.
    """
    # TODO: Call services/orders.get_order_list with dummy params
    pass

def webhook_live() -> Dict[str, Any]:
    """
    Shopee live webhook endpoint. Validates signature, inserts inbox row, returns 200.
    Returns:
        Success dict.
    """
    # TODO: Validate signature, insert Shopee Webhook Inbox, enqueue job
    pass

def webhook_test() -> Dict[str, Any]:
    """
    Shopee test webhook endpoint. Validates signature, inserts inbox row, returns 200.
    Returns:
        Success dict.
    """
    # TODO: Validate signature, insert Shopee Webhook Inbox, enqueue job
    pass

def sync_orders_api(minutes: int = 15) -> Dict[str, Any]:
    """
    Manually trigger incremental order sync.
    Args:
        minutes: How far back to sync.
    Returns:
        Sync result dict.
    """
    # TODO: Call jobs/sync_orders.run
    pass

def sync_finance_api() -> Dict[str, Any]:
    """
    Manually trigger finance sync.
    Returns:
        Sync result dict.
    """
    # TODO: Call jobs/sync_finance.run
    pass
