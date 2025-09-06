#!/usr/bin/env python
import frappe
from shopee_bridge.auth import get_token_status, refresh_access_token_if_needed

# Initialize Frappe (required when running standalone scripts)
frappe.init(site="erp.managerio.ddns.net")
frappe.connect()

try:
    # Get current token status
    print("Current Token Status:")
    status = get_token_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    # If token needs refresh, refresh it
    if status.get("needs_refresh", False):
        print("\nToken needs refresh, refreshing...")
        refresh_result = refresh_access_token_if_needed()
        print(f"Refresh result: {refresh_result}")
        
        # Check status after refresh
        print("\nToken Status After Refresh:")
        new_status = get_token_status()
        for key, value in new_status.items():
            print(f"  {key}: {value}")
    else:
        print("\nToken does not need refresh.")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    # Always disconnect from Frappe
    frappe.destroy()
