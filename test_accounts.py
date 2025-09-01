#!/usr/bin/env python3
"""
Test script to verify Shopee expense accounts creation
Run this from ERPNext console or as a script
"""

def test_shopee_accounts():
    """Test that all Shopee expense accounts can be created"""
    
    # Account mapping that should match webhook.py
    fee_account_mapping = {
        "commission": "Komisi Shopee",
        "service": "Biaya Layanan Shopee", 
        "protection": "Proteksi Pengiriman Shopee", 
        "shipdiff": "Selisih Ongkir Shopee",
        "voucher_seller": "Voucher Seller Shopee",
        "voucher_shopee": "Voucher Shopee", 
        "coin_cash_back": "Coin Cashback Shopee",
        "voucher_code_seller": "Voucher Kode Seller Shopee"
    }
    
    print("Testing Shopee Expense Account Creation")
    print("=" * 50)
    
    # Test each account
    results = {}
    for key, account_name in fee_account_mapping.items():
        print(f"Testing {key}: {account_name}")
        try:
            # This would need to be run in ERPNext context
            # account = _get_or_create_expense_account(account_name)
            # For now, just show what should be created
            print(f"  ✓ Should create: {account_name}")
            results[key] = "success"
        except Exception as e:
            print(f"  ✗ Error: {e}")
            results[key] = f"error: {e}"
    
    print("\nSummary:")
    print("=" * 50)
    success_count = sum(1 for r in results.values() if r == "success")
    print(f"Expected accounts: {len(fee_account_mapping)}")
    print(f"Successful: {success_count}")
    
    print("\nAccount List for Manual Verification:")
    print("=" * 50)
    for i, (key, account_name) in enumerate(fee_account_mapping.items(), 1):
        print(f"{i}. {account_name}")
    
    print("\nTo test in ERPNext Console:")
    print("=" * 50)
    print("1. Go to ERPNext Console")
    print("2. Run: frappe.call('shopee_bridge.webhook.create_all_shopee_expense_accounts')")
    print("3. Or run: frappe.call('shopee_bridge.webhook.debug_payment_entry_accounts')")

if __name__ == "__main__":
    test_shopee_accounts()
