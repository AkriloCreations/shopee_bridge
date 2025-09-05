#!/usr/bin/env python3
"""Smoke test script for Shopee Bridge.

This script performs basic connectivity and functionality tests
to ensure the Shopee Bridge is working correctly.
"""

import sys
import os
import time
from datetime import datetime, timedelta

# Add the app path to sys.path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_imports():
	"""Test that all modules can be imported without errors."""
	print("🔍 Testing imports...")
	
	try:
		import shopee_bridge
		print("✅ shopee_bridge imported successfully")
	except ImportError as e:
		print(f"❌ Failed to import shopee_bridge: {e}")
		return False
	
	try:
		from shopee_bridge import auth, clients, helpers
		print("✅ Core modules imported successfully")
	except ImportError as e:
		print(f"❌ Failed to import core modules: {e}")
		return False
	
	try:
		from shopee_bridge.services import orders, finance
		print("✅ Service modules imported successfully")
	except ImportError as e:
		print(f"❌ Failed to import service modules: {e}")
		return False
	
	return True

def test_helpers():
	"""Test helper functions."""
	print("\n🔧 Testing helper functions...")
	
	try:
		from shopee_bridge import helpers
		
		# Test epoch timestamp
		ts = helpers.epoch_now()
		assert isinstance(ts, int)
		assert ts > 1609459200  # After 2021
		print("✅ epoch_now() works")
		
		# Test safe conversions
		assert helpers.safe_int("123") == 123
		assert helpers.safe_float("45.67") == 45.67
		assert helpers.safe_str(123) == "123"
		print("✅ Safe conversion functions work")
		
		# Test idempotency key generation
		key1 = helpers.generate_idempotency_key("test", "123")
		key2 = helpers.generate_idempotency_key("test", "123")
		assert key1 == key2
		print("✅ Idempotency key generation works")
		
		return True
	except Exception as e:
		print(f"❌ Helper tests failed: {e}")
		return False

def test_auth_structure():
	"""Test auth module structure without making API calls."""
	print("\n🔐 Testing auth module structure...")
	
	try:
		from shopee_bridge import auth
		
		# Check that key functions exist
		assert hasattr(auth, 'build_authorize_url')
		assert hasattr(auth, 'sign_request')
		assert hasattr(auth, 'refresh_if_needed')
		print("✅ Auth functions are available")
		
		return True
	except Exception as e:
		print(f"❌ Auth structure test failed: {e}")
		return False

def test_clients_structure():
	"""Test clients module structure."""
	print("\n🌐 Testing clients module structure...")
	
	try:
		from shopee_bridge import clients
		
		# Check that key functions exist
		assert hasattr(clients, 'http_get')
		assert hasattr(clients, 'http_post')
		assert hasattr(clients, 'paginate_get')
		print("✅ Client functions are available")
		
		# Check exception classes
		assert hasattr(clients, 'ShopeeAPIError')
		assert hasattr(clients, 'ShopeeRateLimitError')
		assert hasattr(clients, 'ShopeeAuthError')
		print("✅ Exception classes are available")
		
		return True
	except Exception as e:
		print(f"❌ Clients structure test failed: {e}")
		return False

def test_services_structure():
	"""Test services module structure."""
	print("\n📦 Testing services module structure...")
	
	try:
		from shopee_bridge.services import orders, finance
		
		# Check orders service
		assert hasattr(orders, 'get_order_list')
		assert hasattr(orders, 'get_order_detail')
		assert hasattr(orders, 'sync_incremental_orders')
		print("✅ Orders service functions are available")
		
		# Check finance service
		assert hasattr(finance, 'get_escrow_detail')
		assert hasattr(finance, 'sync_escrow_for_order')
		print("✅ Finance service functions are available")
		
		return True
	except Exception as e:
		print(f"❌ Services structure test failed: {e}")
		return False

def test_webhook_structure():
	"""Test webhook module structure."""
	print("\n🪝 Testing webhook module structure...")
	
	try:
		from shopee_bridge import webhook
		
		# Check webhook functions
		assert hasattr(webhook, 'verify_webhook_signature')
		assert hasattr(webhook, 'create_webhook_inbox')
		assert hasattr(webhook, 'process_webhook_inbox')
		print("✅ Webhook functions are available")
		
		return True
	except Exception as e:
		print(f"❌ Webhook structure test failed: {e}")
		return False

def run_smoke_tests():
	"""Run all smoke tests."""
	print("🚀 Starting Shopee Bridge Smoke Tests")
	print("=" * 50)
	
	tests = [
		test_imports,
		test_helpers,
		test_auth_structure,
		test_clients_structure,
		test_services_structure,
		test_webhook_structure,
	]
	
	passed = 0
	total = len(tests)
	
	for test in tests:
		try:
			if test():
				passed += 1
		except Exception as e:
			print(f"❌ Test {test.__name__} crashed: {e}")
	
	print("\n" + "=" * 50)
	print(f"📊 Test Results: {passed}/{total} passed")
	
	if passed == total:
		print("🎉 All smoke tests passed!")
		return True
	else:
		print("⚠️  Some tests failed. Check the output above.")
		return False

if __name__ == "__main__":
	success = run_smoke_tests()
	sys.exit(0 if success else 1)
