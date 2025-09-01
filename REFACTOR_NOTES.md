# Payment Entry Refactor Notes

## Problem Description

The `create_payment_entry_from_shopee` function had the following issues:

1. **Single Account Usage**: All deduction entries were using "Selisih Biaya Shopee" account instead of specific expense accounts for each fee type
2. **Poor Error Handling**: If any specific expense account creation failed, it would fall back to the generic account
3. **No Validation**: No validation of the final Payment Entry amounts and deductions
4. **Poor Debugging**: Limited logging to understand which accounts were created/used

## Root Cause

The issue was in the account creation and deduction logic:

```python
# OLD CODE ISSUE:
fee_accounts["commission"] = None  # if creation failed
# Later in deduction loop:
account_to_use = fee_accounts.get(k) or diff_account  # Always used diff_account!
```

This meant if ANY specific account creation failed, ALL deductions would use the "Selisih Biaya Shopee" account.

## Solution Implemented

### 1. Improved Account Creation
- Better error handling for account creation
- Retry logic for duplicate account names
- More robust parent account detection
- Detailed logging of account creation process

### 2. Fixed Deduction Logic
- Separate handling for each fee type with proper account assignment
- Fallback to diff account only when specific account is truly unavailable
- Better validation that we don't exceed remaining amount
- Detailed logging of each deduction entry

### 3. Added Validation
- `_validate_payment_entry_amounts()` function to check final PE amounts
- Validation that paid amount matches net amount
- Validation that total allocated matches expected amount
- Check for duplicate deduction accounts

### 4. Enhanced Debugging
- Added debug functions to test account creation
- Better logging throughout the process
- Deduction details tracking for troubleshooting

## Key Changes

### Before:
```python
# All fees used same account if any creation failed
for k in ordered_keys:
    account_to_use = fee_accounts.get(k) or diff_account  # Problem here!
```

### After:
```python
# Each fee type gets its own account, with proper fallback
for k in ordered_keys:
    account_to_use = fee_accounts.get(k)
    if not account_to_use:
        frappe.logger().warning(f"No account for {k}, using diff account")
        account_to_use = diff_account
```

## Testing

Added test functions:
1. `debug_payment_entry_accounts()` - Test account creation and deduction logic
2. `test_payment_entry_creation()` - Test full PE creation with different scenarios

## Expected Result

Now each Shopee fee should have its own expense account:
- Commission → "Komisi Shopee"
- Service Fee → "Biaya Layanan Shopee"  
- Protection Fee → "Proteksi Pengiriman Shopee"
- Shipping Difference → "Selisih Ongkir Shopee"
- Voucher Seller → "Voucher Seller Shopee"
- Voucher Shopee → "Voucher Shopee"
- Coin Cashback → "Coin Cashback Shopee"
- Voucher Code Seller → "Voucher Kode Seller Shopee"

Only unmatched amounts should go to "Selisih Biaya Shopee".
