# shopee_bridge/setup/install.py

from __future__ import annotations

import json
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Field types yang wajib string/JSON (bukan None)
TEXTY = {
    "Data", "Small Text", "Text", "Long Text", "Text Editor",
    "Markdown Editor", "HTML Editor", "Link", "Select", "Code", "JSON",
}


def has_field(doctype: str, fieldname: str) -> bool:
    """Cek apakah sebuah field ada pada skema doctype (lintas-versi)."""
    try:
        return any(df.fieldname == fieldname for df in frappe.get_meta(doctype).fields)
    except Exception:
        return False


def sanitize_doc_strings(doc) -> bool:
    """Pastikan SEMUA field teks/JSON (termasuk child) tidak None."""
    changed = False
    meta = frappe.get_meta(doc.doctype)

    # parent fields
    for df in meta.fields:
        if df.fieldtype in TEXTY:
            val = doc.get(df.fieldname)
            if val is None:
                doc.set(df.fieldname, "[]" if df.fieldtype == "JSON" else "")
                changed = True

        elif df.fieldtype in ("Table", "Table MultiSelect"):
            rows = doc.get(df.fieldname) or []
            if not rows:
                continue
            try:
                child_meta = frappe.get_meta(df.options)
            except Exception:
                continue
            for row in rows:
                for cdf in child_meta.fields:
                    if cdf.fieldtype in TEXTY and (row.get(cdf.fieldname) is None):
                        row.set(cdf.fieldname, "[]" if cdf.fieldtype == "JSON" else "")
                        changed = True

    return changed


def ensure_module_def(mod_name: str):
    """Pastikan Module Def ada (dipakai Workspace/Desk)."""
    if not frappe.db.exists("Module Def", {"name": mod_name}):
        doc = frappe.get_doc({
            "doctype": "Module Def",
            "module_name": mod_name,
            "custom": 1,
        })
        sanitize_doc_strings(doc)
        doc.insert(ignore_permissions=True)


def ensure_workspace(mod_name: str, ws_name: str, seq: int | None = None):
    """Buat/perbarui Workspace secara aman lintas-versi.

    Args:
        mod_name: Nama Module (Module Def)
        ws_name:  Nama Workspace (docname)
        seq:      Urutan tampilan. Jika None, biarkan default.
                  Kalau kamu mau “ke-2 dari bawah”, taruh angka besar (mis. 998).
    """
    dt = "Workspace"

    # selalu reload meta agar field lintas-versi tercover
    try:
        frappe.reload_doc("desk", "doctype", "workspace")
    except Exception:
        pass

    ws = frappe.get_doc(dt, ws_name) if frappe.db.exists(dt, ws_name) else frappe.new_doc(dt)
    if not ws.get("name"):
        ws.set("name", ws_name)
        ws.flags.name_set = True

    def set_if_empty(field, value):
        if has_field(dt, field):
            if (ws.get(field) is None) or (isinstance(ws.get(field), str) and not ws.get(field).strip()):
                ws.set(field, value)

    # title/label/module/visibility
    set_if_empty("title", mod_name or ws_name)
    set_if_empty("label", mod_name or ws_name)
    if has_field(dt, "module"):
        ws.module = mod_name
    if has_field(dt, "public"):
        ws.public = 1
    if has_field(dt, "is_hidden"):
        ws.is_hidden = 0
    if has_field(dt, "description") and ws.get("description") is None:
        ws.description = ""
    if has_field(dt, "icon") and ws.get("icon") is None:
        ws.icon = ""

    # urutan (cover sequence_id / sequence)
    if seq is not None:
        if has_field(dt, "sequence_id"):
            ws.sequence_id = seq
        if has_field(dt, "sequence"):
            ws.sequence = seq

    # konten minimal → string JSON (bukan list)
    if has_field(dt, "content"):
        ws.content = json.dumps([
            {"type": "shortcut", "label": "Shopee", "items": [
                {"label": "Shopee Settings", "type": "DocType", "link_to": "DocType/Shopee Settings"}
            ]}
        ])

    # guard terakhir: zero None untuk semua field teks/json
    sanitize_doc_strings(ws)
    ws.flags.ignore_mandatory = True
    ws.save(ignore_permissions=True)


def ensure_workspace_shortcut(
    workspace: str,
    shortcut_group_label: str,
    item_label: str,
    link_doctype: str | None = None,
    link_to: str | None = None,
    item_type: str = "DocType",
    icon: str | None = None,
    force_update: bool = False,
) -> bool:
    """Tambahkan (idempoten) shortcut baru ke Workspace.

    Struktur konten Workspace (Frappe v14+) yang kita pakai:
    [
        {"type": "shortcut", "label": "Shopee", "items": [
            {"label": "Shopee Settings", "type": "DocType", "link_to": "DocType/Shopee Settings"}
        ]}
    ]

    Fungsi ini akan:
    - Membaca konten JSON yang ada (jika kosong → array baru)
    - Mencari group (object dengan type=shortcut dan label == shortcut_group_label)
    - Menambahkan item baru jika belum ada item dengan label sama
    - Jika sudah ada dan force_update=True maka update field link_to / type / icon

    Args:
        workspace: Nama doc Workspace.
        shortcut_group_label: Label group (misal "Shopee").
        item_label: Label shortcut yang akan ditampilkan.
        link_doctype: Jika diberikan dan link_to tidak, maka link_to otomatis jadi f"DocType/{link_doctype}".
        link_to: Link tujuan (prioritas lebih tinggi daripada link_doctype).
        item_type: Tipe item (DocType, Report, Page, dll).
        icon: Optional ikon (akan disimpan pada item bila diberikan).
        force_update: Jika True dan item sudah ada → update propertinya.
    Returns:
        bool: True jika ada perubahan dan disimpan, False jika tidak ada perubahan.
    """
    dt = "Workspace"
    if not frappe.db.exists(dt, workspace):
        raise ValueError(f"Workspace '{workspace}' belum ada. Pastikan sudah dibuat lewat ensure_workspace().")

    ws = frappe.get_doc(dt, workspace)
    raw = ws.content or "[]"
    try:
        content = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        content = []

    if not isinstance(content, list):  # jaga-jaga format lama
        content = []

    # cari group
    group = None
    for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "shortcut" and blk.get("label") == shortcut_group_label:
            group = blk
            break

    if not group:
        group = {"type": "shortcut", "label": shortcut_group_label, "items": []}
        content.append(group)

    items = group.setdefault("items", [])
    if not isinstance(items, list):
        group["items"] = items = []

    target_link = link_to or (f"DocType/{link_doctype}" if link_doctype else None)
    if not target_link:
        raise ValueError("Harus isi link_doctype atau link_to")

    existing = None
    for it in items:
        if isinstance(it, dict) and it.get("label") == item_label:
            existing = it
            break

    changed = False
    if existing:
        if force_update:
            # update fields jika ada perubahan
            if existing.get("type") != item_type:
                existing["type"] = item_type; changed = True
            if existing.get("link_to") != target_link:
                existing["link_to"] = target_link; changed = True
            if icon and existing.get("icon") != icon:
                existing["icon"] = icon; changed = True
    else:
        new_item = {"label": item_label, "type": item_type, "link_to": target_link}
        if icon:
            new_item["icon"] = icon
        items.append(new_item)
        changed = True

    if changed:
        ws.content = json.dumps(content)
        sanitize_doc_strings(ws)
        ws.save(ignore_permissions=True)
    return changed


def after_install():
    """Idempotent post-install:
    - create custom fields
    - ensure module/workspace
    - seed empty Shopee Settings (single)
    """
    fields = {
        "Sales Order": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data",
                 insert_after="title", unique=1, reqd=0, in_standard_filter=1),
            dict(fieldname="buyer_user_id", label="Shopee Buyer User ID", fieldtype="Data",
                 insert_after="shopee_order_sn"),
            dict(fieldname="buyer_username", label="Shopee Buyer Username", fieldtype="Data",
                 insert_after="buyer_user_id"),
            dict(fieldname="shopee_sync_hash", label="Shopee Sync Hash", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Sales Invoice": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", unique=1,
                 in_standard_filter=1),
            dict(fieldname="escrow_synced", label="Shopee Escrow Synced", fieldtype="Check", default=0),
            dict(fieldname="escrow_synced_at", label="Shopee Escrow Synced At", fieldtype="Datetime"),
            dict(fieldname="escrow_fee_total", label="Shopee Fee Total", fieldtype="Currency"),
            dict(fieldname="escrow_net", label="Shopee Net Payout", fieldtype="Currency"),
            dict(fieldname="payout_batch_id", label="Shopee Payout Batch ID", fieldtype="Data"),
            dict(fieldname="last_pushed_update_time", label="Shopee Last Pushed Update Time", fieldtype="Datetime"),
        ],
        "Delivery Note": [
            dict(fieldname="shopee_order_sn", label="Shopee Order SN", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="package_number", label="Shopee Package Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="tracking_number", label="Shopee Tracking Number", fieldtype="Data", in_standard_filter=1),
            dict(fieldname="status_pickup", label="Shopee Pickup Status", fieldtype="Data"),
            dict(fieldname="status_delivery", label="Shopee Delivery Status", fieldtype="Data"),
            dict(fieldname="delivered_at", label="Shopee Delivered At", fieldtype="Datetime"),
        ],
    }

    mod = "Shopee Bridge"

    try:
        # 1) Custom fields
        try:
            create_custom_fields(fields, ignore_validate=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge create_custom_fields")

        # 2) Module & Workspace (sequence diset 998 = “kedua dari bawah” biasanya)
        try:
            ensure_module_def(mod)
            ensure_workspace(mod, mod, seq=998)  # ubah angka kalau perlu posisi lain
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge ensure workspace")

        # 3) Seed Single Shopee Settings kosong (jika belum ada)
        try:
            if not frappe.db.exists("Shopee Settings"):
                doc = frappe.get_doc({
                    "doctype": "Shopee Settings",
                    "partner_id": 0,
                    "partner_key": "",
                    "region": "",
                    "redirect_url": "",
                    "access_token": "",
                    "refresh_token": "",
                    "token_expires_at": None,
                })
                sanitize_doc_strings(doc)
                doc.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Shopee Bridge seed Shopee Settings")

        frappe.db.commit()
        print("[Shopee Bridge] Install complete: custom fields, workspace & defaults ensured.")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Shopee Bridge after_install fatal")
        print("[Shopee Bridge] Install encountered errors. Check Error Log.")
        raise
