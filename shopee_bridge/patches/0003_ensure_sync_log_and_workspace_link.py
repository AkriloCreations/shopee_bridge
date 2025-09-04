"""Ensure Shopee Sync Log DocType (full schema) + workspace shortcut/link.

Use when earlier consolidated bootstrap missed creating the DocType (e.g. due to
non-standard path layout). This patch programmatically creates the DocType if
the JSON loader did not, then guarantees a workspace shortcut (JSON + child row).
Idempotent and safe to rerun.
"""
from __future__ import annotations

import json
import frappe

DT = "Shopee Sync Log"
WORKSPACE = "Shopee Bridge"

FIELDS = [
    {"fieldname": "sync_type", "fieldtype": "Select", "label": "Sync Type", "options": "orders\nreturns\nshipping\nfinance\nwebhook", "reqd": 1},
    {"fieldname": "status", "fieldtype": "Select", "label": "Status", "options": "success\npartial\nfailed", "reqd": 1},
    {"fieldname": "job", "fieldtype": "Data", "label": "Job", "reqd": 1, "in_standard_filter": 1},
    {"fieldname": "key_ref", "fieldtype": "Data", "label": "Key Reference", "in_standard_filter": 1},
    {"fieldname": "from_ts", "fieldtype": "Datetime", "label": "From"},
    {"fieldname": "to_ts", "fieldtype": "Datetime", "label": "To"},
    {"fieldname": "total", "fieldtype": "Int", "label": "Total"},
    {"fieldname": "success", "fieldtype": "Int", "label": "Success"},
    {"fieldname": "failed", "fieldtype": "Int", "label": "Failed"},
    {"fieldname": "notes", "fieldtype": "Long Text", "label": "Notes"},
    {"fieldname": "payload_sample", "fieldtype": "Long Text", "label": "Payload Sample"},
    {"fieldname": "log_tail", "fieldtype": "Long Text", "label": "Recent Log Lines", "read_only": 1, "description": "Appended by write_log helper; last ~200 lines"},
    {"fieldname": "last_updated", "fieldtype": "Datetime", "label": "Last Updated", "read_only": 1},
]


def _ensure_doctype():
    if frappe.db.exists("DocType", DT):
        return False
    doc = frappe.get_doc({
        "doctype": "DocType",
        "name": DT,
        "module": WORKSPACE,
        "custom": 0,
        "issingle": 0,
        "istable": 0,
        "editable_grid": 0,
        "track_changes": 0,
        "allow_rename": 0,
        "autoname": "naming_series:",
        "naming_series": "SHSYNC-.YYYY.MM.-.#####",
        "fields": FIELDS,
        "permissions": [
            {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "print": 1, "export": 1}
        ],
    })
    doc.insert(ignore_permissions=True)
    frappe.reload_doc("shopee_bridge", "doctype", "shopee_sync_log")
    return True


def _ensure_workspace_shortcut():
    if not frappe.db.exists("Workspace", WORKSPACE):
        # minimal workspace if still missing
        ws = frappe.get_doc({"doctype": "Workspace", "name": WORKSPACE, "title": WORKSPACE, "module": WORKSPACE, "public": 1})
        ws.insert(ignore_permissions=True)
    ws = frappe.get_doc("Workspace", WORKSPACE)
    # JSON content
    raw = ws.content or "[]"
    try:
        content = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        content = []
    if not isinstance(content, list):
        content = []
    group = None
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "shortcut" and blk.get("label") == "Shopee":
            group = blk; break
    if not group:
        group = {"type": "shortcut", "label": "Shopee", "items": []}
        content.append(group)
    items = group.setdefault("items", [])
    if not isinstance(items, list):
        group["items"] = items = []
    changed = False
    found = False
    for it in items:
        if isinstance(it, dict) and it.get("label") == "Sync Log":
            # unify link
            if it.get("link_to") != "List/Shopee Sync Log":
                it["link_to"] = "List/Shopee Sync Log"; changed = True
            found = True
            break
    if not found:
        items.append({"label": "Sync Log", "type": "DocType", "link_to": "List/Shopee Sync Log"}); changed = True
    if changed:
        ws.content = json.dumps(content)
    # Child table shortcut
    existed = False
    for sc in ws.get("shortcuts") or []:
        if (sc.link_to == DT) or (sc.label == "Sync Log"):
            if sc.link_to != DT or sc.doc_view != "List":
                sc.link_to = DT; sc.doc_view = "List"; changed = True
            existed = True
            break
    if not existed:
        row = ws.append("shortcuts", {})
        row.label = "Sync Log"; row.type = "DocType"; row.link_to = DT; row.doc_view = "List"; changed = True
    if changed:
        ws.save(ignore_permissions=True)
        frappe.db.commit()


def execute():  # noqa: D401
    created = False
    try:
        # Try reload from file first (if path correct)
        try:
            frappe.reload_doc("shopee_bridge", "doctype", "shopee_sync_log")
        except Exception:
            pass
        if not frappe.db.exists("DocType", DT):
            created = _ensure_doctype()
        _ensure_workspace_shortcut()
    except Exception as exc:  # pragma: no cover
        frappe.log_error(str(exc), "Shopee Bridge 0003 ensure sync log & shortcut")
        return
    print(f"[Shopee Bridge] Sync Log ensured (created={created}) + workspace shortcut (0003).")
