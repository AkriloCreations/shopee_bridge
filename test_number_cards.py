#!/usr/bin/env python
import frappe

# Test Number Card queries
print("Testing Number Card queries...")

try:
    # Test Shopee Sync Errors query
    sync_errors = frappe.get_list("Shopee Sync Log", filters={"status": "ERROR"}, fields=["count(name) as c"])
    print(f"✅ Shopee Sync Errors query: {sync_errors}")

    # Test Shopee Webhooks Pending query
    webhooks_pending = frappe.get_list("Shopee Webhook Inbox", filters={"status": "NEW"}, fields=["count(name) as c"])
    print(f"✅ Shopee Webhooks Pending query: {webhooks_pending}")

    # Check if Number Cards exist
    number_cards = frappe.get_list("Number Card", filters={"module": "Shopee Bridge"})
    print(f"✅ Number Cards found: {len(number_cards)}")
    for card in number_cards:
        print(f"  - {card.name}")

    print("✅ All tests passed!")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()