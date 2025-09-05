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
