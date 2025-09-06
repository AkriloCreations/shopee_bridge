#!/usr/bin/env python3
"""Diagnostic script for Shopee Bridge workspace shortcuts."""

import frappe
import json

def diagnose_workspace():
    """Print workspace diagnostic information."""
    print("=== SHOPEE BRIDGE WORKSPACE DIAGNOSTIC ===\n")

    try:
        workspace = frappe.get_doc("Workspace", "Shopee Bridge")

        print("✅ Workspace 'Shopee Bridge' found:")
        print(f"   - Name: {workspace.name}")
        print(f"   - Route: {getattr(workspace, 'route', 'N/A')}")
        print(f"   - Module: {getattr(workspace, 'module', 'N/A')}")
        print(f"   - Public: {getattr(workspace, 'public', 'N/A')}")
        print(f"   - Is Hidden: {getattr(workspace, 'is_hidden', 'N/A')}")

        # Parse content blocks
        if workspace.content:
            try:
                content_blocks = json.loads(workspace.content)
                print(f"   - Content blocks: {len(content_blocks)}")
                for i, block in enumerate(content_blocks):
                    if block.get("type") == "shortcut":
                        print(f"     Block {i+1}: {block['data']['label']} -> {block['data']['link_to']}")
            except json.JSONDecodeError as e:
                print(f"   - Content blocks: ERROR parsing JSON - {e}")
        else:
            print("   - Content blocks: None")

        # Check shortcuts child table
        if hasattr(workspace, 'shortcuts') and workspace.shortcuts:
            print(f"   - Shortcuts child rows: {len(workspace.shortcuts)}")
            for i, shortcut in enumerate(workspace.shortcuts):
                print(f"     Shortcut {i+1}: {shortcut.label} -> {shortcut.link_to}")
        else:
            print("   - Shortcuts child rows: None")

    except frappe.DoesNotExistError:
        print("❌ Workspace 'Shopee Bridge' NOT found")
    except Exception as e:
        print(f"❌ Error loading workspace: {e}")

    print("\n=== DIAGNOSTIC COMPLETE ===")

if __name__ == "__main__":
    frappe.init(site="erpdev.managerio.ddns.net")
    frappe.connect()
    try:
        diagnose_workspace()
    finally:
        frappe.destroy()
