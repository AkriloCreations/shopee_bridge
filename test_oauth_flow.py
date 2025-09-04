#!/usr/bin/env python3
"""
Complete OAuth Flow Test for Shopee Bridge

This test verifies:
1. Shopee Settings configuration
2. OAuth URL generation
3. Request signing
4. Health status checks

Usage: python test_oauth_flow.py
"""

import frappe
import json

def test_settings_configuration():
    """Test that Shopee Settings is properly configured."""
    print("=== Testing Shopee Settings Configuration ===")
    
    try:
        settings = frappe.get_doc("Shopee Settings")
        
        print(f"✅ Partner ID: {settings.partner_id}")
        print(f"✅ Environment: {settings.environment}")
        print(f"✅ Redirect URL: {settings.redirect_url}")
        
        # Test password field access
        partner_key = settings.get_password("partner_key")
        if partner_key:
            print(f"✅ Partner Key: {partner_key[:8]}{'*' * (len(partner_key) - 8)}")
        else:
            print("❌ Partner Key: Not set")
        
        print(f"Shop ID: {getattr(settings, 'shop_id', 'Not set')}")
        print(f"Access Token: {'Set' if getattr(settings, 'access_token', None) else 'Not set'}")
        print(f"Refresh Token: {'Set' if getattr(settings, 'refresh_token', None) else 'Not set'}")
        
        return True
    except Exception as e:
        print(f"❌ Error accessing Shopee Settings: {e}")
        return False

def test_oauth_url_generation():
    """Test OAuth URL generation."""
    print("\n=== Testing OAuth URL Generation ===")
    
    try:
        from shopee_bridge.api import connect_to_shopee
        
        # Test with default scopes
        result = connect_to_shopee()
        if result.get("ok"):
            url = result["url"]
            print(f"✅ OAuth URL generated: {url[:100]}...")
            
            # Verify URL structure
            if "partner.test-stable.shopeemobile.com" in url or "partner.shopeemobile.com" in url:
                print("✅ Correct Shopee domain")
            if "partner_id=" in url and "redirect=" in url and "scope=" in url:
                print("✅ Required parameters present")
                
            return True
        else:
            print(f"❌ OAuth URL generation failed: {result.get('error')}")
            return False
            
    except Exception as e:
        print(f"❌ OAuth URL generation error: {e}")
        return False

def test_signature_generation():
    """Test request signature generation."""
    print("\n=== Testing Request Signature Generation ===")
    
    try:
        from shopee_bridge import auth
        
        # Test signature for a mock API path
        test_path = "/api/v2/shop/get_shop_info"
        test_params = {"limit": 10}
        
        try:
            # This will fail without access token, but we can test the structure
            signed = auth.sign_request(test_path, test_params, None)
            print("❌ Expected AuthRequired exception, but got result")
            return False
        except auth.AuthRequired as e:
            print(f"✅ Correctly requires authentication: {e}")
            return True
        except Exception as e:
            print(f"❌ Unexpected error in signing: {e}")
            return False
            
    except Exception as e:
        print(f"❌ Signature test error: {e}")
        return False

def test_health_status():
    """Test system health status."""
    print("\n=== Testing Health Status ===")
    
    try:
        from shopee_bridge.api import get_health_status
        
        result = get_health_status()
        if result.get("ok"):
            health = result["health"]
            print(f"✅ Health check successful")
            print(f"  Token Valid: {health['token_valid']}")
            print(f"  Settings Configured: {health['settings_configured']}")
            print(f"  Recent Errors: {health['recent_errors']}")
            print(f"  Pending Webhooks: {health['pending_webhooks']}")
            return True
        else:
            print(f"❌ Health check failed: {result.get('error')}")
            return False
            
    except Exception as e:
        print(f"❌ Health status error: {e}")
        return False

def test_api_endpoints():
    """Test key API endpoints."""
    print("\n=== Testing API Endpoints ===")
    
    endpoints = [
        ("shopee_bridge.api.connect_to_shopee", {}),
        ("shopee_bridge.api.test_shopee_connection", {}),
        ("shopee_bridge.api.get_health_status", {}),
    ]
    
    success_count = 0
    
    for endpoint, args in endpoints:
        try:
            result = frappe.get_attr(endpoint)(**args)
            if result.get("ok"):
                print(f"✅ {endpoint}: Success")
                success_count += 1
            else:
                print(f"⚠️  {endpoint}: {result.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"❌ {endpoint}: {e}")
    
    print(f"API Endpoints: {success_count}/{len(endpoints)} successful")
    return success_count == len(endpoints)

def main():
    """Run all tests."""
    print("🚀 Shopee Bridge OAuth Flow Test")
    print("=" * 50)
    
    # Initialize Frappe
    frappe.init(site='erp.managerio.ddns.net')  
    frappe.connect()
    
    tests = [
        ("Settings Configuration", test_settings_configuration),
        ("OAuth URL Generation", test_oauth_url_generation), 
        ("Request Signing", test_signature_generation),
        ("Health Status", test_health_status),
        ("API Endpoints", test_api_endpoints),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 50)
    print("🔍 Test Summary:")
    
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
        if result:
            passed += 1
    
    print(f"\nOverall: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        print("🎉 All tests passed! OAuth infrastructure is ready.")
    else:
        print("⚠️  Some tests failed. Check configuration.")
    
    print("\n📋 Next Steps:")
    print("1. Visit the OAuth URL to complete authorization")
    print("2. Handle the callback to get access tokens") 
    print("3. Test actual API calls with tokens")

if __name__ == "__main__":
    main()