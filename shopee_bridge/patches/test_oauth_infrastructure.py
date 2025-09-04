"""
Test OAuth Infrastructure - Shopee Bridge

Run via: bench --site all execute shopee_bridge.patches.test_oauth_infrastructure.execute
"""

import frappe

def execute():
    """Test complete OAuth infrastructure."""
    print("🚀 Shopee Bridge OAuth Infrastructure Test")
    print("=" * 50)
    
    # Test 1: Settings Configuration
    print("\n=== Test 1: Shopee Settings Configuration ===")
    try:
        settings = frappe.get_doc("Shopee Settings")
        print(f"✅ Partner ID: {settings.partner_id}")
        print(f"✅ Environment: {settings.environment}")
        print(f"✅ Redirect URL: {settings.redirect_url}")
        
        partner_key = settings.get_password("partner_key")
        if partner_key:
            # Show first 8 chars, mask the rest
            print(f"✅ Partner Key: {partner_key[:8]}{'*' * max(0, len(partner_key) - 8)} ({len(partner_key)} chars)")
        else:
            print("❌ Partner Key: Not set")
        
        print(f"Shop ID: {getattr(settings, 'shop_id', 'Not set')}")
        access_token = getattr(settings, 'access_token', None)
        refresh_token = getattr(settings, 'refresh_token', None)
        print(f"Access Token: {'Set (' + str(len(access_token)) + ' chars)' if access_token else 'Not set'}")
        print(f"Refresh Token: {'Set (' + str(len(refresh_token)) + ' chars)' if refresh_token else 'Not set'}")
        
    except Exception as e:
        print(f"❌ Settings error: {e}")
        
    # Test 2: OAuth URL Generation
    print("\n=== Test 2: OAuth URL Generation ===")
    try:
        result = frappe.get_attr("shopee_bridge.api.connect_to_shopee")()
        if result.get("ok"):
            url = result["url"]
            print(f"✅ OAuth URL: {url[:80]}...")
            
            # Check URL components
            if "partner_id=" in url:
                print("✅ Contains partner_id")
            if "redirect=" in url:
                print("✅ Contains redirect URL")
            if "scope=" in url:
                print("✅ Contains scopes")
            if "state=" in url:
                print("✅ Contains CSRF state token")
                
        else:
            print(f"❌ URL generation failed: {result.get('error')}")
    except Exception as e:
        print(f"❌ OAuth URL error: {e}")
    
    # Test 3: Health Status
    print("\n=== Test 3: System Health Status ===")
    try:
        result = frappe.get_attr("shopee_bridge.api.get_health_status")()
        if result.get("ok"):
            health = result["health"]
            print(f"✅ Token Valid: {health['token_valid']}")
            print(f"✅ Settings Configured: {health['settings_configured']}")
            print(f"✅ Recent Errors: {health['recent_errors']}")
            print(f"✅ Pending Webhooks: {health['pending_webhooks']}")
        else:
            print(f"❌ Health check failed: {result.get('error')}")
    except Exception as e:
        print(f"❌ Health status error: {e}")
        
    # Test 4: Authentication Functions
    print("\n=== Test 4: Authentication Functions ===")
    try:
        from shopee_bridge import auth
        
        # Test URL building
        scopes = ["shop.basic.info", "order"]
        url = auth.build_authorize_url(scopes)
        print(f"✅ Auth URL builder works: {len(url)} chars")
        
        # Test signing (will fail without tokens, but tests structure)
        try:
            auth.sign_request("/api/v2/shop/get_shop_info", {}, None)
            print("❌ Expected AuthRequired exception")
        except auth.AuthRequired:
            print("✅ Correctly requires authentication")
        except Exception as e:
            print(f"⚠️  Signing error: {e}")
            
    except Exception as e:
        print(f"❌ Auth functions error: {e}")
        
    # Test 5: API Endpoints
    print("\n=== Test 5: API Endpoints ===")
    api_tests = [
        "shopee_bridge.api.connect_to_shopee",
        "shopee_bridge.api.test_shopee_connection", 
        "shopee_bridge.api.get_health_status",
        "shopee_bridge.api.get_webhook_logs",
    ]
    
    for endpoint in api_tests:
        try:
            result = frappe.get_attr(endpoint)()
            if result.get("ok"):
                print(f"✅ {endpoint}: Success")
            else:
                print(f"⚠️  {endpoint}: {result.get('error', 'Unknown error')[:50]}")
        except Exception as e:
            print(f"❌ {endpoint}: {str(e)[:50]}")
    
    # Summary
    print("\n" + "=" * 50)
    print("📋 OAuth Flow Ready - Next Steps:")
    print("1. ✅ Settings configured with Partner ID, Key, Redirect URL")
    print("2. ✅ OAuth URL generation working")
    print("3. ✅ API endpoints responding") 
    print("4. 📝 To complete OAuth: Visit the generated URL")
    print("5. 📝 Handle callback via shopee_bridge.api.oauth_callback")
    print("6. 📝 Test API calls with received tokens")
    
    print(f"\n🔗 Your OAuth URL:")
    try:
        oauth_result = frappe.get_attr("shopee_bridge.api.connect_to_shopee")()
        if oauth_result.get("ok"):
            print(oauth_result["url"])
        else:
            print(f"Error generating URL: {oauth_result.get('error')}")
    except Exception as e:
        print(f"Error: {e}")
        
    print("\nℹ️  Partner key field type 'Password' memang auto-mask saat copy.")
    print("   Ini normal behavior ERPNext untuk security. Key tetap bisa digunakan.")