"""
Shopee Bridge job: reconcile bank transactions.
"""
from typing import Dict, Any

def run(days_back: int = 2) -> Dict[str, Any]:
    """
    Run bank reconciliation job for Shopee payouts.
    Args:
        days_back: Days to look back.
    Returns:
        Reconcile result dict.
    """
    # TODO: Call services/finance.reconcile_bank_strict
    pass
