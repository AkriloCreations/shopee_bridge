frappe.ui.form.on("Shopee Settings", {
    refresh(frm) {
        // === TOKEN EXPIRY INFO ===
        if (frm.doc.token_expires_at) {
            try {
                // Parse as epoch timestamp (Shopee v2 standard)
                const expiryEpoch = parseInt(frm.doc.token_expires_at);
                if (!isNaN(expiryEpoch)) {
                    // Convert to WIB timezone
                    const dt = new Date(expiryEpoch * 1000);
                    const wibStr = dt.toLocaleString('id-ID', { timeZone: 'Asia/Jakarta' });
                    const nowEpoch = Math.floor(Date.now() / 1000);
                    const timeRemaining = expiryEpoch - nowEpoch;
                    
                    let status, indicator;
                    if (timeRemaining <= 0) {
                        status = '❌ Expired';
                        indicator = 'red';
                    } else if (timeRemaining < 600) { // 10 minutes
                        status = '⚠️ Expiring Soon';
                        indicator = 'orange';
                    } else {
                        status = '✅ Valid';
                        indicator = 'green';
                    }
                    
                    frm.dashboard.set_headline(`<span style="font-size:1.1em;color:${indicator}">Access Token <b>${status}</b> sampai <b>${wibStr} WIB</b></span>`);
                }
            } catch (e) {
                frm.dashboard.set_headline(`<span style="color:red">Gagal membaca expiry token: ${e}</span>`);
            }
        }
        // Add button groups for better organization
        frm.clear_custom_buttons();
        
        // === CONNECTION & AUTH GROUP ===
        const ready = !!(frm.doc.partner_id && frm.doc.partner_key && frm.doc.redirect_url);
        
        if (ready) {
            frm.add_custom_button(__("Connect to Shopee"), async () => {
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.connect_to_shopee" });
                    if (r.message?.ok && r.message.url) {
                        frappe.msgprint(__("Opening Shopee authorization page..."));
                        window.open(r.message.url, "_blank");
                    } else {
                        frappe.msgprint(__("Error: {0}", [r.message?.error || "Failed to generate OAuth URL"]));
                            // Connection indicator logic
                            let indicator = 'green';
                            if (expiryEpoch < nowEpoch) {
                                indicator = 'red';
                                status = '❌ Disconnected';
                            } else if (expiryEpoch - nowEpoch < 86400) { // less than 24h
                                indicator = 'orange';
                                status = '⚠️ Expiring Soon';
                            } else if (!frm.doc.access_token) {
                                indicator = 'red';
                                status = '❌ Disconnected';
                            } else {
                                indicator = 'green';
                                status = '✅ Connected';
                            }
                            frm.dashboard.set_headline(`<span style="font-size:1.1em;color:${indicator}">Access Token <b>${status}</b> sampai <b>${wibStr} WIB</b></span>`);
                    }
                } catch (e) { 
                    frappe.msgprint(__("Error: {0}", [String(e)])); 
                }
            }, __("Connection"));

            frm.add_custom_button(__("Test Connection"), async () => {
                frappe.show_alert(__("Testing connection..."), 3);
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.test_shopee_connection" });
                    if (r.message?.ok) {
                        const shop = r.message.shop;
                        let msg = `<b>Connection Status:</b><br><br>`;
                        msg += `<b>Environment:</b> ${shop.environment}<br>`;
                        msg += `<b>Shop ID:</b> ${shop.shop_id || 'Not connected'}<br>`;
                        msg += `<b>Has Token:</b> ${shop.has_token ? '✅ Yes' : '❌ No'}<br>`;
                        if (shop.token_expires_at) {
                            const dt = new Date(shop.token_expires_at * 1000);
                            const wibStr = dt.toLocaleString('id-ID', { timeZone: 'Asia/Jakarta' });
                            const nowEpoch = Math.floor(Date.now() / 1000);
                            const timeRemaining = shop.token_expires_at - nowEpoch;
                            const hoursRemaining = Math.floor(timeRemaining / 3600);
                            const minutesRemaining = Math.floor((timeRemaining % 3600) / 60);
                            
                            msg += `<b>Token Expiry:</b> ${wibStr} WIB (${hoursRemaining}h ${minutesRemaining}m remaining)<br>`;
                        }
                        if (shop.scopes) {
                            msg += `<b>Scopes:</b> ${Array.isArray(shop.scopes) ? shop.scopes.join(', ') : shop.scopes}<br>`;
                        }
                        if (shop.last_auth_error) {
                            msg += `<b>Last Auth Error:</b> <span style="color:red">${shop.last_auth_error}</span><br>`;
                        }
                        if (shop.api_error) {
                            msg += `<b>API Error:</b> <span style="color:red">${shop.api_error}</span><br>`;
                        }
                        if (shop.message) {
                            msg += `<b>Message:</b> ${shop.message}<br>`;
                        }
                        if (shop.api_response) {
                            let displayValue = shop.api_response;
                            if (typeof displayValue === 'object') {
                                try {
                                    displayValue = `<pre style="background:#f8f8f8;padding:4px;border-radius:3px">${JSON.stringify(displayValue, null, 2)}</pre>`;
                                } catch (e) {
                                    displayValue = '[object Object]';
                                }
                            }
                            msg += `<b>API Response:</b> ${displayValue}<br>`;
                        }
                        frappe.msgprint(msg, __("Connection Test Result"));
                    } else {
                        frappe.msgprint(__("Error: {0}", [r.message?.error]));
                    }
                } catch (e) { 
                    frappe.msgprint(__("Connection Error: {0}", [String(e)])); 
                }
            }, __("Connection"));

            frm.add_custom_button(__("Refresh Token"), async () => {
                frappe.show_alert(__("Checking token status and refreshing if needed..."), 3);
                try {
                    // Use new centralized refresh function
                    const r = await frappe.call({ method: "shopee_bridge.auth.refresh_access_token_if_needed" });
                    if (r.message) {
                        frappe.show_alert(__("Token was refreshed successfully!"), 5);
                        frm.reload_doc();
                    } else {
                        frappe.show_alert(__("Token is still valid, no refresh needed."), 3);
                    }
                } catch (e) {
                    frappe.msgprint(__("Token refresh error: {0}", [String(e)]));
                }
            }, __("Connection"));
        } else {
            frm.add_custom_button(__("Setup Required"), () => {
                frappe.msgprint(__("Please configure Partner ID, Partner Key, and Redirect URL first."));
            }, __("Connection")).addClass("btn-warning");
        }

        // === SYNC & OPERATIONS GROUP ===
        if (frm.doc.access_token) {
            frm.add_custom_button(__("Sync Orders"), async () => {
                const minutes = await new Promise(resolve => {
                    frappe.prompt({
                        label: __("Sync last N minutes"),
                        fieldname: "minutes",
                        fieldtype: "Int",
                        default: 30,
                        reqd: 1
                    }, (values) => resolve(values.minutes), __("Sync Orders"));
                });
                
                if (minutes) {
                    frappe.show_alert(__("Starting order sync..."), 3);
                    try {
                        const r = await frappe.call({ 
                            method: "shopee_bridge.api.sync_orders_api",
                            args: { minutes: minutes }
                        });
                        
                        if (r.message?.ok) {
                            frappe.msgprint(__("Order sync completed: {0}", [JSON.stringify(r.message.sync, null, 2)]));
                        } else {
                            frappe.msgprint(__("Sync failed: {0}", [r.message?.error]));
                        }
                    } catch (e) {
                        frappe.msgprint(__("Sync error: {0}", [String(e)]));
                    }
                }
            }, __("Operations"));

            frm.add_custom_button(__("Sync Shipping"), async () => {
                frappe.show_alert(__("Starting shipping sync..."), 3);
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.sync_shipping_api" });
                    if (r.message?.ok) {
                        frappe.msgprint(__("Shipping sync completed: {0}", [JSON.stringify(r.message.sync, null, 2)]));
                    } else {
                        frappe.msgprint(__("Shipping sync failed: {0}", [r.message?.error]));
                    }
                } catch (e) {
                    frappe.msgprint(__("Shipping sync error: {0}", [String(e)]));
                }
            }, __("Operations"));

            frm.add_custom_button(__("Sync Returns"), async () => {
                frappe.show_alert(__("Starting returns sync..."), 3);
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.sync_returns_api" });
                    if (r.message?.ok) {
                        frappe.msgprint(__("Returns sync completed: {0}", [JSON.stringify(r.message.sync, null, 2)]));
                    } else {
                        frappe.msgprint(__("Returns sync failed: {0}", [r.message?.error]));
                    }
                } catch (e) {
                    frappe.msgprint(__("Returns sync error: {0}", [String(e)]));
                }
            }, __("Operations"));

            frm.add_custom_button(__("Sync Finance"), async () => {
                frappe.show_alert(__("Starting finance sync..."), 3);
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.sync_finance_api" });
                    if (r.message?.ok) {
                        frappe.msgprint(__("Finance sync completed: {0}", [JSON.stringify(r.message.sync, null, 2)]));
                    } else {
                        frappe.msgprint(__("Finance sync failed: {0}", [r.message?.error]));
                    }
                } catch (e) {
                    frappe.msgprint(__("Finance sync error: {0}", [String(e)]));
                }
            }, __("Operations"));
        }

        // === MONITORING GROUP ===
        frm.add_custom_button(__("System Health"), async () => {
            frappe.show_alert(__("Checking system health..."), 3);
            try {
                const r = await frappe.call({ method: "shopee_bridge.api.get_health_status" });
                if (r.message?.ok) {
                    const health = r.message.health;
                    let status_color = health.token_valid && health.settings_configured ? "green" : "orange";
                    
                    let msg = `<div style="color: ${status_color}"><b>System Health Status</b></div><br>
                        <b>Token Valid:</b> ${health.token_valid ? '✅ Yes' : '❌ No'}<br>
                        <b>Settings Configured:</b> ${health.settings_configured ? '✅ Yes' : '❌ No'}<br>
                        <b>Recent Errors (24h):</b> ${health.recent_errors}<br>
                        <b>Pending Webhooks (1h):</b> ${health.pending_webhooks}<br>
                        <b>Last Check:</b> ${health.timestamp}`;
                    
                    frappe.msgprint(msg, __("System Health Status"));
                } else {
                    frappe.msgprint(__("Health check failed: {0}", [r.message?.error]));
                }
            } catch (e) {
                frappe.msgprint(__("Health check error: {0}", [String(e)]));
            }
        }, __("Monitor"));

        frm.add_custom_button(__("Webhook Logs"), async () => {
            try {
                const r = await frappe.call({ method: "shopee_bridge.api.get_webhook_logs" });
                if (r.message?.ok) {
                    const logs = r.message.logs;
                    if (logs.length === 0) {
                        frappe.msgprint(__("No recent webhook logs found."));
                        return;
                    }
                    
                    let msg = `<b>Recent Webhook Logs (${logs.length}):</b><br><br>`;
                    logs.slice(0, 10).forEach(log => {
                        const status_icon = log.status === 'done' ? '✅' : 
                                          log.status === 'failed' ? '❌' : '⏳';
                        const sig_icon = log.signature_valid ? '🔐' : '⚠️';
                        
                        msg += `${status_icon} ${sig_icon} <b>${log.event_type}</b> (${log.source_env})<br>
                                &nbsp;&nbsp;&nbsp;&nbsp;Status: ${log.status}<br>
                                &nbsp;&nbsp;&nbsp;&nbsp;Time: ${log.creation}<br><br>`;
                    });
                    
                    const d = new frappe.ui.Dialog({
                        title: __("Recent Webhook Logs"),
                        fields: [{
                            fieldtype: "HTML",
                            fieldname: "webhook_logs",
                            options: msg
                        }],
                        size: "large"
                    });
                    d.show();
                } else {
                    frappe.msgprint(__("Failed to fetch webhook logs: {0}", [r.message?.error]));
                }
            } catch (e) {
                frappe.msgprint(__("Webhook logs error: {0}", [String(e)]));
            }
        }, __("Monitor"));

        // === INFO DISPLAY ===
        if (frm.doc.shop_id) {
            frm.add_custom_button(__("Show Shop Info"), async () => {
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.test_shopee_connection" });
                    if (r.message?.ok) {
                        const info = r.message.shop;
                        let msg = `<b>Shop Information:</b><br><br>`;
                        

                            Object.entries(info).forEach(([key, value]) => {
                                if (value !== null && value !== undefined) {
                                    let displayValue = value;
                                    if (typeof value === 'object') {
                                        try {
                                            displayValue = `<pre style="background:#f8f8f8;padding:4px;border-radius:3px">${JSON.stringify(value, null, 2)}</pre>`;
                                        } catch (e) {
                                            displayValue = '[object Object]';
                                        }
                                    }
                                    msg += `<b>${key.replace(/_/g, ' ').toUpperCase()}:</b> ${displayValue}<br>`;
                                }
                            });
                        
                        frappe.msgprint(msg, __("Shop Information"));
                    }
                } catch (e) {
                    frappe.msgprint(__("Error fetching shop info: {0}", [String(e)]));
                }
            }, __("Info"));
        }
    },

    // Handle OAuth callback parameters and pre-fill form  
    onload(frm) {
        // Check for OAuth callback parameters directly in URL (from Shopee redirect)
        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        const shop_id = urlParams.get('shop_id');
        const main_account_id = urlParams.get('main_account_id');
        
        // Only handle if we have actual OAuth parameters (not from our internal redirect)
        if (code && (shop_id || main_account_id)) {
            // Show loading message
            frappe.show_alert(__('Processing OAuth callback automatically...'), 5);
            
            // Automatically exchange code for tokens
            frappe.call({
                method: "shopee_bridge.api.oauth_callback",
                args: {
                    code: code,
                    shop_id: shop_id,
                    main_account_id: main_account_id
                },
                callback: function(r) {
                    if (r.message?.ok) {
                        // Success - show success message and reload form
                        frappe.msgprint({
                            title: __('Shopee Connection Successful'),
                            message: __('✅ OAuth flow completed successfully!<br><br><b>Shop ID:</b> {0}<br><b>Tokens:</b> Automatically saved<br><b>Status:</b> Connected', [
                                r.message.shop_id || shop_id
                            ]),
                            indicator: 'green'
                        });
                        
                        // Clean URL and reload form to show updated data
                        window.history.replaceState({}, document.title, window.location.pathname);
                        setTimeout(() => {
                            frm.reload_doc();
                        }, 2000);
                        
                    } else {
                        // Failed - show error and fallback to manual
                        const error = r.message?.error || 'Unknown error';
                        frappe.msgprint({
                            title: __('Automatic Token Exchange Failed'),
                            message: __('❌ Error: {0}<br><br>Please try manual exchange:<br><b>Authorization Code:</b> {1}<br><b>Shop ID:</b> {2}', [
                                error,
                                code,
                                shop_id || main_account_id
                            ]),
                            indicator: 'red'
                        });
                        
                        // Try to fill fields for manual attempt
                        setTimeout(() => {
                            try {
                                if (code && frm.fields_dict.last_auth_code) {
                                    frm.set_value('last_auth_code', code);
                                }
                                if (shop_id && frm.fields_dict.shop_id) {
                                    frm.set_value('shop_id', shop_id);
                                }
                                if (main_account_id && frm.fields_dict.merchant_id) {
                                    frm.set_value('merchant_id', main_account_id);
                                }
                            } catch (e) {
                                console.log('Field setting failed:', e);
                            }
                        }, 1000);
                        
                        // Clean URL after error handling
                        window.history.replaceState({}, document.title, window.location.pathname);
                    }
                },
                error: function(xhr) {
                    // Network error - fallback to manual
                    frappe.msgprint({
                        title: __('Network Error'),
                        message: __('❌ Could not connect to server for automatic token exchange.<br><br>Please try manual exchange:<br><b>Authorization Code:</b> {0}<br><b>Shop ID:</b> {1}', [
                            code,
                            shop_id || main_account_id
                        ]),
                        indicator: 'red'
                    });
                    
                    // Clean URL after network error
                    window.history.replaceState({}, document.title, window.location.pathname);
                }
            });
        }
    },

    // Add manual token exchange button
    last_auth_code(frm) {
        if (frm.doc.last_auth_code && frm.doc.shop_id) {
            frm.add_custom_button(__("Exchange Code for Tokens"), async () => {
                frappe.show_alert(__("Exchanging authorization code for tokens..."), 5);
                
                try {
                    const r = await frappe.call({
                        method: "shopee_bridge.api.oauth_callback",
                        args: {
                            code: frm.doc.last_auth_code,
                            shop_id: frm.doc.shop_id,
                            main_account_id: frm.doc.merchant_id
                        }
                    });
                    
                    if (r.message?.ok) {
                        frappe.show_alert(__("Token exchange successful! Tokens have been saved."), 5);
                        frm.reload_doc();
                    } else {
                        frappe.msgprint(__("Token exchange failed: {0}", [r.message?.error || 'Unknown error']));
                    }
                } catch (e) {
                    frappe.msgprint(__("Token exchange error: {0}", [String(e)]));
                }
            }, __("OAuth"));
        }
    }
});

