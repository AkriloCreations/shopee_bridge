import unittest
from unittest.mock import patch
from click.testing import CliRunner
from shopee_bridge.cli import cli

class TestCli(unittest.TestCase):
	def setUp(self):
		self.runner = CliRunner()

	@patch('shopee_bridge.api.sync_recent_orders')
	def test_sync_recent(self, mock_sync):
		mock_sync.return_value = {"orders_total": 10, "escrow_logged": 10}
		result = self.runner.invoke(cli, ['sync-recent', '--hours', '1'])
		self.assertEqual(result.exit_code, 0)
		mock_sync.assert_called_once_with(1)

	@patch('shopee_bridge.api.audit_shopee_orders_for_month')
	def test_audit_month(self, mock_audit):
		mock_audit.return_value = {"count": 5}
		result = self.runner.invoke(cli, ['audit-month', '--year', '2025', '--month', '8'])
		self.assertEqual(result.exit_code, 0)
		mock_audit.assert_called_once_with(2025, 8)

	@patch('shopee_bridge.api.debug_sign')
	def test_debug_sign(self, mock_debug):
		mock_debug.return_value = {"path": "/test", "ts": 123, "signature": "sig"}
		result = self.runner.invoke(cli, ['debug-sign', '--path', '/test'])
		self.assertEqual(result.exit_code, 0)
		mock_debug.assert_called_once_with('/test')
