import unittest
import frappe # pyright: ignore[reportMissingImports]
import json
import hmac
import hashlib

class TestWebhookSignature(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
    
    def test_valid_signature(self):
        from shopee_bridge.api import verify_webhook_signature, _settings
        
        # Get actual webhook key
        s = _settings()
        webhook_key = getattr(s, "webhook_key", "")
        
        if not webhook_key:
            self.skipTest("Webhook key not configured")
        
        # Create test data
        test_data = {"order_id": "12345", "status": "completed"}
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Calculate correct signature
        correct_sig = hmac.new(
            webhook_key.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        # Test with correct signature
        headers = {"X-Shopee-Signature": correct_sig}
        result = verify_webhook_signature(raw_body, headers)
        
        self.assertTrue(result)
    
    def test_invalid_signature(self):
        from shopee_bridge.api import verify_webhook_signature
        
        test_data = {"order_id": "12345", "status": "completed"}
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Test with wrong signature
        headers = {"X-Shopee-Signature": "wrong_signature"}
        result = verify_webhook_signature(raw_body, headers)
        
        self.assertFalse(result)
    
    def test_missing_signature(self):
        from shopee_bridge.api import verify_webhook_signature
        
        test_data = {"order_id": "12345", "status": "completed"}
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Test with no signature
        headers = {}
        result = verify_webhook_signature(raw_body, headers)
        
        self.assertFalse(result)

# Don't redefine the function here - it should be in api.py