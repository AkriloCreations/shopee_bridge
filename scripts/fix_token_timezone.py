#!/usr/bin/env python
"""
Token Timezone Fixer for Shopee Bridge

This script helps diagnose and fix any timezone issues with the Shopee OAuth token expiration dates.
Run inside the Frappe environment via bench execute.

Example: bench execute shopee_bridge/scripts/fix_token_timezone.py
"""

import frappe
import json
from datetime import datetime, timezone
try:
    import pytz
    HAS_PYTZ = True
except ImportError:
    HAS_PYTZ = False

def format_token_date(dt):
    """Format a datetime in multiple formats for comparison."""
    if not dt:
        return {"error": "No datetime provided"}
    
    result = {
        "raw": str(dt),
        "type": str(type(dt)),
        "has_tzinfo": dt.tzinfo is not None,
        "tzinfo": str(dt.tzinfo)
    }
    
    try:
        utc_time = dt.astimezone(timezone.utc) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        result["utc_iso"] = utc_time.isoformat()
        result["utc_str"] = utc_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        
        if HAS_PYTZ:
            # Format in Jakarta timezone for comparison
            jakarta_tz = pytz.timezone("Asia/Jakarta")
            jakarta_time = dt.astimezone(jakarta_tz) if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc).astimezone(jakarta_tz)
            result["jakarta_str"] = jakarta_time.strftime("%d-%m-%Y %H:%M:%S")
            result["jakarta_full"] = jakarta_time.strftime("%d-%m-%Y %H:%M:%S %Z%z")
    except Exception as e:
        result["format_error"] = str(e)
        
    return result

def fix_token_timezone():
    """Diagnose and fix token timezone issues."""
    try:
        # Get Shopee Settings
        print("Checking Shopee token settings...")
        settings = frappe.get_doc("Shopee Settings")
        
        # Check token_expires_at
        token_expires_at = getattr(settings, "token_expires_at", None)
        
        print("\nToken Expiration Status:")
        print(f"Raw value: {token_expires_at}")
        print(f"Type: {type(token_expires_at)}")
        
        if not token_expires_at:
            print("No token expiration set.")
            return
            
        # Format for visualization
        formats = format_token_date(token_expires_at)
        for key, value in formats.items():
            print(f"  {key}: {value}")
            
        # Check if we need to fix
        needs_fix = False
        reason = []
        
        if isinstance(token_expires_at, str):
            needs_fix = True
            reason.append("Token is stored as string")
            
        if isinstance(token_expires_at, datetime) and token_expires_at.tzinfo is None:
            needs_fix = True
            reason.append("Token datetime has no timezone")
            
        # Fix if needed
        if needs_fix:
            print("\nFIXING TOKEN TIMEZONE ISSUES:")
            print(f"Reasons: {', '.join(reason)}")
            
            # Convert to datetime if string
            if isinstance(token_expires_at, str):
                print("Converting string to datetime...")
                token_expires_at = frappe.utils.get_datetime(token_expires_at)
                
            # Ensure UTC timezone
            if token_expires_at.tzinfo is None:
                print("Adding UTC timezone...")
                token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)
                
            # Update settings
            settings.token_expires_at = token_expires_at
            settings.save(ignore_permissions=True)
            frappe.db.commit()
            
            print("Token timezone fixed successfully.")
            
            # Show updated values
            print("\nUpdated Token Information:")
            updated_settings = frappe.get_doc("Shopee Settings")
            updated_expires = getattr(updated_settings, "token_expires_at", None)
            formats = format_token_date(updated_expires)
            for key, value in formats.items():
                print(f"  {key}: {value}")
        else:
            print("\nToken timezone is correctly configured, no fix needed.")
            
        # Calculate time remaining
        if isinstance(token_expires_at, datetime):
            now = datetime.now(timezone.utc)
            if token_expires_at.tzinfo is None:
                token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)
            
            time_diff = token_expires_at - now
            seconds_remaining = time_diff.total_seconds()
            
            print(f"\nToken expires in: {seconds_remaining:.1f} seconds")
            print(f"                  {seconds_remaining/60:.1f} minutes")
            print(f"                  {seconds_remaining/3600:.2f} hours")
            
            if seconds_remaining < 0:
                print("TOKEN IS EXPIRED! Please re-authorize.")
            elif seconds_remaining < 600:
                print("TOKEN EXPIRES SOON! Will be refreshed on next operation.")
            else:
                print("Token is valid.")
            
    except Exception as e:
        print(f"Error checking token: {e}")

if __name__ == "__main__":
    fix_token_timezone()
