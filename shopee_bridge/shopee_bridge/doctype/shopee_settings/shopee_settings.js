frappe.ui.form.on("Shopee Settings", {
    refresh(frm) {
        frm.add_custom_button("Test Connection", async () => {
            // Use thin public API facade (architecture rule: no direct service logic in doctype JS)
            try {
                const r = await frappe.call({ method: "shopee_bridge.api.test_shopee_connection" });
                frappe.msgprint(__("Shopee Connection Test Result:\n{0}", [JSON.stringify(r.message, null, 2)]));
            } catch (e) { frappe.msgprint(String(e)); }
        });

        const ready = !!(frm.doc.partner_id && frm.doc.partner_key && frm.doc.redirect_url);
        if (ready) {
            frm.add_custom_button("Connect to Shopee", async () => {
                try {
                    const r = await frappe.call({ method: "shopee_bridge.api.connect_to_shopee" });
                    if (r.message?.ok && r.message.url) {
                        window.open(r.message.url, "_blank");
                    } else {
                        frappe.msgprint(r.message?.error || __("Failed to build authorize URL"));
                    }
                } catch (e) { frappe.msgprint(String(e)); }
            });
        }
    },
});

