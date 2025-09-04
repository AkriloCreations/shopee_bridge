import frappe
import json

def execute():
    """Create workspace shortcuts with complete field data matching ERPNext requirements"""
    
    # Ensure workspace exists
    if not frappe.db.exists("Workspace", "Shopee Bridge"):
        print("Shopee Bridge workspace not found, creating...")
        ws = frappe.new_doc("Workspace")
        ws.name = "Shopee Bridge"
        ws.title = "Shopee Bridge"
        ws.label = "Shopee Bridge"
        ws.module = "Shopee Bridge"
        ws.public = 1
        ws.is_hidden = 0
        ws.sequence_id = 998
        ws.content = "[]"
        ws.flags.ignore_permissions = True
        ws.save(ignore_permissions=True)
        frappe.db.commit()
    else:
        ws = frappe.get_doc("Workspace", "Shopee Bridge")
    
    print(f"Working with workspace: {ws.name}")
    
    # Clear existing shortcuts
    ws.shortcuts = []
    
    # Define shortcuts with complete data as shown in form
    shortcuts_data = [
        {
            "type": "DocType",
            "label": "Shopee Settings", 
            "link_to": "Shopee Settings",
            "doc_view": "",  # Single DocType, no view
            "color": "Grey",
            "format": "",  # No custom format
            "count_filter": "",  # No filters
        },
        {
            "type": "DocType",
            "label": "Webhook Inbox",
            "link_to": "Shopee Webhook Inbox", 
            "doc_view": "List",  # List view
            "color": "Grey",
            "format": "",
            "count_filter": "",
        },
        {
            "type": "DocType", 
            "label": "Customer Issues",
            "link_to": "Customer Issue",
            "doc_view": "List",  # List view
            "color": "Grey", 
            "format": "",
            "count_filter": "",
        }
    ]
    
    # Add shortcuts to workspace
    for shortcut_data in shortcuts_data:
        # Check if DocType exists
        if not frappe.db.exists("DocType", shortcut_data["link_to"]):
            print(f"Skipping {shortcut_data['label']}: DocType {shortcut_data['link_to']} not found")
            continue
            
        # Add shortcut
        shortcut = ws.append("shortcuts", {})
        shortcut.type = shortcut_data["type"]
        shortcut.label = shortcut_data["label"]
        shortcut.link_to = shortcut_data["link_to"]
        shortcut.doc_view = shortcut_data["doc_view"]
        shortcut.color = shortcut_data["color"]
        shortcut.format = shortcut_data["format"]
        # Note: count_filter might need to be handled differently depending on ERPNext version
        
        print(f"Added shortcut: {shortcut_data['label']} -> {shortcut_data['link_to']} ({shortcut_data['doc_view'] or 'Form'})")
    
    # Also update JSON content for compatibility
    json_shortcuts = [{
        "type": "shortcut",
        "label": "Shopee Bridge",
        "items": [
            {"label": "Shopee Settings", "type": "DocType", "link_to": "Shopee Settings"},
            {"label": "Webhook Inbox", "type": "DocType", "link_to": "Shopee Webhook Inbox"},
            {"label": "Customer Issues", "type": "DocType", "link_to": "Customer Issue"},
        ]
    }]
    ws.content = json.dumps(json_shortcuts)
    
    # Save workspace with all flags
    ws.flags.ignore_permissions = True
    ws.flags.ignore_mandatory = True
    ws.flags.ignore_validate = True
    
    try:
        ws.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"Successfully updated workspace with {len(ws.shortcuts)} shortcuts")
        
        # Verify shortcuts were saved
        saved_ws = frappe.get_doc("Workspace", "Shopee Bridge")
        print(f"Verification: Workspace now has {len(saved_ws.shortcuts)} shortcuts")
        for sc in saved_ws.shortcuts:
            print(f"  - {sc.label}: {sc.link_to} ({sc.doc_view or 'Form'})")
            
    except Exception as e:
        print(f"Error saving workspace: {e}")
        frappe.log_error(frappe.get_traceback(), "Workspace Shortcuts Creation Error")