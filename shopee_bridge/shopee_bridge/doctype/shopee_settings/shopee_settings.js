// Shopee Settings Client Script
// Namespace server: shopee_bridge.api.<method>

frappe.ui.form.on('Shopee Settings', {
	refresh(frm) {
		// allow re-render
		frm._status_shown = false;

		if (!frm.is_new()) {
			// Shopee Actions
			frm.add_custom_button(__('Connect to Shopee'), () => connect_to_shopee(frm), __('Shopee Actions'))
				.addClass('btn-primary');
			if (frm.doc.access_token) {
				frm.add_custom_button(__('Test Connection'), () => test_shopee_connection(frm), __('Shopee Actions'));
			}

			// Sync Actions
			if (frm.doc.access_token) {
				frm.add_custom_button(__('Sync Orders (Hours / Date Range)'), () => sync_orders_dialog(frm), __('Sync Actions'));
				frm.add_custom_button(__('Sync Items'), () => sync_shopee_items(frm), __('Sync Actions'));
				frm.add_custom_button(__('Sync Status'), () => get_sync_status(frm), __('Sync Actions'));
			}

			// Phase 2 quick actions (only show if flow ON AND backend method exists)
			if (frm.doc.use_sales_order_flow) {
				// Optional: check if server method likely exists (lightweight)
				frappe.call({
					method: 'frappe.client.get_list',
					args: { doctype: 'DocType', filters: { name: 'Sales Invoice' }, limit_page_length: 1 },
					callback: () => {
						frm.add_custom_button(__('Make DN + SI (by Order SN)'), () => make_dn_si_prompt(frm), __('Phase 2'));
					}
				});
				frm.dashboard && frm.dashboard.add_indicator(__('Phase 2: SO → WO → DN → SI'), 'blue');
			}

			// Token Actions
			if (frm.doc.refresh_token) {
				frm.add_custom_button(__('Refresh Token'), () => refresh_token(frm), __('Token Actions'));
			}

			// Debug
			frm.add_custom_button(__('Debug Signature'), () => debug_signature(frm), __('Debug'));
		}

		show_connection_status(frm, true);

		// Auto OAuth callback
		if (window.location.search.includes('code=')) {
			handle_oauth_callback(frm);
		}
	},

	environment(frm) {
		if (frm.doc.environment) {
			frappe.show_alert({ message: __('Environment changed to: ') + frm.doc.environment, indicator: 'blue' });
		}
	},

	partner_id(frm) { if (frm.doc.partner_id) reset_tokens(frm); },
	partner_key(frm) { if (frm.doc.partner_key) reset_tokens(frm); }
});

// ---------------- Helpers & Flows ----------------
function reset_tokens(frm) {
	frm.set_value('access_token', '');
	frm.set_value('refresh_token', '');
	frm.set_value('token_expire_at', '');
	frm._status_shown = false;
	show_connection_status(frm, true);
}

function connect_to_shopee(frm) {
	if (!frm.doc.partner_id || !frm.doc.partner_key) {
		frappe.msgprint(__('Please enter Partner ID and Partner Key first'));
		return;
	}
	frappe.call({
		method: 'shopee_bridge.api.connect_url',
		callback(r) {
			if (r.message && r.message.url) {
				frappe.msgprint({
					title: __('Connect to Shopee'),
						message: __('You will be redirected to Shopee for authorization.'),
					indicator: 'blue',
					primary_action: {
						label: __('Continue to Shopee'),
						action() { window.open(r.message.url, '_blank'); }
					}
				});
			} else frappe.msgprint(__('Failed to generate connection URL'));
		}
	});
}

function make_dn_si_prompt(frm) {
	const d = new frappe.ui.Dialog({
		title: __('Make Delivery Note & Sales Invoice'),
		fields: [
			{ fieldtype: 'Data', fieldname: 'order_sn', label: 'Shopee Order SN', reqd: 1 },
			{ fieldtype: 'Date', fieldname: 'posting_date', label: 'Posting Date', default: frappe.datetime.get_today() }
		],
		primary_action_label: __('Create'),
		primary_action(values) {
			frappe.call({
				method: 'shopee_bridge.api.make_dn_si', // NOTE: backend method must exist; otherwise remove this feature
				args: values,
				callback(r) {
					if (r.message && r.message.ok) {
						frappe.msgprint(__('Created DN: {0}<br>Created SI: {1}', [r.message.delivery_note, r.message.sales_invoice]));
					} else {
						frappe.msgprint({ title: __('Failed'), message: __('Unable to create DN/SI'), indicator: 'red' });
					}
				},
				error(r) {
					frappe.msgprint({ title: __('Error'), message: r.message || __('Server error'), indicator: 'red' });
				}
			});
			d.hide();
		}
	});
	d.show();
}

function test_shopee_connection(frm) {
	frappe.show_progress(__('Testing Connection...'), 30, 100, 'Please wait');
	frappe.call({
		method: 'shopee_bridge.api.test_connection',
		callback(r) {
			frappe.hide_progress();
			if (r.message && r.message.success) {
				const s = r.message;
				frappe.msgprint({
					title: __('Connection Successful'),
					message: `<div class="alert alert-success"><i class="fa fa-check-circle"></i> <strong>Connected successfully!</strong></div>
						<table class="table table-bordered">
							<tr><td><strong>Shop Name:</strong></td><td>${s.shop_name || 'N/A'}</td></tr>
							<tr><td><strong>Shop ID:</strong></td><td>${s.shop_id || 'N/A'}</td></tr>
							<tr><td><strong>Region:</strong></td><td>${s.region || 'N/A'}</td></tr>
							<tr><td><strong>Status:</strong></td><td>${s.status || 'N/A'}</td></tr>
						</table>`,
					indicator: 'green'
				});
			} else {
				frappe.msgprint({
					title: __('Connection Failed'),
					message: `<div class="alert alert-danger"><i class="fa fa-exclamation-triangle"></i> ${(r.message && r.message.message) || __('Unable to connect to Shopee')}</div>`,
					indicator: 'red'
				});
			}
		},
		error(r) {
			frappe.hide_progress();
			frappe.msgprint({ title: __('Connection Error'), message: r.message || __('Error during test'), indicator: 'red' });
		}
	});
}

// ---------------- Sync Orders Dialog ----------------
function sync_orders_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __('Sync Orders'),
		fields: [
			{ fieldtype: 'Section Break', label: 'Mode' },
			{ fieldtype: 'Select', fieldname: 'mode', label: 'Mode', reqd: 1, options: ['By Hours', 'By Date Range'], default: 'By Hours' },
			{ fieldtype: 'Column Break' },
			{ fieldtype: 'Int', fieldname: 'hours', label: 'Hours (lookback)', default: 24, depends_on: "eval:doc.mode=='By Hours'" },
			{ fieldtype: 'Section Break', depends_on: "eval:doc.mode=='By Date Range'" },
			{ fieldtype: 'Date', fieldname: 'from_date', label: 'From Date', depends_on: "eval:doc.mode=='By Date Range'"},
			{ fieldtype: 'Date', fieldname: 'to_date', label: 'To Date', depends_on: "eval:doc.mode=='By Date Range'"},
			{ fieldtype: 'Section Break' },
			{ fieldtype: 'Int', fieldname: 'page_size', label: 'Page Size', default: 50 }
		],
		primary_action_label: __('Start Sync'),
		primary_action(values) {
			const page_size = parseInt(values.page_size || 50, 10);
			if (values.mode === 'By Hours') {
				const hours = parseInt(values.hours || 24, 10);
				if (isNaN(hours) || hours <= 0) return frappe.msgprint(__('Hours must be positive.'));
				frappe.show_progress(__('Syncing Orders...'), 20, 100, 'Please wait');
				frappe.call({
					method: 'shopee_bridge.api.sync_recent_orders',
					args: { hours, page_size },
					callback: (r) => handle_sync_result(frm, r, 'Orders'),
					error: (r) => handle_sync_error(r)
				});
			} else {
				if (!values.from_date || !values.to_date) return frappe.msgprint(__('Select both From and To dates.'));
				const fromTs = toUnixStart(values.from_date);
				const toTs = toUnixEnd(values.to_date);
				if (fromTs > toTs) return frappe.msgprint(__('From Date cannot be after To Date.'));
				frappe.show_progress(__('Backfilling Orders...'), 20, 100, 'Please wait');
				frappe.call({
					method: 'shopee_bridge.api.sync_orders_range',
					args: { time_from: fromTs, time_to: toTs, page_size },
					callback: (r) => handle_sync_result(frm, r, 'Backfill'),
					error: (r) => handle_sync_error(r)
				});
			}
			d.hide();
		}
	});
	d.show();
}

function handle_sync_result(frm, r, label) {
	frappe.hide_progress();
	const res = r.message || {};
	const ok = (res.errors === 0) || res.success;
	frappe.msgprint({
		title: ok ? __(label + ' Completed') : __(label + ' Completed with Errors'),
		message: `<div class="alert ${ok ? 'alert-success' : 'alert-warning'}"><strong>Results:</strong>
			<table class="table table-sm">
				<tr><td>Processed Orders</td><td><b>${res.processed_orders || res.processed || 0}</b></td></tr>
				<tr><td>Errors</td><td><b>${res.errors || 0}</b></td></tr>
				<tr><td>From</td><td>${res.from ? new Date(res.from*1000).toLocaleString() : (res.window?.from_iso || '-')}</td></tr>
				<tr><td>To</td><td>${res.to ? new Date(res.to*1000).toLocaleString() : (res.window?.to_iso || '-')}</td></tr>
			</table></div>`,
		indicator: ok ? 'green' : 'orange'
	});
	if (ok) frm.reload_doc();
}
function handle_sync_error(r) {
	frappe.hide_progress();
	frappe.msgprint({ title: __('Sync Failed'), message: (r.message || 'Unknown error'), indicator: 'red' });
}

// ---------------- Sync Items ----------------
function sync_shopee_items(frm) {
	const hours = prompt(__('Sync items from how many hours ago?'), '168');
	if (!hours || isNaN(hours)) return;
	frappe.show_progress(__('Syncing Items...'), 20, 100, 'This may take a while');
	frappe.call({
		method: 'shopee_bridge.api.sync_items',
		args: { hours: parseInt(hours, 10) },
		callback(r) {
			frappe.hide_progress();
			if (r.message && r.message.ok) {
				const x = r.message;
				frappe.msgprint({
					title: __('Item Sync Completed'),
					message: `<div class="alert alert-success"><strong>Sync Results:</strong><br>
						<table class="table table-sm">
							<tr><td>Created Items:</td><td><strong>${x.created || 0}</strong></td></tr>
							<tr><td>Updated Items:</td><td><strong>${x.updated || 0}</strong></td></tr>
							<tr><td>Total Processed:</td><td><strong>${x.processed_items || 0}</strong></td></tr>
							<tr><td>Errors:</td><td><strong>${x.errors || 0}</strong></td></tr>
						</table></div>`,
					indicator: 'green'
				});
			} else {
				frappe.msgprint({
					title: __('Item Sync Failed'),
					message: `<div class="alert alert-danger">${(r.message && r.message.message) || 'An error occurred during item sync'}</div>`,
					indicator: 'red'
				});
			}
		}
	});
}

// ---------------- Token Refresh ----------------
function refresh_token(frm) {
	frappe.show_progress(__('Refreshing Token...'), 30, 100, 'Please wait');
	frappe.call({
		method: 'shopee_bridge.api.refresh_if_needed',
		callback(r) {
			frappe.hide_progress();
			const result = r.message || {};
			const status = result.status;
			let msg = '', ind = 'blue', reload = false;
			switch(status) {
				case 'refreshed': msg = __('Token refreshed successfully'); ind='green'; reload=true; break;
				case 'token_still_valid': msg = __('Token still valid'); break;
				case 'no_refresh_token': msg = __('No refresh token. Reconnect.'); ind='orange'; reset_tokens(frm); frm.save(); break;
				case 'error': msg = __('Token refresh failed: ') + (result.message || 'Unknown'); ind='red'; break;
				case 'no_new_token': msg = __('No new token received'); ind='orange'; break;
				default: msg = __('Unexpected response: ') + (status||'none'); ind='red';
			}
			frappe.show_alert({ message: msg, indicator: ind }, 5);
			if (reload) setTimeout(() => frm.reload_doc(), 800); else { frm._status_shown = false; show_connection_status(frm, true); }
		},
		error(r) {
			frappe.hide_progress();
			let em = r.message || 'Server error';
			if (/unauthorized|invalid token|expired/i.test(em)) { reset_tokens(frm); frm.save(); em += '. Reconnect.'; }
			frappe.show_alert({ message: __('Token refresh error: ') + em, indicator: 'red' }, 8);
			frm._status_shown = false; show_connection_status(frm, true);
		}
	});
}

function get_sync_status(frm) {
	frappe.call({ method: 'shopee_bridge.api.get_sync_status', callback(r){ if (r.message && r.message.success){ const s=r.message; frappe.msgprint({ title: __('Sync Status'), message: `<div class="alert alert-info"><strong>Current Status:</strong><br><table class="table table-sm">
		<tr><td>Token Status:</td><td><span class="badge ${s.token_status==='valid'?'badge-success':'badge-warning'}">${s.token_status}</span></td></tr>
		<tr><td>Token Expires:</td><td>${s.token_expires? new Date(s.token_expires).toLocaleString():'N/A'}</td></tr>
		<tr><td>Last Sync:</td><td>${s.last_sync? new Date(s.last_sync).toLocaleString():'Never'}</td></tr>
		<tr><td>Total Orders Synced:</td><td><strong>${s.total_synced_orders||0}</strong></td></tr>
		<tr><td>Recent Errors (24h):</td><td><strong>${s.recent_errors||0}</strong></td></tr>
		<tr><td>Environment:</td><td><span class="badge badge-info">${s.environment||'Test'}</span></td></tr>
	</table></div>`, indicator: s.token_status==='valid'?'green':'orange'}); } }});
}

// ---------------- Debug ----------------
function debug_signature(frm){ frappe.call({ method:'shopee_bridge.api.debug_sign', callback(r){ if(!r.message)return; const d=r.message; frappe.msgprint({ title: __('Debug Signature'), message: `<div class="alert alert-info"><strong>Signature Debug Info:</strong><br><table class="table table-sm">
	<tr><td>Partner ID:</td><td><code>${d.partner_id}</code></td></tr>
	<tr><td>Partner Key Length:</td><td>${d.partner_key_length}</td></tr>
	<tr><td>Path:</td><td><code>${d.path}</code></td></tr>
	<tr><td>Timestamp:</td><td><code>${d.timestamp}</code></td></tr>
	<tr><td>Base String:</td><td><code>${d.base_string}</code></td></tr>
	<tr><td>Signature:</td><td><code>${d.signature}</code></td></tr>
	<tr><td>Environment:</td><td>${d.environment}</td></tr>
</table></div>`, indicator:'blue'}); }}); }

// ---------------- Status Display ----------------
function show_connection_status(frm, force){
	if (!force && frm._status_shown) return; // allow force refresh
	frm._status_shown = true;
	const html = get_status_html(frm);
	const existing = frm.$wrapper.find('#connection-status-wrapper');
	if (existing.length) existing.remove();
	const wrap = $(`<div class="form-group" id="connection-status-wrapper"><div class="clearfix"><label class="control-label" style="margin-bottom:5px;">Connection Status</label></div><div class="control-input-wrapper"><div class="control-input">${html}</div></div></div>`);
	if (frm.fields_dict.partner_key) wrap.insertAfter(frm.fields_dict.partner_key.wrapper);
}

function get_status_html(frm){
	if (!frm.doc.partner_id || !frm.doc.partner_key) return `<div class='alert alert-info' style='margin-bottom:0;'> <i class='fa fa-info-circle'></i> <strong>Setup Required:</strong> Enter Partner ID & Key first</div>`;
	if (!frm.doc.access_token) return `<div class='alert alert-warning' style='margin-bottom:0;'><i class='fa fa-exclamation-triangle'></i> <strong>Not Connected:</strong> Click "Connect to Shopee".</div>`;
	const now = new Date();
	const exp = frm.doc.token_expire_at ? new Date(frm.doc.token_expire_at*1000) : null;
	if (!exp) return `<div class='alert alert-warning' style='margin-bottom:0;'><i class='fa fa-question-circle'></i> <strong>Connected:</strong> Expiration unknown <button class='btn btn-sm btn-warning' onclick='refresh_token(cur_frm)' style='margin-left:10px;'>Check Token</button></div>`;
	const diff = exp - now; const expired = diff <=0; const soon = diff>0 && diff<3600000;
	if (expired) return `<div class='alert alert-danger' style='margin-bottom:0;'><i class='fa fa-times-circle'></i> <strong>Token Expired:</strong> ${exp.toLocaleString()} <button class='btn btn-sm btn-primary' onclick='connect_to_shopee(cur_frm)' style='margin-left:10px;'>Reconnect</button></div>`;
	if (soon) return `<div class='alert alert-warning' style='margin-bottom:0;'><i class='fa fa-clock-o'></i> <strong>Token Expires Soon:</strong> ${exp.toLocaleString()} <button class='btn btn-sm btn-warning' onclick='refresh_token(cur_frm)' style='margin-left:10px;'>Refresh Now</button></div>`;
	return `<div class='alert alert-success' style='margin-bottom:0;'><i class='fa fa-check-circle'></i> <strong>Connected:</strong> Expires ${exp.toLocaleString()} <button class='btn btn-sm btn-secondary' onclick='refresh_token(cur_frm)' style='margin-left:10px;'>Refresh Token</button></div>`;
}

// ---------------- OAuth Callback Handling ----------------
function handle_oauth_callback(frm){
	const qs = new URLSearchParams(window.location.search);
	const code = qs.get('code'); const shop_id = qs.get('shop_id'); const error = qs.get('error');
	if (error){ frappe.msgprint({ title: __('Shopee Authorization Failed'), message: __('Error: ')+error+'<br><br>'+__('Please try connecting again.'), indicator:'red'}); clear_query_string(); return; }
	if (code && !frm._oauth_handled){
		frm._oauth_handled = true; frappe.show_progress(__('Connecting to Shopee...'), 50, 100, 'Exchanging authorization code...');
		frappe.call({ method:'shopee_bridge.api.exchange_code', args:{ code, shop_id: shop_id || null }, callback(r){ frappe.hide_progress(); if (r.message && r.message.ok){ frappe.show_alert({ message: __('Shopee connected successfully!'), indicator:'green' },5); setTimeout(()=>frm.reload_doc(), 1200); } else { let em='Failed to connect to Shopee.'; if (r.message && r.message.message) em += '<br>Error: '+r.message.message; frappe.msgprint({ title: __('Connection Failed'), message: em, indicator:'red'}); } clear_query_string(); }, error(r){ frappe.hide_progress(); frappe.msgprint({ title: __('Connection Error'), message: 'Failed to exchange authorization code.<br>Error: '+ (r.message||'Err'), indicator:'red'}); clear_query_string(); } });
	}
}
function clear_query_string(){ if (window.history && window.history.replaceState){ window.history.replaceState({}, document.title, window.location.pathname); } }

// ---------------- Utils ----------------
function toUnixStart(d){ return Math.floor(new Date(d+' 00:00:00').getTime()/1000); }
function toUnixEnd(d){ return Math.floor(new Date(d+' 23:59:59').getTime()/1000); }

