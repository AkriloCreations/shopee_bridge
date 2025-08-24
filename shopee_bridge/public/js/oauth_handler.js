// File: apps/shopee_bridge/shopee_bridge/public/js/oauth_handler.js

// Global OAuth handler yang jalan di semua page
$(document).ready(function() {
    // Check URL params untuk OAuth callback
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');
    const shop_id = urlParams.get('shop_id');
    
    // Jika ada code & shop_id, dan kita TIDAK di Shopee Settings page
    if (code && shop_id && !window.location.pathname.includes('shopee-settings')) {
        console.log('OAuth callback detected, redirecting to Shopee Settings...');
        
        // Redirect ke Shopee Settings dengan params
        const targetUrl = `/app/shopee-settings?code=${code}&shop_id=${shop_id}`;
        window.location.href = targetUrl;
    }
    
    // Jika ada code & shop_id, dan kita DI Shopee Settings page  
    if (code && shop_id && window.location.pathname.includes('shopee-settings')) {
        console.log('Processing OAuth callback...');
        
        // Auto process setelah delay singkat (tunggu form load)
        setTimeout(function() {
            frappe.show_alert({
                message: 'Processing Shopee authorization...',
                indicator: 'blue'
            });
            
            frappe.call({
                method: 'shopee_bridge.api.exchange_code',
                args: {
                    code: code,
                    shop_id: shop_id
                },
                callback: function(r) {
                    if (r.message && r.message.ok) {
                        frappe.show_alert({
                            message: 'Shopee connected successfully!',
                            indicator: 'green'
                        });
                        
                        // Clean URL
                        if (window.history && window.history.replaceState) {
                            const cleanUrl = window.location.origin + window.location.pathname;
                            window.history.replaceState({}, document.title, cleanUrl);
                        }
                        
                        // Refresh page setelah clean URL
                        setTimeout(() => {
                            window.location.reload();
                        }, 1500);
                    }
                },
                error: function(err) {
                    frappe.show_alert({
                        message: 'Auth failed: ' + (err.message || 'Unknown error'),
                        indicator: 'red'
                    });
                }
            });
        }, 1000);
    }
});
