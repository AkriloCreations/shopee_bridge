"""Minimal tests for Shopee Bridge.

Run with: python -m pytest tests/test_basic.py
"""

import pytest
import json
from unittest.mock import Mock, patch
import os
import sys

# Mock frappe to avoid import errors in test environment
from unittest.mock import MagicMock
sys.modules['frappe'] = MagicMock()
sys.modules['frappe.utils'] = MagicMock()

# Add the app directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_webhook_idempotency_key():
	"""Test webhook idempotency key derivation."""
	from shopee_bridge.webhook import derive_idempotency_key
	
	# Test with event_id
	payload = {"event_id": "12345", "order_sn": "ORDER001"}
	key = derive_idempotency_key(payload)
	assert key == "12345"
	
	# Test composite key
	payload = {
		"event_type": "order.status_update",
		"order_sn": "ORDER001", 
		"status": "completed",
		"update_time": 1640995200
	}
	key = derive_idempotency_key(payload)
	assert len(key) == 40  # SHA1 hex length
	
	# Test fallback to payload hash
	payload = {"unknown_field": "value"}
	key = derive_idempotency_key(payload)
	assert len(key) == 40

def test_mappers_normalize_sku():
	"""Test SKU normalization."""
	from shopee_bridge.mappers import normalize_sku
	
	assert normalize_sku("ABC-123") == "ABC-123"
	assert normalize_sku("abc 123") == "ABC-123"
	assert normalize_sku("  ABC   123  ") == "ABC-123"
	assert normalize_sku(None) == ""
	assert normalize_sku("") == ""

def test_mappers_order_to_customer():
	"""Test order to customer mapping."""
	from shopee_bridge.mappers import map_order_to_customer
	
	order = {
		"buyer_username": "testuser",
		"buyer_user_id": 12345,
		"recipient_address": {"name": "Test User"}
	}
	
	customer = map_order_to_customer(order)
	assert customer["customer_name"] == "Test User"
	assert customer["buyer_user_id"] == 12345

def test_clients_error_mapping():
	"""Test API error mapping."""
	from shopee_bridge.clients import ShopeeAPIError, ShopeeRateLimitError
	
	# Test rate limit error
	error = ShopeeRateLimitError("Rate limited", 429)
	assert error.status_code == 429
	assert "Rate limited" in str(error)
	
	# Test generic API error
	error = ShopeeAPIError("Server error", 500)
	assert error.status_code == 500

def test_smoke_imports():
	"""Test that all modules can be imported without errors."""
	try:
		import shopee_bridge.clients
		import shopee_bridge.webhook
		import shopee_bridge.mappers
		import shopee_bridge.services.orders
		import shopee_bridge.services.finance
		assert True
	except ImportError as e:
		pytest.fail(f"Import failed: {e}")

@patch('shopee_bridge.clients.request_json')
def test_get_order_list(mock_request_json):
	"""Test get_order_list uses GET and adds required time params."""
	from shopee_bridge.services.orders import get_order_list
	
	# Mock response with no more pages
	mock_request_json.return_value = {
		"response": {
			"order_list": [{"order_sn": "ORDER001"}],
			"more": False
		}
	}
	
	result = get_order_list(1640995200, 1641081600, status="paid", page_size=50)
	
	# Assert request_json was called with GET
	mock_request_json.assert_called_with(
		method="GET",
		host="",
		path="/api/v2/order/get_order_list",
		query={
			"time_range_field": "create_time",
			"time_from": 1640995200,
			"time_to": 1641081600,
			"page_size": 50,
			"order_status": "paid"
		},
		body=None
	)
	
	assert result == ["ORDER001"]

if __name__ == "__main__":
	# Run basic smoke tests
	print("Running smoke tests...")
	
	try:
		test_webhook_idempotency_key()
		print("✅ Webhook idempotency test passed")
	except Exception as e:
		print(f"❌ Webhook test failed: {e}")
	
	try:
		test_mappers_normalize_sku()
		print("✅ SKU normalization test passed")
	except Exception as e:
		print(f"❌ SKU test failed: {e}")
	
	try:
		test_smoke_imports()
		print("✅ Import smoke test passed")
	except Exception as e:
		print(f"❌ Import test failed: {e}")
	
	print("Smoke tests completed!")
