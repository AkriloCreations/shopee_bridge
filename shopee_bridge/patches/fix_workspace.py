import frappe
import json

def execute():
    """Fix workspace shortcuts for Shopee Bridge DocTypes"""
    
    # Check if workspace exists
    if not frappe.db.exists("Workspace", "Shopee Bridge"):
        print("Shopee Bridge workspace not found")
        return
    
    # Get workspace
    ws = frappe.get_doc("Workspace", "Shopee Bridge")
    
    # Clear existing shortcuts
    ws.shortcuts = []
    
    # Add shortcuts for existing DocTypes
    shortcuts_to_add = [
        ("Shopee Settings", "Shopee Settings", ""),  # Single DocType, no view specified
        ("Webhook Inbox", "Shopee Webhook Inbox", "List"),
        ("Customer Issues", "Customer Issue", "List"),
    ]
    
    for label, doctype, view in shortcuts_to_add:
        # Only add if the DocType exists and has proper permissions
        if frappe.db.exists("DocType", doctype):
            shortcut = ws.append("shortcuts", {})
            shortcut.label = label
            shortcut.type = "DocType"
            shortcut.link_to = doctype
            shortcut.doc_view = view
            shortcut.color = "Grey"
            print(f"Added shortcut: {label} -> {doctype}")
        else:
            print(f"Skipped shortcut {label}: DocType {doctype} not found")
    
    # Update JSON content as well
    shortcuts_content = [
        {"type": "shortcut", "label": "Shopee", "items": [
            {"label": "Shopee Settings", "type": "DocType", "link_to": "Shopee Settings"},
            {"label": "Webhook Inbox", "type": "DocType", "link_to": "List/Shopee Webhook Inbox"},
            {"label": "Customer Issues", "type": "DocType", "link_to": "List/Customer Issue"},
        ]}
    ]
    ws.content = json.dumps(shortcuts_content)
    
    # Save workspace
    ws.flags.ignore_mandatory = True
    ws.flags.ignore_permissions = True
    ws.save(ignore_permissions=True)
    frappe.db.commit()
    
    print(f"Workspace 'Shopee Bridge' updated with {len(ws.shortcuts)} shortcuts")