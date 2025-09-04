frappe.ui.form.on('Shopee Settings', {
    refresh(frm) {
        const canConnect = frm.doc.partner_id && frm.doc.partner_key && frm.doc.redirect_url;
        frm.add_custom_button(__('Connect to Shopee'), () => {
            if (!canConnect) {
                frappe.msgprint(__('Lengkapi Partner ID, Partner Key, dan Redirect URL.'));
                return;
            }
            frappe.call({
                method: 'shopee_bridge.shopee_bridge.doctype.shopee_settings.shopee_settings.ShopeeSettings.connect_to_shopee',
                doc: frm.doc,
                callback(r) {
                    if (r.message && r.message.ok && r.message.authorize_url) {
                        window.open(r.message.authorize_url, '_blank');
                    } else if (r.message && r.message.error) {
                        frappe.msgprint(__('Error: ') + r.message.error);
                    }
                }
            });
        }, __('Shopee'));

        frm.add_custom_button(__('Test Connection'), () => {
            frappe.call({
                method: 'shopee_bridge.shopee_bridge.doctype.shopee_settings.shopee_settings.ShopeeSettings.test_shopee_connection',
                doc: frm.doc,
                callback(r) {
                    if (r.message && r.message.ok) {
                        frappe.msgprint(__('Info: ') + JSON.stringify(r.message.info || {}, null, 2));
                    } else if (r.message && r.message.error) {
                        frappe.msgprint(__('Error: ') + r.message.error);
                    }
                }
            });
        }, __('Shopee'));

        if (!canConnect) {
            frm.custom_buttons && Object.keys(frm.custom_buttons).forEach(k => {
                if (k.includes('Connect to Shopee')) {
                    // No direct disable API; simply warn on click is handled above.
                }
            });
        }
    }
});
