"""
Shopee Bridge cron job: incremental order sync (every 10m).
"""
from typing import Dict, Any

def run(minutes: int = 10) -> Dict[str, Any]:
    """
    Run incremental order sync job.
    Args:
        minutes: Minutes since last update.
    Returns:
        Sync result dict.
    """
    # TODO: Call services/orders.sync_incremental_orders
    pass
