import unittest
from unittest.mock import patch, MagicMock
from shopee_bridge.services.finance import get_escrow_detail

class TestFinance(unittest.TestCase):
	@patch('shopee_bridge.clients.http_post')
	def test_get_escrow_detail(self, mock_http_post):
		"""Test get_escrow_detail calls http_post and returns dict."""
		mock_http_post.return_value = {"response": {"order_sn": "123", "amount": 100}}
		result = get_escrow_detail("host", "token", 123, "order123")
		mock_http_post.assert_called_once_with("/api/v2/payment/get_escrow_detail", json={"order_sn": "order123", "shop_id": 123})
		self.assertIsInstance(result, dict)
		self.assertEqual(result["order_sn"], "123")
