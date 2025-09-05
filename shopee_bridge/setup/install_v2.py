"""Modern, Simplified Install System for Shopee Bridge.

This module provides a clean, modern post-install hook that leverages
the smart core systems for reliable, self-healing setup.
"""

from __future__ import annotations

import frappe
from datetime import datetime

from shopee_bridge.shopee_bridge.core.bootstrap import run_bootstrap
from shopee_bridge.shopee_bridge.core.workspace import create_or_update_workspace


def after_install():
    """Modern post-install hook for Shopee Bridge.
    
    This function is called automatically after app installation
    and ensures the complete Shopee Bridge system is properly configured.
    """
    start_time = datetime.now()
    
    try:
        print("[Shopee Bridge] Starting modern installation process...")
        
        # 1. Run smart bootstrap system
        print("[Shopee Bridge] Running smart bootstrap...")
        bootstrap_result = run_bootstrap(verbose=True, force=False, repair=True)
        
        if not bootstrap_result.get("success", False):
            error_msg = bootstrap_result.get("error", "Unknown bootstrap error")
            print(f"[Shopee Bridge] Bootstrap failed: {error_msg}")
            frappe.log_error(
                f"Bootstrap failed during installation: {error_msg}",
                "Shopee Bridge Install Error"
            )
            # Don't raise exception - try to continue with workspace setup
        else:
            print("[Shopee Bridge] Bootstrap completed successfully")
            
            # Show what was repaired if anything
            repairs_made = bootstrap_result.get("repairs_made", [])
            if repairs_made:
                print(f"[Shopee Bridge] Auto-repairs made during bootstrap:")
                for repair in repairs_made:
                    print(f"  - {repair}")
        
        # 2. Create/update workspace with dynamic shortcuts
        print("[Shopee Bridge] Setting up workspace...")
        workspace_result = create_or_update_workspace(sequence=998)
        
        if workspace_result.get("success", False):
            print("[Shopee Bridge] Workspace configured successfully")
            shortcuts = workspace_result.get("shortcuts", [])
            if shortcuts:
                print(f"[Shopee Bridge] Added shortcuts: {', '.join(shortcuts)}")
        else:
            error_msg = workspace_result.get("error", "Unknown workspace error")
            print(f"[Shopee Bridge] Workspace setup failed: {error_msg}")
            frappe.log_error(
                f"Workspace setup failed during installation: {error_msg}",
                "Shopee Bridge Install Error"
            )
        
        # 3. Commit all changes
        frappe.db.commit()
        
        # 4. Final summary
        duration = (datetime.now() - start_time).total_seconds()
        print(f"[Shopee Bridge] Installation completed in {duration:.2f} seconds")
        print("[Shopee Bridge] ✅ Ready to use!")
        
        # 5. Show getting started info
        _show_getting_started_info()
        
    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = str(e)
        
        print(f"[Shopee Bridge] Installation failed after {duration:.2f} seconds")
        print(f"[Shopee Bridge] Error: {error_msg}")
        
        frappe.log_error(
            frappe.get_traceback(),
            "Shopee Bridge Installation Fatal Error"
        )
        
        # Show recovery instructions
        _show_recovery_instructions()
        
        # Re-raise to indicate installation failure
        raise


def _show_getting_started_info():
    """Show helpful information for getting started."""
    print("\n" + "=" * 60)
    print("🎉 SHOPEE BRIDGE INSTALLATION COMPLETE!")
    print("=" * 60)
    print("")
    print("📋 What's been set up:")
    print("   ✅ Module registration")
    print("   ✅ Custom fields for Sales Order, Invoice, Delivery Note")  
    print("   ✅ Shopee Settings (Single DocType)")
    print("   ✅ Workspace with dynamic shortcuts")
    print("   ✅ Health monitoring system")
    print("")
    print("🚀 Next steps:")
    print("   1. Go to 'Shopee Bridge' workspace")
    print("   2. Open 'Settings' to configure API credentials")
    print("   3. Test webhook integration")
    print("")
    print("🛠️  Developer tools:")
    print("   Health Check: bench --site [site] execute shopee_bridge.core.cli.check_health")
    print("   Auto Repair:  bench --site [site] execute shopee_bridge.core.cli.repair_setup")
    print("   List Commands: bench --site [site] execute shopee_bridge.core.cli.list_commands")
    print("")
    print("📚 Need help? Check the documentation or logs for detailed information.")
    print("=" * 60)


def _show_recovery_instructions():
    """Show recovery instructions if installation fails."""
    print("\n" + "=" * 60)
    print("⚠️  INSTALLATION ENCOUNTERED ERRORS")
    print("=" * 60)
    print("")
    print("🔧 Recovery options:")
    print("")
    print("1. Run auto-repair:")
    print("   bench --site [site] execute shopee_bridge.core.cli.repair_setup")
    print("")
    print("2. Check system health:")
    print("   bench --site [site] execute shopee_bridge.core.cli.check_health")
    print("")
    print("3. Full re-bootstrap:")
    print("   bench --site [site] execute shopee_bridge.core.cli.full_bootstrap")
    print("")
    print("4. Manual troubleshooting:")
    print("   - Check Error Logs in ERPNext")
    print("   - Verify database connectivity")
    print("   - Check app file permissions")
    print("")
    print("📧 If issues persist, check logs or contact support.")
    print("=" * 60)


# For backward compatibility, create alias
after_install_v2 = after_install