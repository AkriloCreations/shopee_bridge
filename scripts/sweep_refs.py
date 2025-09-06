#!/usr/bin/env python3
"""Sweep and fix hardcoded DocType references in database.

This utility finds and fixes hardcoded references to:
- shopee_bridge.doctype.*
- frappe.core.doctype.shopee_sync_log
- api/method/shopee_bridge.doctype.*
- shopee_sync_log.shopee_sync_log

And replaces them with proper shopee_bridge.api.* references.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def init_frappe():
    """Initialize Frappe environment."""
    import frappe
    site = os.environ.get('FRAPPE_SITE') or 'site1.local'
    try:
        frappe.init(site=site)
        frappe.connect()
        return True
    except Exception as e:
        print(f"Failed to initialize Frappe: {e}")
        return False

def sweep_client_scripts():
    """Clean up Client Script references."""
    if not init_frappe():
        return
    
    import frappe
    
    patterns = [
        ("shopee_bridge.doctype", "shopee_bridge.api"),
        ("frappe.core.doctype.shopee_sync_log", "shopee_bridge.api"),
        ("api/method/shopee_bridge.doctype", "api/method/shopee_bridge.api"),
        ("shopee_sync_log.shopee_sync_log", "shopee_bridge.api")
    ]
    
    client_scripts = frappe.get_all("Client Script", fields=["name", "script"])
    updated_count = 0
    
    for script in client_scripts:
        original_script = script.script or ""
        modified_script = original_script
        
        for old_pattern, new_pattern in patterns:
            if old_pattern in modified_script:
                modified_script = modified_script.replace(old_pattern, new_pattern)
                
        if modified_script != original_script:
            doc = frappe.get_doc("Client Script", script.name)
            doc.script = modified_script
            doc.save(ignore_permissions=True)
            updated_count += 1
            print(f"Updated Client Script: {script.name}")
    
    print(f"Updated {updated_count} Client Scripts")
    
def sweep_server_scripts():
    """Clean up Server Script references."""
    if not init_frappe():
        return
        
    import frappe
    
    patterns = [
        ("shopee_bridge.doctype", "shopee_bridge.api"),
        ("frappe.core.doctype.shopee_sync_log", "shopee_bridge.api"),
        ("shopee_sync_log.shopee_sync_log", "shopee_bridge.api")
    ]
    
    server_scripts = frappe.get_all("Server Script", fields=["name", "script"])
    updated_count = 0
    
    for script in server_scripts:
        original_script = script.script or ""
        modified_script = original_script
        
        for old_pattern, new_pattern in patterns:
            if old_pattern in modified_script:
                modified_script = modified_script.replace(old_pattern, new_pattern)
                
        if modified_script != original_script:
            doc = frappe.get_doc("Server Script", script.name)
            doc.script = modified_script
            doc.save(ignore_permissions=True)
            updated_count += 1
            print(f"Updated Server Script: {script.name}")
    
    print(f"Updated {updated_count} Server Scripts")

def sweep_workspace_content():
    """Clean up Workspace content references."""
    if not init_frappe():
        return
        
    import frappe
    import json
    
    workspaces = frappe.get_all("Workspace", fields=["name", "content"])
    updated_count = 0
    
    for workspace in workspaces:
        if not workspace.content:
            continue
            
        try:
            content = json.loads(workspace.content)
            modified = False
            
            # Check shortcuts and content for hardcoded references
            for item in content:
                if isinstance(item, dict):
                    # Check data field for link_to values
                    data = item.get("data", {})
                    if isinstance(data, dict):
                        link_to = data.get("link_to", "")
                        if "shopee_bridge.doctype" in link_to:
                            data["link_to"] = link_to.replace("shopee_bridge.doctype", "shopee_bridge.api")
                            modified = True
            
            if modified:
                doc = frappe.get_doc("Workspace", workspace.name)
                doc.content = json.dumps(content)
                doc.save(ignore_permissions=True)
                updated_count += 1
                print(f"Updated Workspace: {workspace.name}")
                
        except (json.JSONDecodeError, TypeError):
            continue
    
    print(f"Updated {updated_count} Workspaces")

def sweep_custom_fields():
    """Clean up Custom Field references."""
    if not init_frappe():
        return
        
    import frappe
    
    # Look for custom fields with hardcoded references in options or default values
    custom_fields = frappe.get_all("Custom Field", 
                                   fields=["name", "options", "default"],
                                   filters={"fieldtype": ["in", ["Link", "Dynamic Link"]]})
    updated_count = 0
    
    for field in custom_fields:
        modified = False
        doc = frappe.get_doc("Custom Field", field.name)
        
        # Check options field
        if field.options and "shopee_bridge.doctype" in field.options:
            doc.options = field.options.replace("shopee_bridge.doctype", "shopee_bridge.api")
            modified = True
            
        # Check default field  
        if field.default and "shopee_bridge.doctype" in field.default:
            doc.default = field.default.replace("shopee_bridge.doctype", "shopee_bridge.api")
            modified = True
            
        if modified:
            doc.save(ignore_permissions=True)
            updated_count += 1
            print(f"Updated Custom Field: {field.name}")
    
    print(f"Updated {updated_count} Custom Fields")

def run_sweep():
    """Run all sweep operations."""
    print("🧹 Starting sweep of hardcoded DocType references...")
    
    try:
        print("\n1. Sweeping Client Scripts...")
        sweep_client_scripts()
        
        print("\n2. Sweeping Server Scripts...")
        sweep_server_scripts()
        
        print("\n3. Sweeping Workspace Content...")
        sweep_workspace_content()
        
        print("\n4. Sweeping Custom Fields...")
        sweep_custom_fields()
        
        print("\n✅ Sweep completed successfully!")
        
        # Commit all changes
        import frappe
        frappe.db.commit()
        
    except Exception as e:
        print(f"\n❌ Sweep failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_sweep()