import unittest

from shopee_bridge.helpers import (
	epoch_now, now_epoch, safe_int, safe_float, safe_str, safe, truncate_text,
	generate_idempotency_key, deep_get, normalize_order_status,
	is_valid_epoch, calculate_time_diff, format_currency,
	validate_required_fields, create_payload_hash, batch_items, chunks, merge_dicts, ymd_to_epoch
)

class TestHelpers(unittest.TestCase):
	def test_now_epoch(self):
		"""Test now_epoch function."""
		ts = now_epoch()
		self.assertIsInstance(ts, int)
		self.assertGreater(ts, 1609459200)  # After 2021
		
	def test_safe(self):
		"""Test safe function."""
		# Test with dict
		data = {"a": 1, "b": 2}
		result = safe(data)
		self.assertIn('"a":1', result)
		# Test truncation
		long_data = {"key": "x" * 300}
		result = safe(long_data, maxlen=50)
		self.assertEqual(len(result), 50)
		# Test with invalid object
		result = safe(object(), maxlen=10)
		self.assertIsInstance(result, str)
		
	def test_chunks(self):
		"""Test chunks function."""
		items = list(range(10))
		chunks_result = chunks(items, 3)
		self.assertEqual(len(chunks_result), 4)
		self.assertEqual(chunks_result[0], [0, 1, 2])
		self.assertEqual(chunks_result[-1], [9])
		# Test empty
		self.assertEqual(chunks([], 3), [])
		
	def test_ymd_to_epoch(self):
		"""Test ymd_to_epoch function."""
		# Test 2021-01-01
		ts = ymd_to_epoch(2021, 1, 1)
		self.assertEqual(ts, 1609459200)  # Known epoch for 2021-01-01
		# Test 2025-09-05 (current date)
		ts = ymd_to_epoch(2025, 9, 5)
		self.assertIsInstance(ts, int)
		self.assertGreater(ts, 1609459200)