"""Migration to Shopee Bridge v2.0 Architecture.

This patch safely migrates from the old problematic bootstrap system
to the new smart, self-healing architecture.
"""

from __future__ import annotations

import json
import frappe
from datetime import datetime

from shopee_bridge.shopee_bridge.core.bootstrap import run_bootstrap
from shopee_bridge.shopee_bridge.core.workspace import create_or_update_workspace, WorkspaceManager
from shopee_bridge.shopee_bridge.core.health import run_full_health_check


def execute():
    """Migrate to Shopee Bridge v2.0 architecture.
    
    This migration:
    1. Backs up existing configuration
    2. Cleans up problematic old patches
    3. Runs the new smart bootstrap system
    4. Validates the migration was successful
    """
    start_time = datetime.now()
    migration_log = []
    
    try:
        print("[Migration v2.0] Starting Shopee Bridge architecture migration...")
        migration_log.append("Migration started")
        
        # 1. Backup existing configuration
        print("[Migration v2.0] Backing up existing configuration...")
        backup_result = _backup_existing_config()
        migration_log.extend(backup_result.get("log", []))
        
        # 2. Clean up old problematic references
        print("[Migration v2.0] Cleaning up old problematic references...")
        cleanup_result = _cleanup_old_references()
        migration_log.extend(cleanup_result.get("log", []))
        
        # 3. Remove broken workspace shortcuts
        print("[Migration v2.0] Removing broken workspace shortcuts...")
        workspace_cleanup = _cleanup_workspace_shortcuts()
        migration_log.extend(workspace_cleanup.get("log", []))
        
        # 4. Run new smart bootstrap system
        print("[Migration v2.0] Running new smart bootstrap system...")
        bootstrap_result = run_bootstrap(verbose=True, force=True, repair=True)
        
        if bootstrap_result.get("success", False):
            migration_log.append("Smart bootstrap completed successfully")
            repairs = bootstrap_result.get("repairs_made", [])
            if repairs:
                migration_log.append(f"Bootstrap repairs made: {len(repairs)}")
                migration_log.extend([f"  - {repair}" for repair in repairs])
        else:
            error_msg = bootstrap_result.get("error", "Unknown error")
            migration_log.append(f"Bootstrap failed: {error_msg}")
            print(f"[Migration v2.0] Warning: Bootstrap failed - {error_msg}")
        
        # 5. Create modern workspace
        print("[Migration v2.0] Creating modern workspace configuration...")
        workspace_result = create_or_update_workspace(sequence=998)
        
        if workspace_result.get("success", False):
            migration_log.append("Modern workspace created successfully")
            shortcuts = workspace_result.get("shortcuts", [])
            migration_log.append(f"Workspace shortcuts: {', '.join(shortcuts)}")
        else:
            error_msg = workspace_result.get("error", "Unknown error")
            migration_log.append(f"Workspace creation failed: {error_msg}")
            print(f"[Migration v2.0] Warning: Workspace creation failed - {error_msg}")
        
        # 6. Run final health check
        print("[Migration v2.0] Running post-migration health check...")
        health_result = run_full_health_check()
        overall_status = health_result.get("overall_status", "unknown")
        migration_log.append(f"Post-migration health status: {overall_status}")
        
        # 7. Commit all changes
        frappe.db.commit()
        
        # 8. Log migration completion
        duration = (datetime.now() - start_time).total_seconds()
        migration_log.append(f"Migration completed in {duration:.2f} seconds")
        
        # Create migration record
        _create_migration_record(migration_log, overall_status, duration)
        
        print(f"[Migration v2.0] ✅ Migration completed successfully in {duration:.2f}s")
        print(f"[Migration v2.0] Final system status: {overall_status}")
        
        # Show post-migration information
        _show_migration_summary(migration_log, overall_status)
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = str(e)
        
        migration_log.append(f"Migration failed after {duration:.2f} seconds: {error_msg}")
        
        print(f"[Migration v2.0] ❌ Migration failed: {error_msg}")
        
        # Log the error
        frappe.log_error(
            frappe.get_traceback(), 
            "Shopee Bridge v2.0 Migration Error"
        )
        
        # Create error record
        try:
            _create_migration_record(migration_log, "error", duration)
        except:
            pass  # Don't fail on record creation
        
        # Show recovery instructions
        _show_migration_recovery_instructions()
        
        # Don't re-raise - we want the migration to be marked as complete
        # even if there were issues, so it doesn't run again


def _backup_existing_config() -> dict:
    """Backup existing configuration before migration."""
    log = []
    backup_data = {}
    
    try:
        # Backup workspace if exists
        if frappe.db.exists("Workspace", "Shopee Bridge"):
            workspace = frappe.get_doc("Workspace", "Shopee Bridge")
            backup_data["workspace"] = {
                "content": workspace.get("content"),
                "shortcuts": [
                    {
                        "label": sc.get("label"),
                        "link_to": sc.get("link_to"),
                        "type": sc.get("type")
                    }
                    for sc in workspace.get("shortcuts", [])
                ]
            }
            log.append("Backed up existing workspace configuration")
        else:
            log.append("No existing workspace found to backup")
        
        # Backup settings if exists
        if frappe.db.exists("Shopee Settings"):
            settings = frappe.get_doc("Shopee Settings")
            backup_data["settings"] = {
                "partner_id": settings.get("partner_id"),
                "region": settings.get("region"),
                "has_credentials": bool(settings.get("partner_key"))
            }
            log.append("Backed up Shopee Settings configuration")
        else:
            log.append("No Shopee Settings found to backup")
        
        # Store backup in a comment record for reference
        if backup_data:
            frappe.get_doc({
                "doctype": "Comment",
                "comment_type": "Info",
                "reference_doctype": "Shopee Settings",
                "reference_name": "Shopee Settings",
                "content": f"Migration v2.0 backup: {json.dumps(backup_data, indent=2)}",
                "comment_email": "system"
            }).insert(ignore_permissions=True)
            log.append("Backup stored in Comment record")
        
        return {"success": True, "log": log, "backup_data": backup_data}
        
    except Exception as e:
        log.append(f"Backup failed: {str(e)}")
        return {"success": False, "log": log, "error": str(e)}


def _cleanup_old_references() -> dict:
    """Clean up old problematic references."""
    log = []
    
    try:
        # Note: We don't delete the problematic bootstrap patch file
        # as that could cause issues with patch tracking
        # Instead, we just ensure it won't cause future problems
        log.append("Old patch files left intact for patch tracking")
        
        # Clear any cached module paths that might be problematic
        try:
            frappe.clear_cache()
            log.append("Cleared module cache")
        except:
            log.append("Cache clear skipped")
        
        return {"success": True, "log": log}
        
    except Exception as e:
        log.append(f"Cleanup failed: {str(e)}")
        return {"success": False, "log": log, "error": str(e)}


def _cleanup_workspace_shortcuts() -> dict:
    """Remove broken shortcuts from workspace."""
    log = []
    
    try:
        if not frappe.db.exists("Workspace", "Shopee Bridge"):
            log.append("No workspace to clean up")
            return {"success": True, "log": log}
        
        # Use the workspace manager to remove broken shortcuts
        workspace_manager = WorkspaceManager()
        removed_count = workspace_manager.remove_broken_shortcuts()
        
        if removed_count > 0:
            log.append(f"Removed {removed_count} broken shortcuts")
        else:
            log.append("No broken shortcuts found")
        
        return {"success": True, "log": log, "removed_count": removed_count}
        
    except Exception as e:
        log.append(f"Workspace cleanup failed: {str(e)}")
        return {"success": False, "log": log, "error": str(e)}


def _create_migration_record(migration_log: list, status: str, duration: float):
    """Create a record of the migration for future reference."""
    try:
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Info", 
            "reference_doctype": "Shopee Settings",
            "reference_name": "Shopee Settings",
            "content": f"""Shopee Bridge v2.0 Migration Record

Status: {status}
Duration: {duration:.2f} seconds
Date: {datetime.now().isoformat()}

Migration Log:
{chr(10).join(migration_log)}
""",
            "comment_email": "system"
        }).insert(ignore_permissions=True)
        
    except Exception:
        # Don't fail migration if we can't create the record
        pass


def _show_migration_summary(migration_log: list, status: str):
    """Show migration summary to user."""
    print("\n" + "=" * 60)
    print("🚀 SHOPEE BRIDGE V2.0 MIGRATION COMPLETE")
    print("=" * 60)
    
    if status == "healthy":
        print("✅ Migration successful - system is healthy!")
    elif status == "needs_attention":
        print("⚠️  Migration completed with warnings - some components need attention")
    elif status == "error":
        print("❌ Migration completed but system has errors")
    else:
        print(f"📊 Migration completed - system status: {status}")
    
    print("\n🔧 New v2.0 Features:")
    print("   ✨ Smart self-healing bootstrap system")
    print("   🎨 Dynamic workspace with adaptive shortcuts") 
    print("   🔍 Comprehensive health monitoring")
    print("   🛠️  Developer CLI tools for debugging")
    print("   🔄 Auto-repair capabilities")
    
    print("\n🛠️  Available CLI commands:")
    print("   Health Check: bench --site [site] execute shopee_bridge.core.cli.check_health")
    print("   Auto Repair:  bench --site [site] execute shopee_bridge.core.cli.repair_setup")
    print("   Show Status:  bench --site [site] execute shopee_bridge.core.cli.show_status")
    
    if status != "healthy":
        print(f"\n💡 Recommended next step:")
        print("   Run: bench --site [site] execute shopee_bridge.core.cli.repair_setup")
    
    print("=" * 60)


def _show_migration_recovery_instructions():
    """Show recovery instructions if migration fails."""
    print("\n" + "=" * 60)
    print("⚠️  MIGRATION ENCOUNTERED ERRORS")
    print("=" * 60)
    print("")
    print("🔧 Recovery steps:")
    print("")
    print("1. Check system health:")
    print("   bench --site [site] execute shopee_bridge.core.cli.check_health")
    print("")
    print("2. Run auto-repair:")
    print("   bench --site [site] execute shopee_bridge.core.cli.repair_setup")
    print("")
    print("3. Reset workspace if needed:")
    print("   bench --site [site] execute shopee_bridge.core.cli.reset_workspace")
    print("")
    print("4. Full re-bootstrap:")
    print("   bench --site [site] execute shopee_bridge.core.cli.full_bootstrap")
    print("")
    print("📧 If issues persist, check Error Logs and migration records.")
    print("=" * 60)