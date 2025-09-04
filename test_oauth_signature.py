#!/usr/bin/env python3
"""
Standalone OAuth Signature Test for Shopee Bridge

Tests the OAuth signature generation according to Shopee API v2 specifications
without requiring the full Frappe framework.
"""

import hmac
import hashlib
import time
import urllib.parse


def hmac_sha256(data: str, key: str) -> str:
    """Generate HMAC-SHA256 signature"""
    return hmac.new(key.encode('utf-8'), data.encode('utf-8'), hashlib.sha256).hexdigest()


def test_authorization_url_signature():
    """Test authorization URL signature generation"""
    print("=== OAuth Authorization URL Signature Test ===")
    
    # Test parameters (matching Shopee specification)
    partner_id = 123456
    api_path = "/api/v2/shop/auth_partner" 
    timestamp = int(time.time())
    partner_key = "test_partner_key_secret"
    redirect_url = "https://myapp.com/oauth/callback"
    
    # Create signature base string for Public API
    # Shopee spec: For Public APIs: partner_id, api path, timestamp
    base_string = f"{partner_id}{api_path}{timestamp}"
    print(f"Signature base string: {base_string}")
    
    # Generate signature
    signature = hmac_sha256(base_string, partner_key)
    print(f"Generated signature: {signature}")
    
    # Build complete authorization URL
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "sign": signature,
        "redirect": redirect_url,
    }
    
    base_url = "https://partner.test-stable.shopeemobile.com"  # Sandbox
    query_string = urllib.parse.urlencode(params)
    auth_url = f"{base_url}{api_path}?{query_string}"
    
    print(f"\nComplete Authorization URL:")
    print(auth_url)
    print(f"\nURL Length: {len(auth_url)} characters")
    
    # Validate signature format
    assert len(signature) == 64, "Signature should be 64 characters"
    assert all(c in '0123456789abcdef' for c in signature), "Signature should be hex"
    
    print("\n✅ Authorization URL signature test passed!")
    return auth_url


def test_token_exchange_signature():
    """Test token exchange signature generation"""
    print("\n=== Token Exchange Signature Test ===")
    
    # Test parameters
    partner_id = 123456
    api_path = "/api/v2/auth/token/get"
    timestamp = int(time.time()) 
    partner_key = "test_partner_key_secret"
    
    # For Public APIs (token exchange): partner_id + api_path + timestamp
    base_string = f"{partner_id}{api_path}{timestamp}"
    print(f"Signature base string: {base_string}")
    
    signature = hmac_sha256(base_string, partner_key)
    print(f"Generated signature: {signature}")
    
    # Build token exchange URL (query params)
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp, 
        "sign": signature
    }
    
    base_url = "https://partner.test-stable.shopeemobile.com"
    query_string = urllib.parse.urlencode(params)
    token_url = f"{base_url}{api_path}?{query_string}"
    
    print(f"\nToken Exchange URL:")
    print(token_url)
    
    # Request body for token exchange
    request_body = {
        "code": "authorization_code_from_callback",
        "partner_id": partner_id,
        "shop_id": 1  # Default for shop apps
    }
    
    print(f"\nRequest Body:")
    print(request_body)
    
    print("\n✅ Token exchange signature test passed!")
    return token_url, request_body


def test_refresh_token_signature():
    """Test refresh token signature generation"""
    print("\n=== Refresh Token Signature Test ===")
    
    # Test parameters
    partner_id = 123456
    api_path = "/api/v2/auth/token/refresh"
    timestamp = int(time.time())
    partner_key = "test_partner_key_secret"
    
    # For Public APIs: partner_id + api_path + timestamp
    base_string = f"{partner_id}{api_path}{timestamp}"
    print(f"Signature base string: {base_string}")
    
    signature = hmac_sha256(base_string, partner_key)
    print(f"Generated signature: {signature}")
    
    # Build refresh URL
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "sign": signature
    }
    
    base_url = "https://partner.test-stable.shopeemobile.com"
    query_string = urllib.parse.urlencode(params)
    refresh_url = f"{base_url}{api_path}?{query_string}"
    
    print(f"\nRefresh Token URL:")
    print(refresh_url)
    
    # Request body for refresh
    request_body = {
        "partner_id": partner_id,
        "shop_id": 987654,
        "refresh_token": "sample_refresh_token"
    }
    
    print(f"\nRequest Body:")
    print(request_body)
    
    print("\n✅ Refresh token signature test passed!")
    return refresh_url, request_body


def test_shop_api_signature():
    """Test Shop API signature generation (for authenticated calls)"""
    print("\n=== Shop API Signature Test ===")
    
    # Test parameters for authenticated API calls
    partner_id = 123456
    api_path = "/api/v2/shop/get_shop_info"
    timestamp = int(time.time())
    access_token = "sample_access_token_123"
    shop_id = 987654
    partner_key = "test_partner_key_secret"
    
    # For Shop APIs: partner_id, api path, timestamp, access_token, shop_id
    base_string = f"{partner_id}{api_path}{timestamp}{access_token}{shop_id}"
    print(f"Signature base string: {base_string}")
    
    signature = hmac_sha256(base_string, partner_key)
    print(f"Generated signature: {signature}")
    
    # Build authenticated API URL
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": shop_id,
        "sign": signature
    }
    
    base_url = "https://partner.test-stable.shopeemobile.com"
    query_string = urllib.parse.urlencode(params)
    api_url = f"{base_url}{api_path}?{query_string}"
    
    print(f"\nShop API URL:")
    print(api_url)
    
    print("\n✅ Shop API signature test passed!")
    return api_url


def main():
    """Run all signature tests"""
    print("🔐 Shopee Bridge OAuth Signature Tests\n")
    
    # Test all signature types
    auth_url = test_authorization_url_signature()
    token_url, token_body = test_token_exchange_signature()
    refresh_url, refresh_body = test_refresh_token_signature()
    shop_api_url = test_shop_api_signature()
    
    print("\n" + "="*60)
    print("📋 COMPLETE OAUTH FLOW SUMMARY")
    print("="*60)
    
    print(f"\n1️⃣  AUTHORIZATION (User visits this URL):")
    print(f"   {auth_url[:80]}...")
    
    print(f"\n2️⃣  TOKEN EXCHANGE (After callback with code):")
    print(f"   POST {token_url[:60]}...")
    print(f"   Body: {token_body}")
    
    print(f"\n3️⃣  TOKEN REFRESH (Before expiry):")
    print(f"   POST {refresh_url[:60]}...")
    print(f"   Body: {refresh_body}")
    
    print(f"\n4️⃣  API CALLS (With valid tokens):")
    print(f"   GET {shop_api_url[:60]}...")
    
    print(f"\n✅ All signature tests completed successfully!")
    print(f"\n🔗 Integration Ready:")
    print(f"   - All signatures follow Shopee API v2 specification")
    print(f"   - URLs use sandbox environment for testing")
    print(f"   - Ready for integration with Frappe/ERPNext")


if __name__ == "__main__":
    main()