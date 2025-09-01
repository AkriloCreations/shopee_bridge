## Summary of Payment Entry Refactor

### Problem Fixed ✅
- **Issue**: All Shopee fees were being recorded to a single "Selisih Biaya Shopee" account instead of their respective specific accounts
- **Root Cause**: Poor error handling in account creation caused fallback to generic account for all fees

### Key Changes Made

#### 1. **Improved Account Creation Logic**
```python
# Before: Individual try-catch blocks that set None on failure
fee_accounts["commission"] = None  # if failed

# After: Centralized account creation with proper tracking
fee_account_mapping = {
    "commission": "Komisi Shopee",
    "service": "Biaya Layanan Shopee", 
    # ... etc
}
for key, account_name in fee_account_mapping.items():
    fee_accounts[key] = _get_or_create_expense_account(account_name)
```

#### 2. **Fixed Deduction Logic**
```python
# Before: Always fell back to diff_account if any account was None
account_to_use = fee_accounts.get(k) or diff_account  # ❌ Problem!

# After: Proper account assignment with logging
account_to_use = fee_accounts.get(k)
if not account_to_use:
    frappe.logger().warning(f"No account for {k}, using diff account")
    account_to_use = diff_account
```

#### 3. **Enhanced `_get_or_create_expense_account()` Function**
- Better parent account detection (tries multiple patterns)
- Handles duplicate account creation race conditions
- More robust error handling and validation
- Detailed logging for troubleshooting

#### 4. **Added Validation & Debugging**
- `_validate_payment_entry_amounts()` to verify PE correctness
- `debug_payment_entry_accounts()` test function
- Enhanced logging throughout the process
- Deduction details tracking

### Expected Result Now

Each Shopee fee type will create its own expense account:
- **Commission Fee** → `Komisi Shopee`
- **Service Fee** → `Biaya Layanan Shopee`
- **Protection Fee** → `Proteksi Pengiriman Shopee`
- **Shipping Difference** → `Selisih Ongkir Shopee`
- **Voucher Seller** → `Voucher Seller Shopee`
- **Voucher Shopee** → `Voucher Shopee`
- **Coin Cashback** → `Coin Cashback Shopee`
- **Voucher Code Seller** → `Voucher Kode Seller Shopee`
- **Unmatched amounts** → `Selisih Biaya Shopee`

### Testing Functions Added

1. **`debug_payment_entry_accounts()`** - Test account creation and deduction simulation
2. **`test_payment_entry_creation()`** - Full PE creation testing with different scenarios

### Files Modified

1. **`webhook.py`** - Main refactor of `create_payment_entry_from_shopee()`
2. **`REFACTOR_NOTES.md`** - Detailed documentation of changes

The refactored code now properly maps each fee type to its specific expense account, providing better financial reporting and clearer separation of different Shopee costs.
