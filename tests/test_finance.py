import unittest
from unittest.mock import patch, MagicMock
from shopee_bridge.services.finance import get_escrow_detail

class TestFinance(unittest.TestCase):
	@patch('shopee_bridge.clients.request_json')
	def test_get_escrow_detail(self, mock_request_json):
		"""Test get_escrow_detail calls request_json and returns dict."""
		mock_request_json.return_value = {"order_sn": "123", "amount": 100}
		result = get_escrow_detail("host", "token", 123, "order123")
		mock_request_json.assert_called_once_with(
			method="GET",
			host="host",
			path="/api/v2/payment/get_escrow_detail",
			query={"order_sn": "order123", "shop_id": 123},
			body=None,
			access_token="token",
			shop_id=123
		)
		self.assertIsInstance(result, dict)
		self.assertEqual(result["order_sn"], "123")

	def test_no_direct_http_calls_in_services(self):
		"""Ensure no direct http_get, http_post, or requests calls in services/ (exclude tests)."""
		import os
		import re
		services_dir = "/home/frappe/frappe-bench/apps/shopee_bridge/shopee_bridge/services"
		violations = []
		for root, dirs, files in os.walk(services_dir):
			for file in files:
				if file.endswith('.py'):
					filepath = os.path.join(root, file)
					with open(filepath, 'r') as f:
						content = f.read()
					# Check for direct calls
					if re.search(r'\bhttp_(get|post)\s*\(', content) or re.search(r'\brequests\s*\.', content):
						violations.append(filepath)
		self.assertEqual(violations, [], f"Direct HTTP calls found in: {violations}")
