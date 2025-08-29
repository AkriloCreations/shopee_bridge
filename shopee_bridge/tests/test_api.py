import unittest
import frappe # pyright: ignore[reportMissingImports]
import json
import hmac
import hashlib
from shopee_bridge.api import _settings

class TestWebhookSignature(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
    
    def test_valid_signature(self):
        from shopee_bridge.api import verify_webhook_signature
        
        # Test implementation here
        pass
    
    def test_invalid_signature(self):
        from shopee_bridge.api import verify_webhook_signature
        
        # Test implementation here  
        pass

def verify_webhook_signature(raw_body: bytes, headers) -> bool:
    print(f"DEBUG: Raw body length: {len(raw_body)}")
    print(f"DEBUG: Headers: {headers}")
    
    s = _settings()
    webhook_key = (getattr(s, "webhook_key", "") or "").strip()
    print(f"DEBUG: Webhook key configured: {bool(webhook_key)}")
    
    # ... rest of function

# Run with: bench --site sitename run-tests shopee_bridge.tests.test_api