# Refactor Summary - Removed "Selisih Biaya Shopee" Fallback

## Changes Made ✅

### 1. **Removed Fallback Account Logic**
- **Before**: If any specific expense account creation failed, ALL fees would use "Selisih Biaya Shopee"
- **After**: Each fee type MUST have its own specific account or the PE creation will fail

### 2. **Strict Account Requirements**
```python
# Before: Created diff_account as fallback
diff_account = _get_or_create_expense_account("Selisih Biaya Shopee")

# After: NO fallback account - all specific accounts required
if failed_accounts:
    frappe.throw(f"Failed to create required Shopee expense accounts: {failed_accounts}")
```

### 3. **Clean Deduction Logic**
```python
# Before: Always fell back to diff_account
account_to_use = fee_accounts.get(k) or diff_account

# After: Use specific account only
account_to_use = fee_accounts.get(k)
if not account_to_use:
    frappe.logger().error(f"No account found for {k} - this should not happen!")
    continue
```

### 4. **No More "Difference" Entries**
- **Before**: Remaining amounts went to "Selisih Biaya Shopee"  
- **After**: Remaining amounts are logged as warnings but not posted (helps identify incomplete fee data)

## Required Expense Accounts

All 8 accounts must exist before PE creation:

1. **Komisi Shopee** (commission_fee)
2. **Biaya Layanan Shopee** (service_fee)  
3. **Proteksi Pengiriman Shopee** (protection_fee)
4. **Selisih Ongkir Shopee** (shipping_fee_difference)
5. **Voucher Seller Shopee** (voucher_seller)
6. **Voucher Shopee** (voucher_from_shopee)
7. **Coin Cashback Shopee** (coin_cash_back) 
8. **Voucher Kode Seller Shopee** (voucher_code_seller)

## Missing Accounts from Screenshot

From your screenshot, I see you have:
- ✅ Komisi Shopee
- ✅ Selisih Ongkir Shopee  
- ✅ Voucher Shopee
- ✅ Proteksi Pengiriman Shopee

**Missing accounts:**
- ❌ Biaya Layanan Shopee
- ❌ Voucher Seller Shopee
- ❌ Coin Cashback Shopee
- ❌ Voucher Kode Seller Shopee

## How to Create Missing Accounts

### Option 1: Use Console
```python
frappe.call('shopee_bridge.webhook.create_all_shopee_expense_accounts')
```

### Option 2: Manual Creation
Create the missing accounts manually in Chart of Accounts under an Expense parent account.

## Expected Result

After creating all accounts, each Payment Entry will have:
- **Paid Amount**: Net amount received
- **Deductions**: Separate line for each fee type using its specific account
- **No "Selisih Biaya Shopee" entries** unless there's truly unaccounted amounts

## Testing

1. **Create accounts**: Run `create_all_shopee_expense_accounts()` 
2. **Test PE creation**: Process a Shopee order payment
3. **Verify deductions**: Check that each fee uses its specific account

The system will now be much more precise about expense categorization!
