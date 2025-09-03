"""
Shopee Bridge cron job: incremental returns sync.
"""
from typing import Dict, Any

def run(minutes: int = 30) -> Dict[str, Any]:
    """
    Run incremental returns sync job.
    Args:
        minutes: Minutes since last update.
    Returns:
        Sync result dict.
    """
    # TODO: Call services/returns.sync_returns_incremental
    pass
