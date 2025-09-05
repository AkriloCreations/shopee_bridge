# Shopee Bridge Modern Architecture Design

**Version:** 2.0  
**Design Philosophy:** Zero-config, Self-healing, Developer-friendly

## 🏗️ Core Principles

### 1. **Zero Configuration**
- Works immediately after `bench install-app shopee_bridge`
- No manual steps required
- Self-detecting and self-healing

### 2. **Single Source of Truth**
- One bootstrap system to rule them all
- No duplicate code or conflicting logic
- Clear separation of concerns

### 3. **Defensive Programming**
- Graceful degradation when components missing
- Self-repair common issues automatically
- Comprehensive error handling with actionable messages

### 4. **Developer Joy**
- Clear, helpful error messages
- Built-in debugging tools
- Easy to extend and maintain

## 🎯 New Architecture

### Directory Structure
```
shopee_bridge/
├── shopee_bridge/
│   ├── __init__.py                    # App version info
│   ├── modules.txt                    # "Shopee Bridge"
│   ├── hooks.py                       # Minimal, clean hooks
│   │
│   ├── shopee_bridge/                 # Module directory
│   │   ├── __init__.py
│   │   │
│   │   ├── doctype/                   # Existing DocTypes (keep as-is)
│   │   │   ├── shopee_settings/       # ✅ Settings DocType
│   │   │   ├── shopee_webhook_inbox/  # ✅ Webhook DocType
│   │   │   └── customer_issue/        # ✅ Issue DocType
│   │   │
│   │   ├── workspace/                 # Dynamic workspace
│   │   │   └── shopee_bridge/
│   │   │       └── shopee_bridge.json # Self-generated
│   │   │
│   │   └── core/                      # 🆕 Core utilities
│   │       ├── __init__.py
│   │       ├── bootstrap.py           # Smart bootstrap system
│   │       ├── workspace.py           # Dynamic workspace manager
│   │       ├── health.py              # Health check utilities
│   │       └── cli.py                 # Developer CLI commands
│   │
│   ├── setup/                         # Clean install system
│   │   ├── __init__.py
│   │   └── install.py                 # Simplified post-install
│   │
│   └── patches/                       # Clean migration
│       └── v2_0/
│           └── migrate_to_v2.py       # One-time cleanup migration
```

## 🚀 Smart Bootstrap System

### Core Components

#### 1. **bootstrap.py** - The Brain
```python
class ShopeeBootstrap:
    """Smart, self-healing bootstrap system."""
    
    def __init__(self):
        self.issues_found = []
        self.repairs_made = []
        self.health_status = {}
    
    def run(self, force=False, repair=True):
        """Main bootstrap entry point."""
        # 1. Health check first
        # 2. Custom fields setup
        # 3. Module registration
        # 4. Dynamic workspace creation
        # 5. Settings initialization
        # 6. Self-repair if needed
        
    def health_check(self):
        """Comprehensive health check."""
        # Check module registration
        # Validate DocTypes exist
        # Verify workspace integrity
        # Test custom fields
        
    def auto_repair(self):
        """Fix common issues automatically."""
        # Repair module registration
        # Fix workspace shortcuts
        # Recreate missing settings
```

#### 2. **workspace.py** - Dynamic Workspace Manager
```python
class WorkspaceManager:
    """Intelligent workspace management."""
    
    def create_or_update_workspace(self):
        """Create workspace with dynamic shortcuts."""
        shortcuts = self.get_available_shortcuts()
        self.update_workspace_content(shortcuts)
    
    def get_available_shortcuts(self):
        """Only include shortcuts for existing DocTypes."""
        available = []
        for doctype, config in self.doctype_configs.items():
            if frappe.db.exists("DocType", doctype):
                available.append(config)
        return available
    
    def repair_workspace(self):
        """Fix common workspace issues."""
        # Remove broken shortcuts
        # Add missing shortcuts
        # Fix JSON format issues
```

#### 3. **health.py** - Health Check System
```python
class HealthChecker:
    """Comprehensive system health monitoring."""
    
    def run_full_check(self):
        """Complete health assessment."""
        return {
            'module_registration': self.check_module(),
            'workspace_integrity': self.check_workspace(), 
            'doctype_availability': self.check_doctypes(),
            'custom_fields': self.check_custom_fields(),
            'settings_config': self.check_settings()
        }
    
    def get_repair_suggestions(self, issues):
        """Provide actionable repair steps."""
```

## 🔧 Clean Hooks System

### New hooks.py
```python
"""Clean, minimal Frappe hooks."""

# App metadata
app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "Your Team"
app_description = "Modern Shopee ↔ ERPNext integration"
app_version = "2.0.0"

# Post-install bootstrap
after_install = "shopee_bridge.setup.install.after_install"

# Scheduler events (unchanged)
scheduler_events = {
    "cron": {
        "*/10 * * * *": [
            "shopee_bridge.jobs.sync_orders.run",
            "shopee_bridge.jobs.sync_shipping.run",
            "shopee_bridge.jobs.sync_returns.run",
        ]
    }
}

# CLI commands for developers
bench_commands = [
    "shopee_bridge.core.cli.check_health",
    "shopee_bridge.core.cli.repair_setup", 
    "shopee_bridge.core.cli.reset_workspace"
]
```

## 🎨 Dynamic Workspace System

### Smart Shortcut Detection
```python
# Only show shortcuts for available DocTypes
DOCTYPE_CONFIGS = {
    "Shopee Settings": {
        "label": "Settings",
        "type": "DocType", 
        "link_to": "Shopee Settings",
        "icon": "settings",
        "priority": 1
    },
    "Shopee Webhook Inbox": {
        "label": "Webhook Inbox",
        "type": "DocType",
        "link_to": "List/Shopee Webhook Inbox", 
        "icon": "inbox",
        "priority": 2
    },
    "Customer Issue": {
        "label": "Customer Issues",
        "type": "DocType",
        "link_to": "List/Customer Issue",
        "icon": "alert-circle", 
        "priority": 3
    }
}
```

### Self-Updating Workspace
- Automatically adds shortcuts when new DocTypes are created
- Removes shortcuts for missing DocTypes
- Maintains clean JSON structure
- Handles version differences gracefully

## 🛠️ Developer Tools

### CLI Commands
```bash
# Health check
bench --site [site] execute shopee_bridge.core.cli.check_health

# Auto-repair
bench --site [site] execute shopee_bridge.core.cli.repair_setup

# Reset workspace  
bench --site [site] execute shopee_bridge.core.cli.reset_workspace

# Full bootstrap
bench --site [site] execute shopee_bridge.core.cli.full_bootstrap
```

### Health Dashboard
- Module registration status
- Workspace integrity check
- DocType availability
- Custom field validation
- Settings configuration

## 🔄 Migration Strategy

### Phase 1: Clean Migration Patch
```python
# patches/v2_0/migrate_to_v2.py
def execute():
    """One-time migration to clean architecture."""
    
    # 1. Backup existing workspace
    # 2. Remove duplicate bootstrap code
    # 3. Clean up obsolete references
    # 4. Run new bootstrap system
    # 5. Verify everything works
```

### Rollback Safety
- Backup existing configuration before migration
- Ability to rollback if issues occur
- Non-destructive migration process

## ✨ Key Benefits

### For Users
- **Zero manual setup** - Works immediately
- **Self-healing** - Fixes itself when possible
- **Clear errors** - Actionable error messages
- **Reliable** - Consistent behavior across installations

### For Developers  
- **Clean code** - Easy to understand and maintain
- **Debugging tools** - Built-in health checks and repair
- **Extensible** - Easy to add new features
- **Testable** - Clear separation of concerns

### For Administrators
- **Health monitoring** - Know the system status
- **Auto-repair** - Less manual intervention needed
- **Comprehensive logging** - Easy troubleshooting
- **Performance tracking** - Monitor system health

---

**Implementation Priority:**
1. ✅ Smart bootstrap system
2. ✅ Dynamic workspace manager  
3. ✅ Health check utilities
4. ✅ Clean migration patch
5. ✅ Developer CLI tools
6. ✅ Comprehensive testing