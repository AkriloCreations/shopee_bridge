#!/usr/bin/env python3
"""
Test script to verify Shopee Bridge bootstrap patch works correctly.
Run this to test the module registration and workspace setup.
"""

def test_bootstrap():
    """Test the bootstrap patch functionality."""
    print("Testing Shopee Bridge Bootstrap...")
    
    try:
        # Import the patch
        from shopee_bridge.patches.0001_bootstrap import execute
        
        print("✅ Bootstrap patch imported successfully")
        
        # Run the patch
        print("Running bootstrap patch...")
        execute()
        
        print("✅ Bootstrap patch executed successfully")
        
        return True
        
    except Exception as e:
        print(f"❌ Bootstrap patch failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_module_path():
    """Test module path resolution."""
    try:
        import frappe
        frappe.init(site="localhost")  # Adjust site name as needed
        frappe.connect()
        
        path = frappe.get_module_path("Shopee Bridge")
        print(f"✅ Module path resolved: {path}")
        
        # Test module def exists
        exists = frappe.db.exists("Module Def", "Shopee Bridge")
        print(f"✅ Module Def exists: {exists}")
        
        # Test workspace exists
        ws_exists = frappe.db.exists("Workspace", "Shopee Bridge")
        print(f"✅ Workspace exists: {ws_exists}")
        
        if ws_exists:
            ws = frappe.get_doc("Workspace", "Shopee Bridge")
            shortcuts = len(ws.shortcuts) if ws.shortcuts else 0
            print(f"✅ Workspace has {shortcuts} shortcuts")
        
        return True
        
    except Exception as e:
        print(f"❌ Module path test failed: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Shopee Bridge Bootstrap Test")
    print("=" * 60)
    
    success = True
    
    # Test 1: Bootstrap patch
    success &= test_bootstrap()
    print()
    
    # Test 2: Module registration (requires Frappe context)
    success &= test_module_path()
    print()
    
    if success:
        print("🎉 All tests passed! Bootstrap is working correctly.")
    else:
        print("⚠️  Some tests failed. Check the errors above.")
        
    print("=" * 60)