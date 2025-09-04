import frappe, json

def execute():
    ws = frappe.get_doc("Workspace", "Shopee Bridge") if frappe.db.exists("Workspace","Shopee Bridge") else frappe.new_doc("Workspace")
    if not ws.get("name"):
        ws.name = "Shopee Bridge"
        ws.flags.name_set = True
    ws.module = "Shopee Bridge"
    ws.public = 1
    ws.is_hidden = 0
    ws.content = json.dumps([
        {"type":"shortcut","label":"Shopee","items":[
            {"label":"Shopee Settings","type":"DocType","link_to":"DocType/Shopee Settings"},
            {"label":"Webhook Inbox","type":"DocType","link_to":"List/Shopee Webhook Inbox"},
            {"label":"Sync Log","type":"DocType","link_to":"Form/Shopee Sync Log"},
        ]}
    ])
    ws.flags.ignore_mandatory = True
    ws.save(ignore_permissions=True)
    frappe.db.commit()
    print("[Shopee Bridge] Workspace fixed.")
