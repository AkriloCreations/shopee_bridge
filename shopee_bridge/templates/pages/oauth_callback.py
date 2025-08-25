# File: apps/shopee_bridge/shopee_bridge/templates/pages/oauth_callback.py

import frappe
from urllib.parse import urlencode

def get_context(context):
    """OAuth callback page context."""
    
    # Get parameters from URL
    code = frappe.form_dict.get('code')
    shop_id = frappe.form_dict.get('shop_id') 
    error = frappe.form_dict.get('error')
    error_description = frappe.form_dict.get('error_description')
    
    context.update({
        'code': code,
        'shop_id': shop_id,
        'error': error,
        'error_description': error_description,
        'title': 'Shopee OAuth Callback'
    })
    
    # If we have the required parameters, redirect to Shopee Settings
    if code and shop_id:
        params = urlencode({
            'code': code,
            'shop_id': shop_id
        })
        
        # Set redirect URL
        context['redirect_url'] = f"/app/shopee-settings?{params}"
    elif error:
        # Handle error case
        context['has_error'] = True
        context['error_message'] = f"Authorization failed: {error}"
        if error_description:
            context['error_message'] += f" - {error_description}"