// File: apps/shopee_bridge/shopee_bridge/public/js/oauth_handler.js

// Enhanced OAuth handler dengan error handling yang lebih baik
(function() {
    'use strict';
    
    // Namespace untuk Shopee OAuth functions
    window.shopee_oauth = {
        processing: false, // Prevent double processing
        
        // Main OAuth handler
        handleCallback: function() {
            if (this.processing) {
                console.log('OAuth callback already being processed, skipping...');
                return;
            }
            
            const urlParams = new URLSearchParams(window.location.search);
            const code = urlParams.get('code');
            const shop_id = urlParams.get('shop_id');
            const error = urlParams.get('error');
            const error_description = urlParams.get('error_description');
            
            // Handle OAuth errors
            if (error) {
                this.handleOAuthError(error, error_description);
                return;
            }
            
            // Handle successful callback
            if (code && shop_id) {
                this.processCallback(code, shop_id);
            }
        },
        
        // Handle OAuth errors
        handleOAuthError: function(error, description) {
            console.error('OAuth Error:', error, description);
            
            frappe.msgprint({
                title: __('Shopee Authorization Failed'),
                message: `
                    <div class="alert alert-danger">
                        <strong>Error:</strong> ${error}<br>
                        ${description ? `<strong>Description:</strong> ${description}` : ''}
                    </div>
                `,
                indicator: 'red'
            });
            
            // Clean URL after showing error
            this.cleanUrl();
        },
        
        // Process OAuth callback
        processCallback: function(code, shop_id) {
            const isShopeeSettingsPage = window.location.pathname.includes('shopee-settings');
            
            // If not on Shopee Settings page, redirect there
            if (!isShopeeSettingsPage) {
                console.log('OAuth callback detected, redirecting to Shopee Settings...');
                const targetUrl = `/app/shopee-settings?code=${code}&shop_id=${shop_id}`;
                window.location.href = targetUrl;
                return;
            }
            
            // If on Shopee Settings page, process the callback
            console.log('Processing OAuth callback on Shopee Settings page...');
            this.exchangeCode(code, shop_id);
        },
        
        // Exchange code for tokens
        exchangeCode: function(code, shop_id) {
            if (this.processing) return;
            this.processing = true;
            
            frappe.show_progress(__('Connecting to Shopee...'), 30, 100, 'Processing authorization...');
            
            frappe.call({
                method: 'shopee_bridge.shopee_bridge.doctype.shopee_settings.api.exchange_code',
                args: {
                    code: code,
                    shop_id: shop_id
                },
                callback: (r) => {
                    frappe.hide_progress();
                    this.processing = false;
                    
                    if (r.message && r.message.ok) {
                        this.handleSuccess(r.message);
                    } else {
                        this.handleFailure('Invalid response from server');
                    }
                },
                error: (err) => {
                    frappe.hide_progress();
                    this.processing = false;
                    this.handleFailure(err.message || 'Network error occurred');
                }
            });
        },
        
        // Handle successful connection
        handleSuccess: function(response) {
            console.log('Shopee connected successfully:', response);
            
            frappe.show_alert({
                message: __('Shopee connected successfully!'),
                indicator: 'green'
            }, 5);
            
            // Show success message with details
            frappe.msgprint({
                title: __('Connection Successful'),
                message: `
                    <div class="alert alert-success">
                        <i class="fa fa-check-circle"></i> <strong>Connected to Shopee successfully!</strong><br><br>
                        <strong>Details:</strong><br>
                        • Shop ID: ${response.shop_id || 'N/A'}<br>
                        • Token expires at: ${response.expire_at ? new Date(response.expire_at * 1000).toLocaleString() : 'N/A'}<br>
                        • Access token: ${response.access_token_preview || 'Hidden'}
                    </div>
                `,
                indicator: 'green'
            });
            
            // Clean URL and refresh
            this.cleanUrl();
            setTimeout(() => {
                window.location.reload();
            }, 3000);
        },
        
        // Handle connection failure
        handleFailure: function(errorMessage) {
            console.error('OAuth exchange failed:', errorMessage);
            
            frappe.show_alert({
                message: __('Connection failed: ') + errorMessage,
                indicator: 'red'
            }, 8);
            
            frappe.msgprint({
                title: __('Connection Failed'),
                message: `
                    <div class="alert alert-danger">
                        <i class="fa fa-exclamation-triangle"></i> <strong>Failed to connect to Shopee</strong><br><br>
                        <strong>Error:</strong> ${errorMessage}<br><br>
                        <strong>Troubleshooting:</strong><br>
                        • Check your Partner ID and Partner Key<br>
                        • Ensure your redirect URL is correctly configured in Shopee Partner Center<br>
                        • Try generating a new authorization URL
                    </div>
                `,
                indicator: 'red'
            });
            
            // Clean URL
            this.cleanUrl();
        },
        
        // Clean URL parameters
        cleanUrl: function() {
            if (window.history && window.history.replaceState) {
                const cleanUrl = window.location.origin + window.location.pathname;
                window.history.replaceState({}, document.title, cleanUrl);
                console.log('URL cleaned:', cleanUrl);
            }
        },
        
        // Test connection (can be called from buttons)
        testConnection: function() {
            frappe.show_progress(__('Testing Connection...'), 20, 100, 'Please wait...');
            
            frappe.call({
                method: 'shopee_bridge.shopee_bridge.doctype.shopee_settings.api.test_connection',
                callback: function(r) {
                    frappe.hide_progress();
                    
                    if (r.message && r.message.success) {
                        const shop = r.message;
                        frappe.msgprint({
                            title: __('Connection Test Successful'),
                            message: `
                                <div class="alert alert-success">
                                    <i class="fa fa-check-circle"></i> <strong>Connected successfully!</strong>
                                </div>
                                <table class="table table-bordered">
                                    <tr><td><strong>Shop Name:</strong></td><td>${shop.shop_name || 'N/A'}</td></tr>
                                    <tr><td><strong>Shop ID:</strong></td><td>${shop.shop_id || 'N/A'}</td></tr>
                                    <tr><td><strong>Region:</strong></td><td>${shop.region || 'N/A'}</td></tr>
                                    <tr><td><strong>Status:</strong></td><td><span class="badge badge-success">${shop.status || 'N/A'}</span></td></tr>
                                </table>
                            `,
                            indicator: 'green'
                        });
                    } else {
                        frappe.msgprint({
                            title: __('Connection Test Failed'),
                            message: `
                                <div class="alert alert-danger">
                                    <i class="fa fa-times-circle"></i> <strong>Connection failed</strong><br><br>
                                    <strong>Error:</strong> ${r.message.message || 'Unknown error'}<br><br>
                                    <strong>Please check:</strong><br>
                                    • Your internet connection<br>
                                    • Shopee Partner credentials<br>
                                    • Token expiration status
                                </div>
                            `,
                            indicator: 'red'
                        });
                    }
                },
                error: function(err) {
                    frappe.hide_progress();
                    frappe.msgprint({
                        title: __('Connection Test Error'),
                        message: `
                            <div class="alert alert-danger">
                                <strong>Network Error:</strong> ${err.message || 'Unable to reach server'}
                            </div>
                        `,
                        indicator: 'red'
                    });
                }
            });
        },
        
        // Manual sync orders
        syncOrders: function(hours = 24) {
            const hoursInput = prompt(__('Sync orders from how many hours ago?'), hours);
            if (!hoursInput || isNaN(hoursInput)) return;
            
            frappe.show_progress(__('Syncing Orders...'), 10, 100, 'This may take a while...');
            
            frappe.call({
                method: 'shopee_bridge.shopee_bridge.doctype.shopee_settings.api.sync_recent_orders',
                args: { hours: parseInt(hoursInput) },
                callback: function(r) {
                    frappe.hide_progress();
                    
                    if (r.message) {
                        const result = r.message;
                        const success = result.errors === 0 || result.success;
                        
                        frappe.msgprint({
                            title: success ? __('Sync Completed') : __('Sync Completed with Errors'),
                            message: `
                                <div class="alert ${success ? 'alert-success' : 'alert-warning'}">
                                    <strong>Sync Results:</strong><br>
                                    <table class="table table-sm">
                                        <tr><td>Processed Orders:</td><td><strong>${result.processed_orders || 0}</strong></td></tr>
                                        <tr><td>Errors:</td><td><strong>${result.errors || 0}</strong></td></tr>
                                        <tr><td>From:</td><td>${new Date(result.from * 1000).toLocaleString()}</td></tr>
                                        <tr><td>To:</td><td>${new Date(result.to * 1000).toLocaleString()}</td></tr>
                                    </table>
                                </div>
                            `,
                            indicator: success ? 'green' : 'orange'
                        });
                    }
                },
                error: function(err) {
                    frappe.hide_progress();
                    frappe.msgprint({
                        title: __('Sync Failed'),
                        message: `<div class="alert alert-danger">${err.message || 'Sync failed'}</div>`,
                        indicator: 'red'
                    });
                }
            });
        }
    };
    
    // Auto-run when document is ready
    $(document).ready(function() {
        // Small delay to ensure frappe is loaded
        setTimeout(function() {
            if (typeof frappe !== 'undefined') {
                shopee_oauth.handleCallback();
            } else {
                console.warn('Frappe not loaded, skipping OAuth callback');
            }
        }, 500);
    });
    
    // Make functions globally available
    window.shopee_test_connection = function() {
        shopee_oauth.testConnection();
    };
    
    window.shopee_sync_orders = function() {
        shopee_oauth.syncOrders();
    };
    
})();