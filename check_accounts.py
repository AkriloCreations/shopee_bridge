#!/usr/bin/env python3
"""
Script to check and fix Chart of Accounts setup for Shopee Bridge
This should be run in ERPNext environment
"""

def check_chart_of_accounts():
    """Check if Chart of Accounts is properly set up for Shopee expense accounts"""
    try:
        import frappe

        # Get default company
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if not company:
            return {
                "success": False,
                "error": "No default company found in Global Defaults"
            }

        print(f"Checking Chart of Accounts for company: {company}")

        # Check if company exists
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "error": f"Company '{company}' does not exist"
            }

        # Check for required parent accounts
        parent_candidates = [
            ("account_name", "Indirect Expenses"),
            ("account_name", "Direct Expenses"),
            ("account_name", "Expenses"),
            ("account_name", "Operating Expenses"),
            ("root_type", "Expense")
        ]

        parent_accounts = []
        for field, value in parent_candidates:
            parents = frappe.db.get_all("Account", {
                "company": company,
                field: value,
                "is_group": 1
            }, ["name", "account_name", "root_type"])

            if parents:
                for parent in parents:
                    parent_accounts.append({
                        "name": parent.name,
                        "account_name": parent.account_name,
                        "root_type": parent.root_type
                    })

        if not parent_accounts:
            return {
                "success": False,
                "error": "No suitable parent expense accounts found",
                "suggestion": "Please set up Chart of Accounts with expense accounts"
            }

        # Check currency
        currency = frappe.db.get_value("Company", company, "default_currency") or "IDR"

        return {
            "success": True,
            "company": company,
            "currency": currency,
            "parent_accounts": parent_accounts,
            "message": f"Found {len(parent_accounts)} parent expense accounts"
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def create_basic_expense_accounts():
    """Create basic expense account structure if missing"""
    try:
        import frappe

        company = frappe.db.get_single_value("Global Defaults", "default_company")
        currency = frappe.db.get_value("Company", company, "default_currency") or "IDR"

        # Find root expense account
        root_expense = frappe.db.get_value("Account", {
            "company": company,
            "root_type": "Expense",
            "is_group": 1,
            "parent_account": ("is", "not set")
        }, "name")

        if not root_expense:
            return {
                "success": False,
                "error": "No root expense account found. Please set up Chart of Accounts properly."
            }

        # Create Indirect Expenses if it doesn't exist
        indirect_expenses = frappe.db.get_value("Account", {
            "company": company,
            "account_name": "Indirect Expenses"
        }, "name")

        if not indirect_expenses:
            try:
                acc = frappe.get_doc({
                    "doctype": "Account",
                    "company": company,
                    "account_name": "Indirect Expenses",
                    "parent_account": root_expense,
                    "is_group": 1,
                    "root_type": "Expense",
                    "account_type": "Expense Account",
                    "account_currency": currency,
                })
                acc.insert(ignore_permissions=True)
                indirect_expenses = acc.name
                print(f"Created Indirect Expenses account: {indirect_expenses}")
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Failed to create Indirect Expenses: {e}"
                }

        return {
            "success": True,
            "indirect_expenses": indirect_expenses,
            "message": "Basic expense structure ready"
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

if __name__ == "__main__":
    print("Chart of Accounts Check Script")
    print("=" * 50)

    # This would need to be run in ERPNext context
    print("To run this script:")
    print("1. Open ERPNext Console")
    print("2. Copy and paste the functions above")
    print("3. Run: check_chart_of_accounts()")
    print("4. Run: create_basic_expense_accounts() if needed")
