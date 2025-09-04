frappe.ui.form.on("Shopee Settings", {
    refresh(frm) {
        frm.add_custom_button("Test Connection", async () => {
            try {
                const r = await frappe.call({ method: "shopee_bridge.shopee_settings.test_shopee_connection" });
                frappe.msgprint(JSON.stringify(r.message, null, 2));
            } catch (e) { frappe.msgprint(String(e)); }
        });

        const ready = !!(frm.doc.partner_id && frm.doc.partner_key && frm.doc.redirect_url);
        if (ready) {
            frm.add_custom_button("Connect to Shopee", async () => {
                try {
                    const r = await frappe.call({ method: "shopee_bridge.shopee_settings.connect_to_shopee" });
                    if (r.message?.ok && r.message.url) window.open(r.message.url, "_blank");
                    else frappe.msgprint(r.message?.error || "Failed to build authorize URL");
                } catch (e) { frappe.msgprint(String(e)); }
            });
        }
    },
});

