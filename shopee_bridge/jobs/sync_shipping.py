"""
Shopee Bridge cron job: incremental shipping sync.
"""
from typing import Dict, Any

def run(minutes: int = 30) -> Dict[str, Any]:
    """
    Run incremental shipping sync job.
    Args:
        minutes: Minutes since last update.
    Returns:
        Sync result dict.
    """
    # TODO: Call services/logistics.sync_shipping_status
    pass
