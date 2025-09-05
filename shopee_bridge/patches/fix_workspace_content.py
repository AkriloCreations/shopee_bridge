import frappe
import json
import uuid

def execute():
    """Fix workspace content with proper ERPNext v15 format"""
    
    if not frappe.db.exists("Workspace", "Shopee Bridge"):
        print("Shopee Bridge workspace not found")
        return
    
    ws = frappe.get_doc("Workspace", "Shopee Bridge")
    
    # Create content with proper ERPNext v15 format
    content_blocks = []
    
    # Header block
    content_blocks.append({
        "id": str(uuid.uuid4())[:10],
        "type": "header", 
        "data": {
            "text": "<span class=\"h4\"><b>Shopee Bridge</b></span>",
            "col": 12
        }
    })
    
    # Shortcut blocks - each shortcut is a separate block
    shortcuts = [
        ("Shopee Settings", "Shopee Settings", "Settings", 4),
        ("Webhook Inbox", "Shopee Webhook Inbox", "List", 4), 
        ("Customer Issues", "Customer Issue", "List", 4)
    ]
    
    for label, link_to, view_type, col_size in shortcuts:
        if frappe.db.exists("DocType", link_to):
            content_blocks.append({
                "id": str(uuid.uuid4())[:10],
                "type": "shortcut",
                "data": {
                    "shortcut_name": label,
                    "label": label,
                    "link_to": link_to,
                    "type": "DocType",
                    "doc_view": view_type,
                    "col": col_size
                }
            })
            print(f"Added shortcut block: {label}")
    
    # Update workspace content
    ws.content = json.dumps(content_blocks)
    
    # Also ensure shortcuts table is properly filled
    ws.shortcuts = []
    
    for label, link_to, view_type, _ in shortcuts:
        if frappe.db.exists("DocType", link_to):
            sc = ws.append("shortcuts", {})
            sc.label = label
            sc.type = "DocType" 
            sc.link_to = link_to
            sc.doc_view = view_type if view_type != "Settings" else ""
            sc.color = "blue"
            sc.format = ""
            print(f"Added shortcut row: {label}")
    
    # Save with flags
    ws.flags.ignore_permissions = True
    ws.flags.ignore_mandatory = True
    
    try:
        ws.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"Successfully updated workspace content with {len(content_blocks)} blocks")
        
        # Show final content structure
        print("Final content preview:")
        print(ws.content[:200] + "...")
        
    except Exception as e:
        print(f"Error updating workspace: {str(e)}")
        frappe.log_error(frappe.get_traceback(), "Workspace Content Fix Error")