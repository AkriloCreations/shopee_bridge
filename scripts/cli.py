#!/usr/bin/env python3
"""Shopee Bridge CLI Tool.

Quick operations for development and debugging.
"""

import sys
import os
import argparse
import json
from datetime import datetime, timedelta
import time

# Add the app directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def init_frappe():
	"""Initialize Frappe environment."""
	import frappe
	from frappe.utils import get_site_path
	
	# Get site name from environment or use default
	site = os.environ.get('FRAPPE_SITE') or 'site1.local'
	
	try:
		frappe.init(site=site)
		frappe.connect()
		return True
	except Exception as e:
		print(f"Failed to initialize Frappe: {e}")
		return False

def audit_orders(days=7):
	"""Audit recent orders."""
	if not init_frappe():
		return
	
	try:
		from shopee_bridge.services import orders
		
		end_time = int(time.time())
		start_time = end_time - (days * 24 * 60 * 60)
		
		order_sns = orders.get_order_list(start_time, end_time)
		
		print(f"📊 Order Audit (last {days} days)")
		print(f"Total orders found: {len(order_sns)}")
		print(f"Sample orders: {order_sns[:5] if order_sns else 'None'}")
		
	except Exception as e:
		print(f"❌ Audit failed: {e}")

def sync_recent(minutes=30):
	"""Sync recent orders."""
	if not init_frappe():
		return
	
	try:
		from shopee_bridge.services import orders
		
		result = orders.sync_incremental_orders(updated_since_minutes=minutes)
		
		print(f"🔄 Recent Order Sync ({minutes} minutes)")
		print(json.dumps(result, indent=2))
		
	except Exception as e:
		print(f"❌ Sync failed: {e}")

def check_health():
	"""Check system health."""
	if not init_frappe():
		return
	
	try:
		from shopee_bridge import auth
		
		token_status = auth.get_token_status()
		
		print("🏥 System Health Check")
		print(f"Access Token: {'✅' if token_status.get('has_access_token') else '❌'}")
		print(f"Refresh Token: {'✅' if token_status.get('has_refresh_token') else '❌'}")
		print(f"Token Expired: {'❌' if token_status.get('is_expired') else '✅'}")
		
		if token_status.get('seconds_remaining'):
			remaining = token_status['seconds_remaining']
			print(f"Seconds until expiry: {remaining}")
		
	except Exception as e:
		print(f"❌ Health check failed: {e}")

def debug_webhook(inbox_name):
	"""Debug webhook payload."""
	if not init_frappe():
		return
	
	try:
		import frappe
		from shopee_bridge.api import debug_webhook_payload
		
		result = debug_webhook_payload(inbox_name)
		
		if result.get('ok'):
			debug_info = result['debug']
			payload = result['payload']
			
			print(f"🐛 Webhook Debug: {inbox_name}")
			print(f"Event Type: {debug_info['event_type']}")
			print(f"Source: {debug_info['source_env']}")
			print(f"Signature Valid: {debug_info['signature_valid']}")
			print(f"Status: {debug_info['status']}")
			print(f"Payload Keys: {debug_info['payload_keys']}")
			print("\nPayload:")
			print(json.dumps(payload, indent=2))
		else:
			print(f"❌ Debug failed: {result.get('error')}")
			
	except Exception as e:
		print(f"❌ Debug failed: {e}")

def main():
	parser = argparse.ArgumentParser(description="Shopee Bridge CLI")
	subparsers = parser.add_subparsers(dest='command', help='Available commands')
	
	# Audit command
	audit_parser = subparsers.add_parser('audit', help='Audit recent orders')
	audit_parser.add_argument('--days', type=int, default=7, help='Days to look back')
	
	# Sync command
	sync_parser = subparsers.add_parser('sync', help='Sync recent orders')
	sync_parser.add_argument('--minutes', type=int, default=30, help='Minutes to look back')
	
	# Health command
	subparsers.add_parser('health', help='Check system health')
	
	# Debug webhook command
	debug_parser = subparsers.add_parser('debug-webhook', help='Debug webhook payload')
	debug_parser.add_argument('inbox_name', help='Webhook inbox record name')
	
	args = parser.parse_args()
	
	if not args.command:
		parser.print_help()
		return
	
	if args.command == 'audit':
		audit_orders(args.days)
	elif args.command == 'sync':
		sync_recent(args.minutes)
	elif args.command == 'health':
		check_health()
	elif args.command == 'debug-webhook':
		debug_webhook(args.inbox_name)

if __name__ == '__main__':
	main()
