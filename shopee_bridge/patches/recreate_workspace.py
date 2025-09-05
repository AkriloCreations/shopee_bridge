import frappe
import json

def execute():
    """Delete and recreate workspace from scratch with proper structure"""
    
    # Delete existing workspace completely
    if frappe.db.exists("Workspace", "Shopee Bridge"):
        print("Deleting existing workspace...")
        frappe.delete_doc("Workspace", "Shopee Bridge", force=True)
        frappe.db.commit()
        print("Workspace deleted")
    
    # Wait a moment and clear cache
    frappe.clear_cache()
    
    # Create completely new workspace
    print("Creating new workspace...")
    ws = frappe.new_doc("Workspace")
    
    # Basic properties
    ws.name = "Shopee Bridge"
    ws.title = "Shopee Bridge"
    ws.label = "Shopee Bridge"  
    ws.module = "Shopee Bridge"
    ws.public = 1
    ws.is_hidden = 0
    ws.sequence_id = 998
    ws.icon = ""
    ws.indicator_color = "blue"
    
    # Content with shortcuts in JSON format
    content = [
        {
            "type": "shortcut",
            "label": "Quick Links",
            "items": [
                {
                    "label": "Shopee Settings",
                    "type": "DocType", 
                    "link_to": "Shopee Settings",
                    "description": "Configure Shopee integration settings"
                },
                {
                    "label": "Webhook Inbox",
                    "type": "DocType",
                    "link_to": "Shopee Webhook Inbox", 
                    "description": "View incoming Shopee webhooks"
                },
                {
                    "label": "Customer Issues", 
                    "type": "DocType",
                    "link_to": "Customer Issue",
                    "description": "Handle customer support tickets"
                }
            ]
        }
    ]
    ws.content = json.dumps(content)
    
    # Add shortcuts to child table (this is what shows in workspace)  
    shortcuts = [
        ("Shopee Settings", "Shopee Settings", "", "blue"),
        ("Webhook Inbox", "Shopee Webhook Inbox", "List", "green"), 
        ("Customer Issues", "Customer Issue", "List", "orange")
    ]
    
    for label, doctype, view, color in shortcuts:
        if frappe.db.exists("DocType", doctype):
            sc = ws.append("shortcuts", {})
            sc.label = label
            sc.type = "DocType"
            sc.link_to = doctype
            sc.doc_view = view
            sc.color = color
            sc.format = ""
            print(f"Added shortcut: {label} -> {doctype} ({view or 'Form'})")
        else:
            print(f"Skipped: {doctype} does not exist")
    
    # Set flags and save
    ws.flags.ignore_permissions = True
    ws.flags.ignore_mandatory = True  
    ws.flags.ignore_validate = True
    ws.flags.name_set = True
    
    try:
        ws.insert(ignore_permissions=True)
        frappe.db.commit()
        print(f"Successfully created workspace with {len(ws.shortcuts)} shortcuts")
        
        # Force clear all caches
        frappe.clear_cache()
        
        # Verify creation
        check_ws = frappe.get_doc("Workspace", "Shopee Bridge")
        print(f"Verification: {len(check_ws.shortcuts)} shortcuts saved")
        
    except Exception as e:
        print(f"Error creating workspace: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Workspace Recreation Error")
        
    # Also ensure workspace appears in sidebar
    try:
        from frappe.desk.doctype.workspace.workspace import update_page
        update_page("Shopee Bridge", "Shopee Bridge")
        print("Updated workspace page cache")
    except Exception as e:
        print(f"Note: Could not update page cache: {e}")