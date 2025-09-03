"""
Shopee Bridge job: full-year backfill orchestrator.
"""
from typing import Dict, Any

def run(company: str, fiscal_year_name: str) -> Dict[str, Any]:
    """
    Run full-year backfill job for Shopee data.
    Args:
        company: ERPNext company name.
        fiscal_year_name: Fiscal year name.
    Returns:
        Backfill result dict.
    """
    # TODO: Call services/fiscal.run_fiscal_year_full_sync
    pass
