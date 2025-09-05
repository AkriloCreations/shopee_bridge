"""Dynamic Workspace Manager for Shopee Bridge.

This module provides intelligent workspace management that automatically
adapts to available DocTypes and maintains clean workspace configuration.
"""

from __future__ import annotations

import json
import frappe
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple


class WorkspaceManager:
    """Intelligent workspace management for Shopee Bridge."""
    
    def __init__(self, module_name: str = "Shopee Bridge"):
        self.module_name = module_name
        self.workspace_name = module_name  # Same as module name
        
        # Configuration for available DocTypes and their shortcuts
        self.doctype_configs = {
            "Shopee Settings": {
                "label": "Settings",
                "type": "DocType",
                "link_to": "Shopee Settings",
                "icon": "settings",
                "priority": 1,
                "description": "Configure Shopee API credentials and settings"
            },
            "Shopee Webhook Inbox": {
                "label": "Webhook Inbox", 
                "type": "DocType",
                "link_to": "List/Shopee Webhook Inbox",
                "icon": "inbox",
                "priority": 2,
                "description": "Manage incoming Shopee webhooks"
            },
            "Customer Issue": {
                "label": "Customer Issues",
                "type": "DocType", 
                "link_to": "List/Customer Issue",
                "icon": "alert-circle",
                "priority": 3,
                "description": "Track and resolve customer issues"
            }
        }
        
        # Text field types that must not be None
        self.text_field_types = {
            "Data", "Small Text", "Text", "Long Text", "Text Editor",
            "Markdown Editor", "HTML Editor", "Link", "Select", "Code", "JSON",
        }
    
    def create_or_update_workspace(self, sequence: int = 998) -> Dict[str, Any]:
        """Create or update workspace with dynamic shortcuts.
        
        Args:
            sequence: Position in workspace list (998 = near bottom)
            
        Returns:
            Dict containing operation results
        """
        try:
            # 1. Get available shortcuts based on existing DocTypes
            available_shortcuts = self.get_available_shortcuts()
            
            # 2. Create or update workspace
            workspace = self._ensure_workspace_document(sequence)
            
            # 3. Update workspace content with available shortcuts
            self._update_workspace_content(workspace, available_shortcuts)
            
            # 4. Save workspace
            workspace.save(ignore_permissions=True)
            
            return {
                "success": True,
                "workspace_name": self.workspace_name,
                "shortcuts_count": len(available_shortcuts),
                "shortcuts": [s["label"] for s in available_shortcuts],
                "message": "Workspace updated successfully"
            }
            
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Shopee Workspace Manager Error")
            return {
                "success": False,
                "error": str(e),
                "workspace_name": self.workspace_name
            }
    
    def get_available_shortcuts(self) -> List[Dict[str, Any]]:
        """Get shortcuts for DocTypes that actually exist."""
        available = []
        
        for doctype, config in self.doctype_configs.items():
            if frappe.db.exists("DocType", doctype):
                available.append({
                    **config,
                    "doctype": doctype  # Add the doctype name for reference
                })
        
        # Sort by priority
        available.sort(key=lambda x: x.get("priority", 999))
        return available
    
    def repair_workspace(self) -> Dict[str, Any]:
        """Fix common workspace issues.
        
        Returns:
            Dict containing repair results
        """
        repairs_made = []
        issues_found = []
        
        try:
            # Check if workspace exists
            if not frappe.db.exists("Workspace", self.workspace_name):
                issues_found.append("Workspace does not exist")
                result = self.create_or_update_workspace()
                if result["success"]:
                    repairs_made.append("Created missing workspace")
                return {
                    "success": result["success"],
                    "repairs_made": repairs_made,
                    "issues_found": issues_found
                }
            
            # Get current workspace
            workspace = frappe.get_doc("Workspace", self.workspace_name)
            
            # Check workspace content
            content_issues = self._check_workspace_content(workspace)
            issues_found.extend(content_issues)
            
            if content_issues:
                # Update workspace with clean content
                result = self.create_or_update_workspace()
                if result["success"]:
                    repairs_made.append("Fixed workspace content issues")
            
            return {
                "success": True,
                "repairs_made": repairs_made,
                "issues_found": issues_found
            }
            
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Shopee Workspace Repair Error")
            return {
                "success": False,
                "error": str(e),
                "repairs_made": repairs_made,
                "issues_found": issues_found
            }
    
    def remove_broken_shortcuts(self) -> int:
        """Remove shortcuts to non-existent DocTypes.
        
        Returns:
            Number of shortcuts removed
        """
        try:
            if not frappe.db.exists("Workspace", self.workspace_name):
                return 0
            
            workspace = frappe.get_doc("Workspace", self.workspace_name)
            removed_count = 0
            
            # Parse current content
            content = self._parse_workspace_content(workspace)
            
            # Find and update shortcut groups
            for group in content:
                if group.get("type") == "shortcut" and "items" in group:
                    items = group["items"]
                    original_count = len(items)
                    
                    # Filter out items linking to non-existent DocTypes
                    group["items"] = [
                        item for item in items
                        if self._is_shortcut_valid(item)
                    ]
                    
                    removed_count += original_count - len(group["items"])
            
            # Update workspace if changes were made
            if removed_count > 0:
                workspace.content = json.dumps(content)
                self._sanitize_doc_strings(workspace)
                workspace.save(ignore_permissions=True)
            
            return removed_count
            
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Shopee Remove Broken Shortcuts Error")
            return 0
    
    def get_workspace_status(self) -> Dict[str, Any]:
        """Get current workspace status and health."""
        try:
            exists = frappe.db.exists("Workspace", self.workspace_name)
            if not exists:
                return {
                    "exists": False,
                    "status": "missing",
                    "message": "Workspace does not exist"
                }
            
            workspace = frappe.get_doc("Workspace", self.workspace_name)
            content = self._parse_workspace_content(workspace)
            
            # Analyze shortcuts
            total_shortcuts = 0
            valid_shortcuts = 0
            broken_shortcuts = 0
            
            for group in content:
                if group.get("type") == "shortcut" and "items" in group:
                    for item in group["items"]:
                        total_shortcuts += 1
                        if self._is_shortcut_valid(item):
                            valid_shortcuts += 1
                        else:
                            broken_shortcuts += 1
            
            status = "healthy"
            if broken_shortcuts > 0:
                status = "needs_repair"
            elif total_shortcuts == 0:
                status = "empty"
            
            return {
                "exists": True,
                "status": status,
                "total_shortcuts": total_shortcuts,
                "valid_shortcuts": valid_shortcuts,
                "broken_shortcuts": broken_shortcuts,
                "module": workspace.get("module"),
                "public": workspace.get("public"),
                "is_hidden": workspace.get("is_hidden")
            }
            
        except Exception as e:
            return {
                "exists": False,
                "status": "error",
                "error": str(e)
            }
    
    def _ensure_workspace_document(self, sequence: int) -> frappe.Document:
        """Ensure workspace document exists and has basic properties."""
        # Reload workspace doctype to handle version differences
        try:
            frappe.reload_doc("desk", "doctype", "workspace")
        except Exception:
            pass
        
        # Get or create workspace
        if frappe.db.exists("Workspace", self.workspace_name):
            workspace = frappe.get_doc("Workspace", self.workspace_name)
        else:
            workspace = frappe.new_doc("Workspace")
            workspace.name = self.workspace_name
            workspace.flags.name_set = True
        
        # Set basic properties
        self._set_workspace_properties(workspace, sequence)
        return workspace
    
    def _set_workspace_properties(self, workspace: frappe.Document, sequence: int) -> None:
        """Set basic workspace properties handling version differences."""
        # Title and label
        if self._has_field("Workspace", "title") and not workspace.get("title"):
            workspace.title = self.module_name
        if self._has_field("Workspace", "label") and not workspace.get("label"):
            workspace.label = self.module_name
        
        # Module association
        if self._has_field("Workspace", "module"):
            workspace.module = self.module_name
        
        # Visibility settings
        if self._has_field("Workspace", "public"):
            workspace.public = 1
        if self._has_field("Workspace", "is_hidden"):
            workspace.is_hidden = 0
        
        # Optional fields
        if self._has_field("Workspace", "description") and not workspace.get("description"):
            workspace.description = "Shopee integration workspace"
        if self._has_field("Workspace", "icon") and not workspace.get("icon"):
            workspace.icon = "shopping-cart"
        
        # Sequence/ordering
        if self._has_field("Workspace", "sequence_id"):
            workspace.sequence_id = sequence
        if self._has_field("Workspace", "sequence"):
            workspace.sequence = sequence
    
    def _update_workspace_content(self, workspace: frappe.Document, shortcuts: List[Dict[str, Any]]) -> None:
        """Update workspace content with available shortcuts."""
        if not self._has_field("Workspace", "content"):
            return
        
        # Create shortcut group
        shortcut_group = {
            "type": "shortcut",
            "label": "Shopee",
            "items": []
        }
        
        # Add available shortcuts
        for shortcut in shortcuts:
            shortcut_group["items"].append({
                "label": shortcut["label"],
                "type": shortcut["type"],
                "link_to": shortcut["link_to"],
                "icon": shortcut.get("icon"),
                "description": shortcut.get("description")
            })
        
        # Set content as JSON string
        content = [shortcut_group] if shortcut_group["items"] else []
        workspace.content = json.dumps(content)
        
        # Ensure all text fields are properly set
        self._sanitize_doc_strings(workspace)
    
    def _parse_workspace_content(self, workspace: frappe.Document) -> List[Dict[str, Any]]:
        """Parse workspace content JSON safely."""
        raw_content = workspace.get("content") or "[]"
        
        try:
            if isinstance(raw_content, str):
                content = json.loads(raw_content)
            else:
                content = raw_content or []
        except (json.JSONDecodeError, TypeError):
            content = []
        
        return content if isinstance(content, list) else []
    
    def _check_workspace_content(self, workspace: frappe.Document) -> List[str]:
        """Check workspace content for issues."""
        issues = []
        content = self._parse_workspace_content(workspace)
        
        # Check for empty content
        if not content:
            issues.append("Workspace has no content")
            return issues
        
        # Check shortcut groups
        for i, group in enumerate(content):
            if not isinstance(group, dict):
                issues.append(f"Group {i} is not a dictionary")
                continue
            
            if group.get("type") == "shortcut":
                items = group.get("items", [])
                if not items:
                    issues.append(f"Shortcut group '{group.get('label', 'Unknown')}' has no items")
                else:
                    # Check individual shortcuts
                    for j, item in enumerate(items):
                        if not self._is_shortcut_valid(item):
                            issues.append(f"Shortcut {j} in group '{group.get('label')}' is broken")
        
        return issues
    
    def _is_shortcut_valid(self, shortcut: Dict[str, Any]) -> bool:
        """Check if a shortcut is valid (points to existing DocType)."""
        if not isinstance(shortcut, dict):
            return False
        
        link_to = shortcut.get("link_to", "")
        if not link_to:
            return False
        
        # Extract DocType name from link
        if link_to.startswith("List/"):
            doctype = link_to[5:]  # Remove "List/" prefix
        else:
            doctype = link_to
        
        # Check if DocType exists
        try:
            return frappe.db.exists("DocType", doctype)
        except Exception:
            return False
    
    def _has_field(self, doctype: str, fieldname: str) -> bool:
        """Check if a doctype has a specific field."""
        try:
            return any(df.fieldname == fieldname for df in frappe.get_meta(doctype).fields)
        except Exception:
            return False
    
    def _sanitize_doc_strings(self, doc: frappe.Document) -> None:
        """Ensure all text/JSON fields are not None."""
        meta = frappe.get_meta(doc.doctype)
        
        for df in meta.fields:
            if df.fieldtype in self.text_field_types and doc.get(df.fieldname) is None:
                default_value = "[]" if df.fieldtype == "JSON" else ""
                doc.set(df.fieldname, default_value)


# Convenience functions for external use
def create_or_update_workspace(module_name: str = "Shopee Bridge", sequence: int = 998) -> Dict[str, Any]:
    """Create or update the Shopee workspace."""
    manager = WorkspaceManager(module_name)
    return manager.create_or_update_workspace(sequence)


def repair_workspace(module_name: str = "Shopee Bridge") -> Dict[str, Any]:
    """Repair workspace issues."""
    manager = WorkspaceManager(module_name)
    return manager.repair_workspace()


def get_workspace_status(module_name: str = "Shopee Bridge") -> Dict[str, Any]:
    """Get workspace status."""
    manager = WorkspaceManager(module_name)
    return manager.get_workspace_status()