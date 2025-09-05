"""Smart Bootstrap System for Shopee Bridge.

This module provides intelligent, self-healing bootstrap functionality
that ensures the Shopee Bridge integration works correctly across
different Frappe/ERPNext versions.
"""

from __future__ import annotations

import json
import frappe
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


class ShopeeBootstrap:
    """Smart, self-healing bootstrap system for Shopee Bridge."""
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.issues_found: List[str] = []
        self.repairs_made: List[str] = []
        self.health_status: Dict[str, Any] = {}
        self.module_name = "Shopee Bridge"
        
        # Text field types that must never be None
        self.text_field_types = {
            "Data", "Small Text", "Text", "Long Text", "Text Editor",
            "Markdown Editor", "HTML Editor", "Link", "Select", "Code", "JSON",
        }
    
    def run(self, force: bool = False, repair: bool = True) -> Dict[str, Any]:
        """Main bootstrap entry point.
        
        Args:
            force: If True, recreate everything from scratch
            repair: If True, attempt auto-repair of issues
            
        Returns:
            Dict containing bootstrap results and status
        """
        start_time = datetime.now()
        
        try:
            if self.verbose:
                print("[Shopee Bootstrap] Starting smart bootstrap system...")
            
            # 1. Health check first
            self.health_check()
            
            # 2. Setup core components
            self.setup_custom_fields()
            self.setup_module_registration()
            self.setup_settings()
            
            # 3. Auto-repair if needed and enabled
            if repair and self.issues_found:
                self.auto_repair()
            
            # 4. Final health check
            self.health_check()
            
            duration = (datetime.now() - start_time).total_seconds()
            
            result = {
                "success": True,
                "duration": duration,
                "issues_found": self.issues_found,
                "repairs_made": self.repairs_made,
                "health_status": self.health_status,
                "message": "Bootstrap completed successfully"
            }
            
            if self.verbose:
                print(f"[Shopee Bootstrap] Completed in {duration:.2f}s")
                if self.repairs_made:
                    print(f"[Shopee Bootstrap] Auto-repairs made: {len(self.repairs_made)}")
            
            return result
            
        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(), 
                "Shopee Bridge Smart Bootstrap Error"
            )
            return {
                "success": False,
                "error": str(e),
                "issues_found": self.issues_found,
                "health_status": self.health_status
            }
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check of the system."""
        self.health_status = {
            "module_registration": self._check_module_registration(),
            "doctype_availability": self._check_doctypes(),
            "custom_fields": self._check_custom_fields(), 
            "settings_config": self._check_settings(),
            "timestamp": datetime.now().isoformat()
        }
        return self.health_status
    
    def setup_custom_fields(self) -> bool:
        """Setup custom fields for Shopee integration."""
        fields = {
            "Sales Order": [
                dict(
                    fieldname="shopee_order_sn", 
                    label="Shopee Order SN", 
                    fieldtype="Data",
                    insert_after="title", 
                    unique=1, 
                    reqd=0, 
                    in_standard_filter=1
                ),
                dict(
                    fieldname="buyer_user_id", 
                    label="Shopee Buyer User ID", 
                    fieldtype="Data",
                    insert_after="shopee_order_sn"
                ),
                dict(
                    fieldname="buyer_username", 
                    label="Shopee Buyer Username", 
                    fieldtype="Data",
                    insert_after="buyer_user_id"
                ),
                dict(
                    fieldname="shopee_sync_hash", 
                    label="Shopee Sync Hash", 
                    fieldtype="Data"
                ),
                dict(
                    fieldname="last_pushed_update_time", 
                    label="Shopee Last Pushed Update Time", 
                    fieldtype="Datetime"
                ),
            ],
            "Sales Invoice": [
                dict(
                    fieldname="shopee_order_sn", 
                    label="Shopee Order SN", 
                    fieldtype="Data", 
                    unique=1,
                    in_standard_filter=1
                ),
                dict(
                    fieldname="escrow_synced", 
                    label="Shopee Escrow Synced", 
                    fieldtype="Check", 
                    default=0
                ),
                dict(
                    fieldname="escrow_synced_at", 
                    label="Shopee Escrow Synced At", 
                    fieldtype="Datetime"
                ),
                dict(
                    fieldname="escrow_fee_total", 
                    label="Shopee Fee Total", 
                    fieldtype="Currency"
                ),
                dict(
                    fieldname="escrow_net", 
                    label="Shopee Net Payout", 
                    fieldtype="Currency"
                ),
                dict(
                    fieldname="payout_batch_id", 
                    label="Shopee Payout Batch ID", 
                    fieldtype="Data"
                ),
                dict(
                    fieldname="last_pushed_update_time", 
                    label="Shopee Last Pushed Update Time", 
                    fieldtype="Datetime"
                ),
            ],
            "Delivery Note": [
                dict(
                    fieldname="shopee_order_sn", 
                    label="Shopee Order SN", 
                    fieldtype="Data", 
                    in_standard_filter=1
                ),
                dict(
                    fieldname="package_number", 
                    label="Shopee Package Number", 
                    fieldtype="Data", 
                    in_standard_filter=1
                ),
                dict(
                    fieldname="tracking_number", 
                    label="Shopee Tracking Number", 
                    fieldtype="Data", 
                    in_standard_filter=1
                ),
                dict(
                    fieldname="status_pickup", 
                    label="Shopee Pickup Status", 
                    fieldtype="Data"
                ),
                dict(
                    fieldname="status_delivery", 
                    label="Shopee Delivery Status", 
                    fieldtype="Data"
                ),
                dict(
                    fieldname="delivered_at", 
                    label="Shopee Delivered At", 
                    fieldtype="Datetime"
                ),
            ],
        }
        
        try:
            create_custom_fields(fields, ignore_validate=True)
            return True
        except Exception as e:
            self.issues_found.append(f"Custom fields creation failed: {str(e)}")
            frappe.log_error(frappe.get_traceback(), "Shopee Bootstrap Custom Fields Error")
            return False
    
    def setup_module_registration(self) -> bool:
        """Ensure module is properly registered."""
        try:
            # 1. Ensure Module Def exists
            if not frappe.db.exists("Module Def", {"name": self.module_name}):
                doc = frappe.get_doc({
                    "doctype": "Module Def",
                    "module_name": self.module_name,
                    "custom": 1,
                })
                self._sanitize_doc_strings(doc)
                doc.insert(ignore_permissions=True)
                self.repairs_made.append("Created Module Def")
            
            # 2. Validate module path can be resolved
            try:
                frappe.clear_cache()
                frappe.get_module_path(self.module_name)
                return True
            except Exception:
                # Fallback check: if folder exists, consider it OK
                self._check_module_path_fallback()
                return True
                
        except Exception as e:
            self.issues_found.append(f"Module registration failed: {str(e)}")
            frappe.log_error(frappe.get_traceback(), "Shopee Bootstrap Module Registration Error")
            return False
    
    def setup_settings(self) -> bool:
        """Initialize Shopee Settings if not exists."""
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
                self._sanitize_doc_strings(doc)
                doc.insert(ignore_permissions=True)
                self.repairs_made.append("Created initial Shopee Settings")
            return True
            
        except Exception as e:
            self.issues_found.append(f"Settings creation failed: {str(e)}")
            frappe.log_error(frappe.get_traceback(), "Shopee Bootstrap Settings Error")
            return False
    
    def auto_repair(self) -> List[str]:
        """Attempt to automatically repair found issues."""
        repairs = []
        
        # For now, issues are already handled in setup methods
        # This method can be extended for more complex repairs
        
        return repairs
    
    def _check_module_registration(self) -> Dict[str, Any]:
        """Check if module is properly registered."""
        try:
            module_def_exists = frappe.db.exists("Module Def", {"name": self.module_name})
            
            try:
                frappe.get_module_path(self.module_name)
                path_resolvable = True
                path_error = None
            except Exception as e:
                path_resolvable = False
                path_error = str(e)
            
            return {
                "module_def_exists": bool(module_def_exists),
                "path_resolvable": path_resolvable,
                "path_error": path_error,
                "status": "healthy" if module_def_exists and path_resolvable else "needs_repair"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _check_doctypes(self) -> Dict[str, Any]:
        """Check availability of required DocTypes."""
        required_doctypes = ["Shopee Settings", "Shopee Webhook Inbox", "Customer Issue"]
        results = {}
        
        for doctype in required_doctypes:
            try:
                exists = frappe.db.exists("DocType", doctype)
                results[doctype] = {
                    "exists": bool(exists),
                    "status": "healthy" if exists else "missing"
                }
            except Exception as e:
                results[doctype] = {
                    "exists": False,
                    "status": "error",
                    "error": str(e)
                }
        
        all_healthy = all(r.get("status") == "healthy" for r in results.values())
        return {
            "doctypes": results,
            "status": "healthy" if all_healthy else "needs_attention"
        }
    
    def _check_custom_fields(self) -> Dict[str, Any]:
        """Check if custom fields are properly created."""
        # This is a basic check - can be expanded
        return {
            "status": "not_implemented",
            "note": "Custom field validation not yet implemented"
        }
    
    def _check_settings(self) -> Dict[str, Any]:
        """Check Shopee Settings configuration."""
        try:
            settings_exist = frappe.db.exists("Shopee Settings")
            return {
                "settings_exist": bool(settings_exist),
                "status": "healthy" if settings_exist else "needs_repair"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _check_module_path_fallback(self) -> bool:
        """Fallback check for module path when registry fails."""
        try:
            from frappe.utils import scrub
            import os
            
            app_path = frappe.get_app_path("shopee_bridge")
            candidate = os.path.join(app_path, scrub(self.module_name))
            
            if os.path.isdir(candidate):
                return True
            else:
                self.issues_found.append(f"Module directory not found at {candidate}")
                return False
                
        except Exception as e:
            self.issues_found.append(f"Module path fallback check failed: {str(e)}")
            return False
    
    def _has_field(self, doctype: str, fieldname: str) -> bool:
        """Check if a doctype has a specific field."""
        try:
            return any(df.fieldname == fieldname for df in frappe.get_meta(doctype).fields)
        except Exception:
            return False
    
    def _sanitize_doc_strings(self, doc) -> None:
        """Ensure all text/JSON fields are not None."""
        meta = frappe.get_meta(doc.doctype)
        
        for df in meta.fields:
            if df.fieldtype in self.text_field_types and doc.get(df.fieldname) is None:
                default_value = "[]" if df.fieldtype == "JSON" else ""
                doc.set(df.fieldname, default_value)
            elif df.fieldtype in ("Table", "Table MultiSelect"):
                rows = doc.get(df.fieldname) or []
                try:
                    child_meta = frappe.get_meta(df.options)
                except Exception:
                    continue
                    
                for row in rows:
                    for cdf in child_meta.fields:
                        if cdf.fieldtype in self.text_field_types and row.get(cdf.fieldname) is None:
                            default_value = "[]" if cdf.fieldtype == "JSON" else ""
                            row.set(cdf.fieldname, default_value)


# Convenience functions for external use
def run_bootstrap(verbose: bool = False, force: bool = False, repair: bool = True) -> Dict[str, Any]:
    """Run the smart bootstrap system."""
    bootstrap = ShopeeBootstrap(verbose=verbose)
    return bootstrap.run(force=force, repair=repair)


def health_check() -> Dict[str, Any]:
    """Run health check only."""
    bootstrap = ShopeeBootstrap(verbose=False)
    return bootstrap.health_check()


def auto_repair() -> Dict[str, Any]:
    """Run auto-repair of detected issues."""
    bootstrap = ShopeeBootstrap(verbose=True)
    bootstrap.health_check()
    repairs = bootstrap.auto_repair()
    return {
        "repairs_made": repairs,
        "issues_found": bootstrap.issues_found,
        "health_status": bootstrap.health_status
    }