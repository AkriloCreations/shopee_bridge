frappe.ui.form.on('Shopee Settings', {
    refresh: function(frm) {
        // Helper to check required fields
        function can_connect() {
            return frm.doc.partner_id && frm.doc.partner_key && frm.doc.redirect_url;
        }

        // Connect to Shopee button
        frm.add_custom_button(__('Connect to Shopee'), function() {
            frappe.call({
                method: 'shopee_bridge.api.connect_to_shopee',
                args: { scopes: ['shop', 'order', 'finance', 'logistics', 'returns'] }, // TODO: adjust scopes as needed
                callback: function(r) {
                    if (r.message) {
                        window.open(r.message, '_blank');
                    } else {
                        frappe.msgprint(__('Failed to get Shopee connect URL.'));
                    }
                }
            });
        }, 'Actions').attr('disabled', !can_connect());

        // Test Connection button
        frm.add_custom_button(__('Test Connection'), function() {
            frappe.call({
                method: 'shopee_bridge.api.test_shopee_connection',
                callback: function(r) {
                    if (r.message) {
                        frappe.msgprint({
                            title: __('Shopee Shop Info'),
                            message: JSON.stringify(r.message, null, 2),
                            indicator: 'green'
                        });
                    } else {
                        frappe.msgprint(__('Connection test failed.'));
                    }
                }
            });
        }, 'Actions');

        // Run Fiscal Year Sync button
        frm.add_custom_button(__('Run Fiscal Year Sync'), function() {
            frappe.call({
                method: 'shopee_bridge.api.run_fy_prompt',
                callback: function(r) {
                    frappe.show_alert({
                        message: __('Fiscal Year Sync started.'),
                        indicator: 'blue'
                    });
                }
            });
        }, 'Actions');
    }
});