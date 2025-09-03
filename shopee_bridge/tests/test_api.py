import unittest
import frappe # pyright: ignore[reportMissingImports]
import json
import hmac
import hashlib

from shopee_bridge.shopee_bridge.utils import _settings

class TestWebhookSignature(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
    
    def test_valid_signature(self):
        from shopee_bridge.api import verify_webhook_signature
        
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
def test_webhook_verification():
    """Test endpoint for webhook signature verification"""
    import json
    
    try:
        # Get settings to check configuration
        s = _settings()
        webhook_key_configured = bool(getattr(s, "webhook_key", ""))
        
        # Test with dummy data
        test_data = {"order_id": "TEST123", "status": "test"}
        raw_body = json.dumps(test_data).encode('utf-8')
        
        # Create a proper signature for testing
        webhook_key = getattr(s, "webhook_key", "")
        if webhook_key:
            test_signature = hmac.new(
                webhook_key.encode('utf-8'),
                raw_body,
                hashlib.sha256
            ).hexdigest()
            
            test_headers = {"X-Shopee-Signature": test_signature}
            verification_result = verify_webhook_signature(raw_body, test_headers)
        else:
            verification_result = False
            test_signature = "N/A - no webhook key"
        
        return {
            "success": True,
            "webhook_key_configured": webhook_key_configured,
            "test_signature": test_signature,
            "verification_result": verification_result
        }
        
    except Exception as e:
        frappe.log_error(f"Test webhook error: {str(e)}", "Shopee Test")
        return {"success": False, "error": str(e)}


# Don't redefine the function here - it should be in api.py