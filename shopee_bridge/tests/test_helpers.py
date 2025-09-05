"""Tests for Shopee Bridge helpers."""

import unittest
from shopee_bridge.helpers import (
	epoch_now, safe_int, safe_float, safe_str, truncate_text,
	generate_idempotency_key, deep_get, normalize_order_status,
	is_valid_epoch, calculate_time_diff, format_currency,
	validate_required_fields, create_payload_hash, batch_items, merge_dicts
)


class TestHelpers(unittest.TestCase):
	
	def test_epoch_now(self):
		"""Test epoch timestamp generation."""
		ts = epoch_now()
		self.assertIsInstance(ts, int)
		self.assertGreater(ts, 1609459200)  # After 2021
		
	def test_safe_int(self):
		"""Test safe integer conversion."""
		self.assertEqual(safe_int("123"), 123)
		self.assertEqual(safe_int(456), 456)
		self.assertEqual(safe_int(None), 0)
		self.assertEqual(safe_int("invalid"), 0)
		
	def test_safe_float(self):
		"""Test safe float conversion."""
		self.assertEqual(safe_float("123.45"), 123.45)
		self.assertEqual(safe_float(456.78), 456.78)
		self.assertEqual(safe_float(None), 0.0)
		self.assertEqual(safe_float("invalid"), 0.0)
		
	def test_safe_str(self):
		"""Test safe string conversion."""
		self.assertEqual(safe_str("test"), "test")
		self.assertEqual(safe_str(123), "123")
		self.assertEqual(safe_str(None), "")
		
	def test_truncate_text(self):
		"""Test text truncation."""
		long_text = "a" * 200
		truncated = truncate_text(long_text, 50)
		self.assertEqual(len(truncated), 50)
		self.assertEqual(truncate_text("short"), "short")
		
	def test_generate_idempotency_key(self):
		"""Test idempotency key generation."""
		key1 = generate_idempotency_key("order", "123", "paid")
		key2 = generate_idempotency_key("order", "123", "paid")
		key3 = generate_idempotency_key("order", "456", "paid")
		
		self.assertEqual(key1, key2)
		self.assertNotEqual(key1, key3)
		self.assertIsInstance(key1, str)
		
	def test_deep_get(self):
		"""Test nested dictionary access."""
		data = {"a": {"b": {"c": "value"}}}
		self.assertEqual(deep_get(data, "a", "b", "c"), "value")
		self.assertEqual(deep_get(data, "x", "y"), None)
		self.assertEqual(deep_get(data, "a", "x"), None)
		
	def test_normalize_order_status(self):
		"""Test order status normalization."""
		self.assertEqual(normalize_order_status("PAID"), "paid")
		self.assertEqual(normalize_order_status(" Ready "), "ready")
		self.assertEqual(normalize_order_status(""), "")
		
	def test_is_valid_epoch(self):
		"""Test epoch timestamp validation."""
		current = epoch_now()
		self.assertTrue(is_valid_epoch(current))
		self.assertTrue(is_valid_epoch(current - 86400))  # 1 day ago
		self.assertTrue(is_valid_epoch(current + 86400))  # 1 day from now
		self.assertFalse(is_valid_epoch("invalid"))
		self.assertFalse(is_valid_epoch(None))
		
	def test_calculate_time_diff(self):
		"""Test time difference calculation."""
		ts1 = 1609459200  # 2021-01-01
		ts2 = 1609545600  # 2021-01-02
		self.assertEqual(calculate_time_diff(ts1, ts2), 86400)
		self.assertEqual(calculate_time_diff(ts2, ts1), 86400)
		
	def test_format_currency(self):
		"""Test currency formatting."""
		self.assertEqual(format_currency(1234.56), "IDR 1,234.56")
		self.assertEqual(format_currency(0), "IDR 0.00")
		
	def test_validate_required_fields(self):
		"""Test required field validation."""
		data = {"a": 1, "b": "", "c": None}
		missing = validate_required_fields(data, ["a", "b", "d"])
		self.assertEqual(missing, ["b", "d"])
		
	def test_create_payload_hash(self):
		"""Test payload hash creation."""
		payload1 = {"a": 1, "b": 2}
		payload2 = {"b": 2, "a": 1}  # Same data, different order
		hash1 = create_payload_hash(payload1)
		hash2 = create_payload_hash(payload2)
		
		self.assertEqual(hash1, hash2)
		self.assertIsInstance(hash1, str)
		
	def test_batch_items(self):
		"""Test item batching."""
		items = list(range(10))
		batches = batch_items(items, 3)
		self.assertEqual(len(batches), 4)
		self.assertEqual(batches[0], [0, 1, 2])
		self.assertEqual(batches[-1], [9])
		
	def test_merge_dicts(self):
		"""Test dictionary merging."""
		d1 = {"a": 1, "b": 2}
		d2 = {"b": 3, "c": 4}
		merged = merge_dicts(d1, d2)
		self.assertEqual(merged, {"a": 1, "b": 3, "c": 4})


if __name__ == "__main__":
	unittest.main()
