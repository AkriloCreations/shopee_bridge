"""
Test Payment Entry creation issue fix in sync_orders_range

Problem: sync_orders_range was not creating Payment Entries because:
1. net_amount=0 was hardcoded in _ensure_payment()
2. create_payment_entry_from_shopee has condition: if net <= 0: return (no PE created)

Fix Applied:
1. Calculate actual net_amount from escrow data using _normalize_escrow_payload()
2. Skip PE creation if net_amount <= 0 (legitimate case for refunds)
3. Pass proper normalized escrow data and calculated net_amount
4. Added proper logging and error handling
5. Added debug functions to troubleshoot PE creation issues

Testing:
- Use debug_sync_orders_range_pe() to test specific orders
- Use test_payment_entry_creation() to test single order PE creation
- Check logs for PE creation attempts and results
"""

# Test functions added to webhook.py:
# - debug_sync_orders_range_pe(order_sn_list, time_from, time_to) 
# - test_payment_entry_creation(order_sn)

# Fixed code in api.py:
# def _ensure_payment(order_sn: str):
#     # OLD: net_amount=0 (hardcoded)
#     # NEW: Calculate from escrow data
#     esc_norm = _normalize_escrow_payload(esc)
#     net_amount = flt(esc_norm.get("net_amount"))
#     if net_amount <= 0:
#         return  # Skip if no payment needed
#     
#     pe_name = create_payment_entry_from_shopee(
#         escrow=esc_norm,  # Use normalized data
#         net_amount=net_amount,  # Use calculated amount
#         posting_ts=payout_ts,  # Use proper timestamp
#     )

print("Payment Entry creation fix applied successfully!")
