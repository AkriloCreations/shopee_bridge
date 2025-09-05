"""Health Check System for Shopee Bridge.

This module provides comprehensive health monitoring and diagnostic
capabilities for the Shopee Bridge integration.
"""

from __future__ import annotations

import frappe
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from .bootstrap import ShopeeBootstrap
from .workspace import WorkspaceManager


class HealthChecker:
    """Comprehensive system health monitoring for Shopee Bridge."""
    
    def __init__(self, module_name: str = "Shopee Bridge"):
        self.module_name = module_name
        self.bootstrap = ShopeeBootstrap(verbose=False)
        self.workspace_manager = WorkspaceManager(module_name)
    
    def run_full_check(self) -> Dict[str, Any]:
        """Complete health assessment of the Shopee Bridge system.
        
        Returns:
            Dict containing comprehensive health status
        """
        start_time = datetime.now()
        
        try:
            health_status = {
                "timestamp": start_time.isoformat(),
                "module_name": self.module_name,
                "overall_status": "unknown",
                "checks": {
                    "module_registration": self._check_module_registration(),
                    "workspace_integrity": self._check_workspace_integrity(),
                    "doctype_availability": self._check_doctype_availability(),
                    "custom_fields": self._check_custom_fields(),
                    "settings_config": self._check_settings_config(),
                    "app_structure": self._check_app_structure(),
                    "permissions": self._check_permissions(),
                    "database_integrity": self._check_database_integrity()
                },
                "summary": {},
                "recommendations": [],
                "duration": 0
            }
            
            # Determine overall status
            health_status["overall_status"] = self._determine_overall_status(health_status["checks"])
            
            # Generate summary
            health_status["summary"] = self._generate_summary(health_status["checks"])
            
            # Generate recommendations
            health_status["recommendations"] = self._generate_recommendations(health_status["checks"])
            
            # Calculate duration
            health_status["duration"] = (datetime.now() - start_time).total_seconds()
            
            return health_status
            
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Shopee Health Check Error")
            return {
                "timestamp": start_time.isoformat(),
                "overall_status": "error",
                "error": str(e),
                "duration": (datetime.now() - start_time).total_seconds()
            }
    
    def run_quick_check(self) -> Dict[str, Any]:
        """Quick health check focusing on critical components.
        
        Returns:
            Dict containing basic health status
        """
        start_time = datetime.now()
        
        try:
            checks = {
                "module_registration": self._check_module_registration(),
                "workspace_integrity": self._check_workspace_integrity(),
                "settings_config": self._check_settings_config()
            }
            
            overall_status = self._determine_overall_status(checks)
            
            return {
                "timestamp": start_time.isoformat(),
                "overall_status": overall_status,
                "checks": checks,
                "duration": (datetime.now() - start_time).total_seconds()
            }
            
        except Exception as e:
            return {
                "timestamp": start_time.isoformat(),
                "overall_status": "error",
                "error": str(e),
                "duration": (datetime.now() - start_time).total_seconds()
            }
    
    def get_repair_suggestions(self, health_status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Provide actionable repair suggestions based on health status.
        
        Args:
            health_status: Result from run_full_check() or run_quick_check()
            
        Returns:
            List of repair suggestions with actions and priorities
        """
        suggestions = []
        checks = health_status.get("checks", {})
        
        # Module registration issues
        module_check = checks.get("module_registration", {})
        if module_check.get("status") != "healthy":
            suggestions.append({
                "priority": "high",
                "category": "module_registration",
                "issue": "Module registration issues detected",
                "action": "Run: shopee_bridge.core.bootstrap.run_bootstrap(repair=True)",
                "description": "Fix module definition and path resolution issues",
                "automated": True
            })
        
        # Workspace issues
        workspace_check = checks.get("workspace_integrity", {})
        if workspace_check.get("status") != "healthy":
            suggestions.append({
                "priority": "medium",
                "category": "workspace",
                "issue": "Workspace integrity issues detected",
                "action": "Run: shopee_bridge.core.workspace.repair_workspace()",
                "description": "Fix broken shortcuts and workspace configuration",
                "automated": True
            })
        
        # Settings issues
        settings_check = checks.get("settings_config", {})
        if settings_check.get("status") != "healthy":
            suggestions.append({
                "priority": "medium", 
                "category": "settings",
                "issue": "Shopee Settings not properly configured",
                "action": "Create Shopee Settings document manually or run bootstrap",
                "description": "Initialize default Shopee Settings configuration",
                "automated": True
            })
        
        # DocType issues
        doctype_check = checks.get("doctype_availability", {})
        if doctype_check.get("status") != "healthy":
            missing_doctypes = []
            for dt, info in doctype_check.get("doctypes", {}).items():
                if not info.get("exists", False):
                    missing_doctypes.append(dt)
            
            if missing_doctypes:
                suggestions.append({
                    "priority": "high",
                    "category": "doctypes",
                    "issue": f"Missing DocTypes: {', '.join(missing_doctypes)}",
                    "action": "Reinstall app or check DocType definitions",
                    "description": "Required DocTypes are missing from the system",
                    "automated": False
                })
        
        # Custom fields issues
        custom_fields_check = checks.get("custom_fields", {})
        if custom_fields_check.get("status") == "needs_repair":
            suggestions.append({
                "priority": "medium",
                "category": "custom_fields",
                "issue": "Custom fields may be missing or outdated",
                "action": "Run: shopee_bridge.core.bootstrap.run_bootstrap(force=True)",
                "description": "Recreate custom fields for Shopee integration",
                "automated": True
            })
        
        # Database integrity issues
        db_check = checks.get("database_integrity", {})
        if db_check.get("status") == "error":
            suggestions.append({
                "priority": "high",
                "category": "database",
                "issue": "Database integrity issues detected",
                "action": "Check database connectivity and permissions",
                "description": "Database access issues may prevent proper operation",
                "automated": False
            })
        
        # Sort by priority
        priority_order = {"high": 1, "medium": 2, "low": 3}
        suggestions.sort(key=lambda x: priority_order.get(x["priority"], 999))
        
        return suggestions
    
    def _check_module_registration(self) -> Dict[str, Any]:
        """Check module registration status."""
        return self.bootstrap._check_module_registration()
    
    def _check_workspace_integrity(self) -> Dict[str, Any]:
        """Check workspace integrity and configuration."""
        workspace_status = self.workspace_manager.get_workspace_status()
        
        status = "healthy"
        if not workspace_status.get("exists", False):
            status = "missing"
        elif workspace_status.get("broken_shortcuts", 0) > 0:
            status = "needs_repair" 
        elif workspace_status.get("total_shortcuts", 0) == 0:
            status = "empty"
        
        return {
            **workspace_status,
            "status": status,
            "check_type": "workspace_integrity"
        }
    
    def _check_doctype_availability(self) -> Dict[str, Any]:
        """Check availability and integrity of required DocTypes."""
        return self.bootstrap._check_doctypes()
    
    def _check_custom_fields(self) -> Dict[str, Any]:
        """Check if custom fields are properly created."""
        try:
            # Define expected custom fields
            expected_fields = {
                "Sales Order": ["shopee_order_sn", "buyer_user_id", "buyer_username", "shopee_sync_hash"],
                "Sales Invoice": ["shopee_order_sn", "escrow_synced", "escrow_fee_total", "payout_batch_id"],
                "Delivery Note": ["shopee_order_sn", "package_number", "tracking_number", "status_delivery"]
            }
            
            results = {}
            all_fields_exist = True
            
            for doctype, fields in expected_fields.items():
                if not frappe.db.exists("DocType", doctype):
                    results[doctype] = {
                        "doctype_exists": False,
                        "status": "doctype_missing"
                    }
                    all_fields_exist = False
                    continue
                
                try:
                    meta = frappe.get_meta(doctype)
                    existing_fields = [df.fieldname for df in meta.fields]
                    
                    field_status = {}
                    missing_fields = []
                    
                    for field in fields:
                        exists = field in existing_fields
                        field_status[field] = exists
                        if not exists:
                            missing_fields.append(field)
                            all_fields_exist = False
                    
                    results[doctype] = {
                        "doctype_exists": True,
                        "fields": field_status,
                        "missing_fields": missing_fields,
                        "status": "healthy" if not missing_fields else "missing_fields"
                    }
                    
                except Exception as e:
                    results[doctype] = {
                        "doctype_exists": True,
                        "status": "error",
                        "error": str(e)
                    }
                    all_fields_exist = False
            
            return {
                "doctypes": results,
                "status": "healthy" if all_fields_exist else "needs_repair",
                "check_type": "custom_fields"
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "check_type": "custom_fields"
            }
    
    def _check_settings_config(self) -> Dict[str, Any]:
        """Check Shopee Settings configuration."""
        return self.bootstrap._check_settings()
    
    def _check_app_structure(self) -> Dict[str, Any]:
        """Check app file structure integrity."""
        try:
            import os
            
            app_path = frappe.get_app_path("shopee_bridge")
            
            # Check critical files and directories
            critical_paths = [
                "shopee_bridge/modules.txt",
                "shopee_bridge/hooks.py",
                "shopee_bridge/__init__.py",
                "shopee_bridge/shopee_bridge/__init__.py",
                "shopee_bridge/shopee_bridge/doctype",
                "shopee_bridge/setup/install.py"
            ]
            
            structure_status = {}
            all_exist = True
            
            for path in critical_paths:
                full_path = os.path.join(app_path, path)
                exists = os.path.exists(full_path)
                is_file = os.path.isfile(full_path) if exists else False
                is_dir = os.path.isdir(full_path) if exists else False
                
                structure_status[path] = {
                    "exists": exists,
                    "is_file": is_file,
                    "is_directory": is_dir
                }
                
                if not exists:
                    all_exist = False
            
            return {
                "structure": structure_status,
                "status": "healthy" if all_exist else "incomplete",
                "check_type": "app_structure"
            }
            
        except Exception as e:
            return {
                "status": "error", 
                "error": str(e),
                "check_type": "app_structure"
            }
    
    def _check_permissions(self) -> Dict[str, Any]:
        """Check system permissions and access."""
        try:
            # Basic permission check - try to access system
            can_read_modules = bool(frappe.get_all("Module Def", limit=1))
            can_read_doctypes = bool(frappe.get_all("DocType", limit=1))
            can_read_workspaces = bool(frappe.get_all("Workspace", limit=1))
            
            return {
                "can_read_modules": can_read_modules,
                "can_read_doctypes": can_read_doctypes, 
                "can_read_workspaces": can_read_workspaces,
                "status": "healthy" if all([can_read_modules, can_read_doctypes, can_read_workspaces]) else "limited",
                "check_type": "permissions"
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "check_type": "permissions"
            }
    
    def _check_database_integrity(self) -> Dict[str, Any]:
        """Check database connectivity and basic integrity."""
        try:
            # Test basic database operations
            frappe.db.sql("SELECT 1")
            
            # Check if we can read from key tables
            tables_accessible = True
            try:
                frappe.db.get_list("DocType", limit=1)
                frappe.db.get_list("Module Def", limit=1) 
            except Exception:
                tables_accessible = False
            
            return {
                "database_connected": True,
                "tables_accessible": tables_accessible,
                "status": "healthy" if tables_accessible else "limited",
                "check_type": "database_integrity"
            }
            
        except Exception as e:
            return {
                "database_connected": False,
                "status": "error",
                "error": str(e),
                "check_type": "database_integrity"
            }
    
    def _determine_overall_status(self, checks: Dict[str, Any]) -> str:
        """Determine overall system status from individual checks."""
        if not checks:
            return "unknown"
        
        statuses = [check.get("status", "unknown") for check in checks.values()]
        
        # If any critical check failed, overall is unhealthy
        if "error" in statuses:
            return "error"
        
        # If any check needs repair, overall needs attention
        if any(status in ["needs_repair", "missing", "incomplete", "limited"] for status in statuses):
            return "needs_attention"
        
        # If all checks are healthy, overall is healthy
        if all(status == "healthy" for status in statuses):
            return "healthy"
        
        return "unknown"
    
    def _generate_summary(self, checks: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a summary of health check results."""
        total_checks = len(checks)
        healthy_checks = sum(1 for check in checks.values() if check.get("status") == "healthy")
        error_checks = sum(1 for check in checks.values() if check.get("status") == "error")
        needs_repair_checks = total_checks - healthy_checks - error_checks
        
        return {
            "total_checks": total_checks,
            "healthy_checks": healthy_checks,
            "needs_repair_checks": needs_repair_checks,
            "error_checks": error_checks,
            "health_percentage": int((healthy_checks / total_checks) * 100) if total_checks > 0 else 0
        }
    
    def _generate_recommendations(self, checks: Dict[str, Any]) -> List[str]:
        """Generate high-level recommendations based on check results."""
        recommendations = []
        
        # Check for critical issues
        critical_issues = [
            check_name for check_name, check_result in checks.items()
            if check_result.get("status") in ["error", "missing"]
        ]
        
        if critical_issues:
            recommendations.append(f"Address critical issues in: {', '.join(critical_issues)}")
        
        # Check for repair needs
        repair_needed = [
            check_name for check_name, check_result in checks.items()
            if check_result.get("status") in ["needs_repair", "incomplete"]
        ]
        
        if repair_needed:
            recommendations.append(f"Run repair operations for: {', '.join(repair_needed)}")
        
        # Overall recommendation
        if not critical_issues and not repair_needed:
            recommendations.append("System appears healthy - no immediate action required")
        elif critical_issues:
            recommendations.append("Run full bootstrap with repair: shopee_bridge.core.bootstrap.run_bootstrap(repair=True)")
        elif repair_needed:
            recommendations.append("Run targeted repairs for identified issues")
        
        return recommendations


# Convenience functions for external use
def run_full_health_check(module_name: str = "Shopee Bridge") -> Dict[str, Any]:
    """Run comprehensive health check."""
    checker = HealthChecker(module_name)
    return checker.run_full_check()


def run_quick_health_check(module_name: str = "Shopee Bridge") -> Dict[str, Any]:
    """Run quick health check."""
    checker = HealthChecker(module_name)
    return checker.run_quick_check()


def get_repair_suggestions(health_status: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get repair suggestions for health check results."""
    checker = HealthChecker()
    return checker.get_repair_suggestions(health_status)