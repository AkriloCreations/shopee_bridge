#!/usr/bin/env python3
"""Diagnostic script for Shopee Bridge workspace and module def."""

import frappe

def diagnose_workspace():
    """Print workspace and module def diagnostic information."""
    print("=== SHOPEE BRIDGE WORKSPACE DIAGNOSTIC ===\n")

    # Check Workspace
    workspace = frappe.get_doc("Workspace", "Shopee Bridge")
    if workspace:
        print("✅ Workspace 'Shopee Bridge' found:")
        print(f"   - Name: {workspace.name}")
        print(f"   - Module: {workspace.module}")
        print(f"   - Public: {workspace.public}")
        print(f"   - Route: {workspace.route}")
        print(f"   - Is Hidden: {workspace.is_hidden}")
        print(f"   - Roles: {[role.role for role in workspace.roles] if workspace.roles else 'None'}")
        print(f"   - Content length: {len(workspace.content) if workspace.content else 0} chars")
    else:
        print("❌ Workspace 'Shopee Bridge' NOT found")

    print()

    # Check Module Def
    module_def = frappe.get_doc("Module Def", "Shopee Bridge")
    if module_def:
        print("✅ Module Def 'Shopee Bridge' found:")
        print(f"   - Name: {module_def.name}")
        print(f"   - Module Name: {module_def.module_name}")
        print(f"   - App Name: {module_def.app_name}")
        print(f"   - Category: {module_def.category}")
    else:
        print("❌ Module Def 'Shopee Bridge' NOT found")

    print("\n=== DIAGNOSTIC COMPLETE ===")

if __name__ == "__main__":
    frappe.init(site="erpdev.managerio.ddns.net")
    frappe.connect()
    try:
        diagnose_workspace()
    finally:
        frappe.destroy()
