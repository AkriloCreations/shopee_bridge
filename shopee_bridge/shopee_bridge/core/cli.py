"""CLI Commands for Shopee Bridge Development and Management.

This module provides command-line utilities for developers and administrators
to manage, debug, and maintain the Shopee Bridge integration.
"""

from __future__ import annotations

import json
import frappe
from datetime import datetime
from typing import Dict, List, Optional, Any

from .bootstrap import run_bootstrap, health_check, auto_repair
from .workspace import repair_workspace, get_workspace_status
from .health import run_full_health_check, run_quick_health_check, get_repair_suggestions


def check_health():
    """Run comprehensive health check and display results.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.check_health
    """
    print("🔍 Shopee Bridge Health Check")
    print("=" * 50)
    
    try:
        # Run full health check
        result = run_full_health_check()
        
        # Display overall status
        status_emoji = {
            "healthy": "✅",
            "needs_attention": "⚠️",
            "error": "❌",
            "unknown": "❓"
        }
        
        overall_status = result.get("overall_status", "unknown")
        emoji = status_emoji.get(overall_status, "❓")
        
        print(f"\n{emoji} Overall Status: {overall_status.upper()}")
        print(f"⏱️  Check Duration: {result.get('duration', 0):.2f}s")
        
        # Display summary
        summary = result.get("summary", {})
        if summary:
            print(f"\n📊 Summary:")
            print(f"   Total Checks: {summary.get('total_checks', 0)}")
            print(f"   Healthy: {summary.get('healthy_checks', 0)}")
            print(f"   Need Repair: {summary.get('needs_repair_checks', 0)}")
            print(f"   Errors: {summary.get('error_checks', 0)}")
            print(f"   Health Score: {summary.get('health_percentage', 0)}%")
        
        # Display individual checks
        checks = result.get("checks", {})
        if checks:
            print(f"\n🔬 Detailed Checks:")
            for check_name, check_result in checks.items():
                check_status = check_result.get("status", "unknown")
                check_emoji = status_emoji.get(check_status, "❓")
                print(f"   {check_emoji} {check_name.replace('_', ' ').title()}: {check_status}")
        
        # Display recommendations
        recommendations = result.get("recommendations", [])
        if recommendations:
            print(f"\n💡 Recommendations:")
            for i, rec in enumerate(recommendations, 1):
                print(f"   {i}. {rec}")
        
        # Display repair suggestions if needed
        if overall_status in ["needs_attention", "error"]:
            print(f"\n🔧 Repair Actions:")
            suggestions = get_repair_suggestions(result)
            for suggestion in suggestions[:3]:  # Show top 3 suggestions
                priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
                emoji = priority_emoji.get(suggestion["priority"], "⚪")
                print(f"   {emoji} {suggestion['issue']}")
                print(f"      Action: {suggestion['action']}")
                if suggestion.get("automated", False):
                    print("      ✨ Can be fixed automatically")
        
        print("\n" + "=" * 50)
        
        if overall_status == "healthy":
            print("🎉 System is healthy! No action required.")
        else:
            print("⚡ Run 'repair_setup' to fix issues automatically.")
        
    except Exception as e:
        print(f"❌ Health check failed: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Shopee CLI Health Check Error")


def repair_setup():
    """Auto-repair detected issues in Shopee Bridge setup.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.repair_setup
    """
    print("🔧 Shopee Bridge Auto-Repair")
    print("=" * 50)
    
    try:
        # First run a quick health check
        print("📋 Running pre-repair health check...")
        pre_check = run_quick_health_check()
        
        pre_status = pre_check.get("overall_status", "unknown")
        print(f"   Status before repair: {pre_status}")
        
        if pre_status == "healthy":
            print("✅ System is already healthy. No repairs needed.")
            return
        
        # Run bootstrap with repair enabled
        print("\n🚀 Running smart bootstrap with auto-repair...")
        bootstrap_result = run_bootstrap(verbose=True, repair=True)
        
        if bootstrap_result.get("success", False):
            print("✅ Bootstrap completed successfully")
            
            repairs_made = bootstrap_result.get("repairs_made", [])
            if repairs_made:
                print(f"🔧 Repairs made:")
                for repair in repairs_made:
                    print(f"   - {repair}")
        else:
            print(f"❌ Bootstrap failed: {bootstrap_result.get('error', 'Unknown error')}")
        
        # Repair workspace specifically
        print("\n🎨 Repairing workspace configuration...")
        workspace_result = repair_workspace()
        
        if workspace_result.get("success", False):
            workspace_repairs = workspace_result.get("repairs_made", [])
            if workspace_repairs:
                print(f"🔧 Workspace repairs made:")
                for repair in workspace_repairs:
                    print(f"   - {repair}")
            else:
                print("   No workspace repairs needed")
        else:
            print(f"❌ Workspace repair failed: {workspace_result.get('error', 'Unknown error')}")
        
        # Run post-repair health check
        print("\n📋 Running post-repair health check...")
        post_check = run_quick_health_check()
        post_status = post_check.get("overall_status", "unknown")
        
        print(f"   Status after repair: {post_status}")
        
        # Show improvement
        if post_status == "healthy" and pre_status != "healthy":
            print("\n🎉 System successfully repaired and is now healthy!")
        elif post_status != pre_status:
            print(f"\n📈 System status improved from {pre_status} to {post_status}")
        else:
            print(f"\n⚠️  System status unchanged. Manual intervention may be required.")
        
        print("\n" + "=" * 50)
        print("🔍 Run 'check_health' for detailed status information.")
        
    except Exception as e:
        print(f"❌ Repair failed: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Shopee CLI Repair Error")


def reset_workspace():
    """Reset and recreate the Shopee workspace from scratch.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.reset_workspace
    """
    print("🎨 Shopee Bridge Workspace Reset")
    print("=" * 50)
    
    try:
        workspace_name = "Shopee Bridge"
        
        # Check current workspace status
        print("📋 Checking current workspace status...")
        current_status = get_workspace_status()
        
        if current_status.get("exists", False):
            print(f"   Current status: {current_status.get('status', 'unknown')}")
            print(f"   Shortcuts: {current_status.get('total_shortcuts', 0)} total, " +
                  f"{current_status.get('broken_shortcuts', 0)} broken")
        else:
            print("   Workspace does not exist")
        
        # Delete existing workspace
        if frappe.db.exists("Workspace", workspace_name):
            print(f"\n🗑️  Deleting existing workspace '{workspace_name}'...")
            try:
                frappe.delete_doc("Workspace", workspace_name, force=True)
                print("   ✅ Workspace deleted")
            except Exception as e:
                print(f"   ⚠️  Failed to delete workspace: {str(e)}")
        
        # Recreate workspace
        print(f"\n🆕 Creating new workspace '{workspace_name}'...")
        from .workspace import create_or_update_workspace
        
        result = create_or_update_workspace()
        
        if result.get("success", False):
            print("   ✅ Workspace created successfully")
            shortcuts = result.get("shortcuts", [])
            print(f"   📌 Added {len(shortcuts)} shortcuts: {', '.join(shortcuts)}")
        else:
            print(f"   ❌ Failed to create workspace: {result.get('error', 'Unknown error')}")
        
        # Verify new workspace
        print(f"\n📋 Verifying new workspace...")
        new_status = get_workspace_status()
        
        if new_status.get("exists", False):
            print(f"   ✅ Workspace exists")
            print(f"   Status: {new_status.get('status', 'unknown')}")
            print(f"   Shortcuts: {new_status.get('total_shortcuts', 0)} total")
        else:
            print(f"   ❌ Workspace verification failed")
        
        print("\n" + "=" * 50)
        print("🎯 Workspace reset complete!")
        
    except Exception as e:
        print(f"❌ Workspace reset failed: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Shopee CLI Workspace Reset Error")


def full_bootstrap():
    """Run complete bootstrap process with full recreation.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.full_bootstrap
    """
    print("🚀 Shopee Bridge Full Bootstrap")
    print("=" * 50)
    
    try:
        print("⚠️  This will recreate all Shopee Bridge components from scratch.")
        print("📋 Starting comprehensive bootstrap process...")
        
        # Run full bootstrap
        result = run_bootstrap(verbose=True, force=True, repair=True)
        
        if result.get("success", False):
            print("\n✅ Bootstrap completed successfully!")
            
            # Show what was done
            issues_found = result.get("issues_found", [])
            repairs_made = result.get("repairs_made", [])
            
            if issues_found:
                print(f"\n🔍 Issues found and addressed:")
                for issue in issues_found:
                    print(f"   - {issue}")
            
            if repairs_made:
                print(f"\n🔧 Repairs made:")
                for repair in repairs_made:
                    print(f"   - {repair}")
            
            # Show final health status
            health_status = result.get("health_status", {})
            if health_status:
                print(f"\n📊 Final Health Status:")
                for check_name, check_result in health_status.items():
                    if isinstance(check_result, dict) and "status" in check_result:
                        status = check_result.get("status", "unknown")
                        print(f"   - {check_name}: {status}")
            
        else:
            print(f"\n❌ Bootstrap failed: {result.get('error', 'Unknown error')}")
        
        # Final health check
        print(f"\n📋 Running final health verification...")
        final_check = run_quick_health_check()
        final_status = final_check.get("overall_status", "unknown")
        
        status_messages = {
            "healthy": "🎉 System is fully operational!",
            "needs_attention": "⚠️  System needs some attention",
            "error": "❌ System has errors that need manual fixing",
            "unknown": "❓ System status is unclear"
        }
        
        print(f"   {status_messages.get(final_status, 'Unknown status')}")
        
        print("\n" + "=" * 50)
        print("🔍 Run 'check_health' for detailed system status.")
        
    except Exception as e:
        print(f"❌ Full bootstrap failed: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Shopee CLI Full Bootstrap Error")


def show_status():
    """Show current system status in a compact format.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.show_status
    """
    print("📊 Shopee Bridge Status")
    print("=" * 30)
    
    try:
        # Quick health check
        result = run_quick_health_check()
        
        overall_status = result.get("overall_status", "unknown")
        duration = result.get("duration", 0)
        
        # Status indicators
        status_indicators = {
            "healthy": "🟢 HEALTHY",
            "needs_attention": "🟡 NEEDS ATTENTION", 
            "error": "🔴 ERROR",
            "unknown": "⚪ UNKNOWN"
        }
        
        print(f"Status: {status_indicators.get(overall_status, 'UNKNOWN')}")
        print(f"Check Time: {duration:.2f}s")
        
        # Quick check results
        checks = result.get("checks", {})
        if checks:
            print(f"\nQuick Checks:")
            for check_name, check_result in checks.items():
                status = check_result.get("status", "unknown")
                indicator = "✅" if status == "healthy" else "❌"
                name = check_name.replace("_", " ").title()
                print(f"  {indicator} {name}")
        
        if overall_status != "healthy":
            print(f"\n💡 Run 'repair_setup' to fix issues")
        
        print(f"\n🔍 Run 'check_health' for detailed analysis")
        
    except Exception as e:
        print(f"❌ Status check failed: {str(e)}")


def list_commands():
    """Show available CLI commands.
    
    Usage:
        bench --site [site] execute shopee_bridge.core.cli.list_commands
    """
    print("🛠️  Shopee Bridge CLI Commands")
    print("=" * 40)
    
    commands = [
        {
            "name": "check_health",
            "description": "Run comprehensive health check",
            "usage": "shopee_bridge.core.cli.check_health"
        },
        {
            "name": "repair_setup", 
            "description": "Auto-repair detected issues",
            "usage": "shopee_bridge.core.cli.repair_setup"
        },
        {
            "name": "reset_workspace",
            "description": "Reset and recreate workspace",
            "usage": "shopee_bridge.core.cli.reset_workspace"
        },
        {
            "name": "full_bootstrap",
            "description": "Complete system recreation",
            "usage": "shopee_bridge.core.cli.full_bootstrap"
        },
        {
            "name": "show_status",
            "description": "Quick system status check",
            "usage": "shopee_bridge.core.cli.show_status"
        },
        {
            "name": "list_commands",
            "description": "Show this command list",
            "usage": "shopee_bridge.core.cli.list_commands"
        },
        {
            "name": "quick_sync_orders",
            "description": "Quick sync for recent orders",
            "usage": "shopee_bridge.core.cli.quick_sync_orders"
        },
        {
            "name": "audit_recent_orders",
            "description": "Audit recent orders for consistency",
            "usage": "shopee_bridge.core.cli.audit_recent_orders"
        },
        {
            "name": "check_system_health",
            "description": "Check overall system health",
            "usage": "shopee_bridge.core.cli.check_system_health"
        },
        {
            "name": "debug_token_status",
            "description": "Debug OAuth token status",
            "usage": "shopee_bridge.core.cli.debug_token_status"
        }
    ]
    
    for cmd in commands:
        print(f"\n📌 {cmd['name']}")
        print(f"   {cmd['description']}")
        print(f"   bench --site [site] execute {cmd['usage']}")
    
    print(f"\n" + "=" * 40)
    print("💡 Replace [site] with your actual site name")


def quick_sync_orders(minutes: int = 15):
	"""Quick sync for recent orders.
	
	Usage:
		bench --site [site] execute shopee_bridge.core.cli.quick_sync_orders
	"""
	print("🔄 Shopee Bridge Quick Order Sync")
	print("=" * 40)
	
	try:
		from shopee_bridge.services import orders
		
		print(f"📅 Syncing orders from last {minutes} minutes...")
		result = orders.sync_incremental_orders(updated_since_minutes=minutes)
		
		if result.get("orders_found", 0) > 0:
			print("✅ Sync completed successfully")
			print(f"   Orders found: {result.get('orders_found', 0)}")
			print(f"   Orders processed: {result.get('orders_processed', 0)}")
			print(f"   Duration: {result.get('duration_s', 0):.2f}s")
			
			if result.get("errors"):
				print(f"   Errors: {len(result['errors'])}")
				for error in result["errors"][:3]:  # Show first 3 errors
					print(f"     - {error}")
		else:
			print("ℹ️  No orders found in the specified time range")
		
	except Exception as e:
		print(f"❌ Sync failed: {str(e)}")
		import frappe
		frappe.log_error(frappe.get_traceback(), "Shopee CLI Quick Sync")


def audit_recent_orders(days: int = 7):
	"""Audit recent orders for consistency.
	
	Usage:
		bench --site [site] execute shopee_bridge.core.cli.audit_recent_orders
	"""
	print("🔍 Shopee Bridge Order Audit")
	print("=" * 35)
	
	try:
		from shopee_bridge.services import orders
		from shopee_bridge import helpers
		
		end_time = helpers.epoch_now()
		start_time = end_time - (days * 24 * 60 * 60)
		
		print(f"📊 Auditing orders from last {days} days...")
		order_sns = orders.get_order_list(start_time, end_time)
		
		print("✅ Audit completed")
		print(f"   Total orders: {len(order_sns)}")
		print(f"   Time range: {days} days")
		
		if order_sns:
			print(f"   Sample orders: {', '.join(order_sns[:5])}")
			if len(order_sns) > 5:
				print(f"   ... and {len(order_sns) - 5} more")
		
	except Exception as e:
		print(f"❌ Audit failed: {str(e)}")
		import frappe
		frappe.log_error(frappe.get_traceback(), "Shopee CLI Audit")


def check_system_health():
	"""Check overall system health.
	
	Usage:
		bench --site [site] execute shopee_bridge.core.cli.check_system_health
	"""
	print("🏥 Shopee Bridge Health Check")
	print("=" * 35)
	
	try:
		from shopee_bridge.core.health import run_quick_health_check
		
		result = run_quick_health_check()
		
		overall_status = result.get("overall_status", "unknown")
		duration = result.get("duration", 0)
		
		status_emojis = {
			"healthy": "🟢",
			"needs_attention": "🟡", 
			"error": "🔴",
			"unknown": "⚪"
		}
		
		emoji = status_emojis.get(overall_status, "⚪")
		print(f"{emoji} Overall Status: {overall_status.upper()}")
		print(f"⏱️  Check Duration: {duration:.2f}s")
		
		checks = result.get("checks", {})
		if checks:
			print(f"\n🔬 Component Status:")
			for check_name, check_result in checks.items():
				status = check_result.get("status", "unknown")
				check_emoji = status_emojis.get(status, "⚪")
				name = check_name.replace("_", " ").title()
				print(f"   {check_emoji} {name}: {status}")
		
		if overall_status != "healthy":
			print(f"\n💡 Run 'repair_setup' to fix issues")
		
	except Exception as e:
		print(f"❌ Health check failed: {str(e)}")
		import frappe
		frappe.log_error(frappe.get_traceback(), "Shopee CLI Health Check")


def debug_token_status():
	"""Debug OAuth token status.
	
	Usage:
		bench --site [site] execute shopee_bridge.core.cli.debug_token_status
	"""
	print("🔑 Shopee Bridge Token Debug")
	print("=" * 35)
	
	try:
		from shopee_bridge import auth
		
		token_status = auth.get_token_status()
		
		print("📋 Token Status:")
		print(f"   Has Access Token: {token_status.get('has_access_token', False)}")
		print(f"   Has Refresh Token: {token_status.get('has_refresh_token', False)}")
		
		if token_status.get("normalized_expires_at"):
			print(f"   Expires At: {token_status.get('normalized_expires_at')}")
			print(f"   Seconds Remaining: {token_status.get('seconds_remaining', 0)}")
			print(f"   Is Expired: {token_status.get('is_expired', True)}")
			print(f"   Needs Refresh: {token_status.get('needs_refresh', True)}")
		else:
			print("   No expiry information available")
		
		if token_status.get("error"):
			print(f"   Error: {token_status.get('error')}")
		
	except Exception as e:
		print(f"❌ Token debug failed: {str(e)}")
		import frappe
		frappe.log_error(frappe.get_traceback(), "Shopee CLI Token Debug")


# Export CLI functions for easy access
__all__ = [
    "check_health",
    "repair_setup", 
    "reset_workspace",
    "full_bootstrap",
    "show_status",
    "list_commands",
    "quick_sync_orders",
    "audit_recent_orders", 
    "check_system_health",
    "debug_token_status"
]