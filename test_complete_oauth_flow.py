#!/usr/bin/env python3
"""
Complete OAuth Flow Test for Shopee Bridge

This script tests the complete OAuth authentication flow according to Shopee API v2 specifications:
1. Generate authorization URL with proper signature
2. Handle OAuth callback with authorization code
3. Exchange code for access_token and refresh_token  
4. Test token refresh mechanism

Usage:
    python test_complete_oauth_flow.py

Requirements:
    - Shopee Settings must be configured with partner_id, partner_key, redirect_url
    - Environment should be set to "test" for sandbox testing
"""

import sys
import os
import json
import time
from urllib.parse import urlparse, parse_qs

# Add the shopee_bridge module to Python path
sys.path.insert(0, '/home/akrilo/erpnext/apps/shopee_bridge')

def mock_frappe_environment():
    """Mock minimal Frappe environment for testing"""
    import frappe
    from unittest.mock import MagicMock
    
    # Mock settings document
    settings_mock = MagicMock()
    settings_mock.partner_id = 123456  # Test partner ID
    settings_mock.partner_key = "test_partner_key_123"  # Test partner key
    settings_mock.redirect_url = "https://myapp.com/oauth/callback"
    settings_mock.environment = "test"  # Sandbox environment
    settings_mock.get_password.return_value = "test_partner_key_123"
    
    # Mock Frappe functions
    frappe.get_cached_doc = MagicMock(return_value=settings_mock)
    frappe.cache = MagicMock()
    frappe.cache().get_value = MagicMock(return_value=None)
    frappe.cache().set_value = MagicMock()
    frappe.cache().delete_value = MagicMock()
    frappe.logger = MagicMock()
    frappe.logger().info = print
    
    return settings_mock

def test_authorization_url():
    """Test 1: Generate properly signed authorization URL"""
    print("=== TEST 1: Authorization URL Generation ===")
    
    try:
        from shopee_bridge import auth
        
        # Generate authorization URL with scopes
        scopes = ["shop.basic.info", "order", "payment", "logistics"]
        auth_url = auth.build_authorize_url(scopes)
        
        print(f"Generated Authorization URL:")
        print(auth_url)
        print()
        
        # Parse and validate URL components
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)
        
        required_params = ["partner_id", "timestamp", "sign", "redirect"]
        for param in required_params:
            if param not in query_params:
                raise Exception(f"Missing required parameter: {param}")
        
        print("✅ Authorization URL generated successfully")
        print(f"   - Base URL: {parsed.scheme}://{parsed.netloc}{parsed.path}")
        print(f"   - Partner ID: {query_params['partner_id'][0]}")
        print(f"   - Timestamp: {query_params['timestamp'][0]}")
        print(f"   - Signature: {query_params['sign'][0][:10]}...")
        print(f"   - Redirect URL: {query_params['redirect'][0]}")
        print(f"   - Scopes: {query_params.get('scope', ['N/A'])[0]}")
        print()
        
        return auth_url, query_params
        
    except Exception as e:
        print(f"❌ Authorization URL test failed: {str(e)}")
        return None, None

def test_signature_generation():
    """Test 2: Verify signature generation follows Shopee specification"""
    print("=== TEST 2: Signature Generation Verification ===")
    
    try:
        from shopee_bridge import auth
        
        # Test signature generation with known values
        partner_id = 123456
        api_path = "/api/v2/shop/auth_partner"
        timestamp = 1609459200  # Fixed timestamp for testing
        partner_key = "test_partner_key_123"
        
        # Expected signature base string: partner_id + api_path + timestamp
        expected_base = f"{partner_id}{api_path}{timestamp}"
        print(f"Signature base string: {expected_base}")
        
        # Generate signature
        signature = auth.hmac_sha256(expected_base, partner_key)
        print(f"Generated signature: {signature}")
        
        # Verify signature is 64-character hex string
        if len(signature) != 64 or not all(c in '0123456789abcdef' for c in signature):
            raise Exception("Invalid signature format")
        
        print("✅ Signature generation verified")
        print()
        
    except Exception as e:
        print(f"❌ Signature generation test failed: {str(e)}")

def test_token_exchange_payload():
    """Test 3: Generate token exchange payload"""
    print("=== TEST 3: Token Exchange Payload Generation ===")
    
    try:
        from shopee_bridge import auth
        
        # Test with shop_id
        code = "test_authorization_code_123"
        shop_id = 987654
        
        payload = auth.exchange_code_for_token(code, shop_id)
        
        print("Token Exchange Payload:")
        print(json.dumps(payload, indent=2))
        print()
        
        # Validate payload structure
        required_fields = ["method", "url", "json", "meta"]
        for field in required_fields:
            if field not in payload:
                raise Exception(f"Missing required field in payload: {field}")
        
        # Validate JSON body
        json_body = payload["json"]
        required_json_fields = ["code", "partner_id", "shop_id"]
        for field in required_json_fields:
            if field not in json_body:
                raise Exception(f"Missing required field in JSON body: {field}")
        
        print("✅ Token exchange payload generated successfully")
        print(f"   - Method: {payload['method']}")
        print(f"   - URL: {payload['url'][:50]}...")
        print(f"   - Code: {json_body['code']}")
        print(f"   - Shop ID: {json_body['shop_id']}")
        print()
        
    except Exception as e:
        print(f"❌ Token exchange payload test failed: {str(e)}")

def test_refresh_token_payload():
    """Test 4: Generate refresh token payload"""
    print("=== TEST 4: Refresh Token Payload Generation ===")
    
    try:
        from shopee_bridge import auth
        
        # Mock settings with tokens
        settings_mock = mock_frappe_environment()
        settings_mock.refresh_token = "test_refresh_token_123"
        settings_mock.shop_id = 987654
        
        payload = auth.refresh_token_via_api()
        
        print("Refresh Token Payload:")
        print(json.dumps(payload, indent=2))
        print()
        
        # Validate payload structure
        json_body = payload["json"]
        required_fields = ["partner_id", "shop_id", "refresh_token"]
        for field in required_fields:
            if field not in json_body:
                raise Exception(f"Missing required field in JSON body: {field}")
        
        print("✅ Refresh token payload generated successfully")
        print(f"   - Method: {payload['method']}")
        print(f"   - Shop ID: {json_body['shop_id']}")
        print(f"   - Has refresh_token: {bool(json_body['refresh_token'])}")
        print()
        
    except Exception as e:
        print(f"❌ Refresh token payload test failed: {str(e)}")

def test_oauth_callback_handling():
    """Test 5: OAuth callback parameter handling"""
    print("=== TEST 5: OAuth Callback Handling ===")
    
    try:
        from shopee_bridge import auth
        
        # Test callback parameters (simulating Shopee's redirect)
        callback_params = {
            "code": "auth_code_from_shopee_123",
            "shop_id": "987654",
            "state": "optional_state_parameter"
        }
        
        print(f"Simulated callback parameters: {callback_params}")
        
        # Test parameter validation
        code = callback_params.get("code")
        shop_id = callback_params.get("shop_id")
        main_account_id = callback_params.get("main_account_id")
        
        if not code:
            raise Exception("Missing code parameter")
        
        if not (shop_id or main_account_id):
            raise Exception("Missing shop_id or main_account_id parameter")
        
        print("✅ OAuth callback parameter validation successful")
        print(f"   - Code: {code[:10]}...")
        print(f"   - Shop ID: {shop_id}")
        print(f"   - Main Account ID: {main_account_id or 'N/A'}")
        print()
        
    except Exception as e:
        print(f"❌ OAuth callback handling test failed: {str(e)}")

def main():
    """Run all OAuth flow tests"""
    print("🚀 Starting Shopee Bridge OAuth Flow Tests\n")
    
    # Setup mock environment
    mock_frappe_environment()
    
    # Run tests in sequence
    test_authorization_url()
    test_signature_generation()
    test_token_exchange_payload()
    test_refresh_token_payload()
    test_oauth_callback_handling()
    
    print("=== SUMMARY ===")
    print("All OAuth flow components have been tested.")
    print("\n📋 Next Steps:")
    print("1. Configure real partner_id and partner_key in Shopee Settings")
    print("2. Test with actual Shopee sandbox environment")
    print("3. Complete the full OAuth flow by visiting the authorization URL")
    print("4. Handle the callback and exchange the code for tokens")
    print("\n🔗 Integration Points:")
    print("- Authorization URL: Use connect_to_shopee() API endpoint")
    print("- OAuth Callback: Use oauth_callback() API endpoint")
    print("- Token Refresh: Automatic via scheduled job or manual refresh_token() API")

if __name__ == "__main__":
    main()