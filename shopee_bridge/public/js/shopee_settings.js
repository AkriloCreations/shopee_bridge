// Shopee Settings Client Script

frappe.ui.form.on('Shopee Settings', {
  refresh(frm) {
    frm._status_shown = false;

    if (!frm.is_new()) {
      frm.add_custom_button(__('Connect to Shopee'), () => connect_to_shopee(frm), __('Shopee Actions'))
        .addClass('btn-primary');
      if (frm.doc.access_token) {
        frm.add_custom_button(__('Test Connection'), () => test_shopee_connection(frm), __('Shopee Actions'));
      }

      if (frm.doc.access_token) {
        frm.add_custom_button(__('Sync Orders (Hours / Date Range)'), () => sync_orders_dialog(frm), __('Sync Actions'));
        frm.add_custom_button(__('Migrate Orders (Backfill)'), () => open_migrate_dialog(frm), __('Sync Actions'));
        frm.add_custom_button(__('Sync Items'), () => sync_shopee_items(frm), __('Sync Actions'));
        frm.add_custom_button(__('Sync Status'), () => get_sync_status(frm), __('Sync Actions'));
  frm.add_custom_button(__('Audit Shopee Orders (Month)'), () => audit_shopee_orders_dialog(frm), __('Sync Actions'));
      }
/* AUDIT SHOPEE ORDERS */
function audit_shopee_orders_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __('Audit Shopee Orders for Month'),
    fields: [
      { fieldtype: 'Int', fieldname: 'year', label: 'Year', reqd: 1, default: new Date().getFullYear() },
      { fieldtype: 'Select', fieldname: 'month', label: 'Month', reqd: 1,
        options: [
          { label: 'January', value: 1 },
          { label: 'February', value: 2 },
          { label: 'March', value: 3 },
          { label: 'April', value: 4 },
          { label: 'May', value: 5 },
          { label: 'June', value: 6 },
          { label: 'July', value: 7 },
          { label: 'August', value: 8 },
          { label: 'September', value: 9 },
          { label: 'October', value: 10 },
          { label: 'November', value: 11 },
          { label: 'December', value: 12 }
        ],
        default: new Date().getMonth() + 1
      },
      { fieldtype: 'Check', fieldname: 'auto_fix', label: 'Auto-fix missing SI/PE', default: 1 }
    ],
    primary_action_label: __('Run Audit'),
    primary_action(values) {
      frappe.show_progress(__('Auditing Shopee Orders...'), 40, 100, 'Please wait');
      frappe.call({
        method: "shopee_bridge.api.audit_shopee_orders_for_month",
        args: {
          year: values.year,
          month: parseInt(values.month, 10),
          auto_fix: !!values.auto_fix
        },
        callback(r) {
          frappe.hide_progress();
          const report = r.message || [];
          let html = `<div class='alert alert-info'><strong>Audit Results:</strong><br><table class='table table-bordered'><thead><tr><th>Order SN</th><th>SO</th><th>SI</th><th>PE</th><th>Auto-Fixed</th></tr></thead><tbody>`;
          for (const row of report) {
            html += `<tr><td>${row.order_sn}</td><td>${row.sales_order}</td><td>${row.sales_invoice || '-'}</td><td>${row.payment_entry_exists ? '✔️' : '❌'}</td><td>${row.auto_fixed ? '✔️' : '-'}</td></tr>`;
          }
          html += '</tbody></table></div>';
          frappe.msgprint({ title: __('Audit Shopee Orders'), message: html, wide: 1 });
        },
        error(r) {
          frappe.hide_progress();
          frappe.msgprint({ title: __('Audit Failed'), message: r.message || 'Server error', indicator: 'red' });
        }
      });
      d.hide();
    }
  });
  d.show();
}

      if (frm.doc.use_sales_order_flow) {
        frm.add_custom_button(__('Make DN + SI (by Order SN)'), () => make_dn_si_prompt(frm), __('Phase 2'));
        frm.dashboard && frm.dashboard.add_indicator(__('Phase 2: SO → WO → DN → SI'), 'blue');
      }

      if (frm.doc.refresh_token) {
        frm.add_custom_button(__('Refresh Token'), () => refresh_token(frm), __('Token Actions'));
      }

      frm.add_custom_button(__('Debug Signature'), () => debug_signature(frm), __('Debug'));
    }

    show_connection_status(frm);

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

/* Helpers */
function reset_tokens(frm) {
  frm.set_value('access_token', '');
  frm.set_value('refresh_token', '');
  frm.set_value('token_expire_at', '');
}

function connect_to_shopee(frm) {
  if (!frm.doc.partner_id || !frm.doc.partner_key) {
    frappe.msgprint(__('Please enter Partner ID and Partner Key first'));
    return;
  }
  frappe.call({
    method: "shopee_bridge.api.connect_url",
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
      } else {
        frappe.msgprint(__('Failed to generate connection URL'));
      }
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
        method: "shopee_bridge.api.make_dn_si",
        args: { order_sn: values.order_sn, posting_date: values.posting_date },
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
    method: "shopee_bridge.api.test_connection",
    callback(r) {
      frappe.hide_progress();
      if (r.message && r.message.success) {
        const s = r.message;
        frappe.msgprint({
          title: __('Connection Successful'),
          message:
            `<div class="alert alert-success">
               <i class="fa fa-check-circle"></i> <strong>Connected successfully!</strong>
             </div>
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
          message: `<div class="alert alert-danger">
                      <i class="fa fa-exclamation-triangle"></i>
                      ${(r.message && r.message.message) || __('Unable to connect to Shopee')}
                    </div>`,
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

/* SYNC ORDERS */
function sync_orders_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __('Sync Orders'),
    fields: [
      { fieldtype: 'Section Break', label: 'Mode' },
      { fieldtype: 'Select', fieldname: 'mode', label: 'Mode', reqd: 1,
        options: ['By Hours', 'By Date Range'], default: 'By Hours' },
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
      let time_from, time_to, label;
      if (values.mode === 'By Hours') {
        const hours = parseInt(values.hours || 24, 10);
        if (isNaN(hours) || hours <= 0) {
          frappe.msgprint(__('Hours must be a positive number.'));
          return;
        }
        time_to = Math.floor(Date.now() / 1000);
        time_from = time_to - hours * 3600;
        label = 'Orders';
      } else {
        if (!values.from_date || !values.to_date) {
          frappe.msgprint(__('Please select both From and To dates.'));
          return;
        }
        time_from = toUnixStart(values.from_date);
        time_to = toUnixEnd(values.to_date);
        if (time_from > time_to) {
          frappe.msgprint(__('From Date cannot be after To Date.'));
          return;
        }
        label = 'Backfill';
      }
      frappe.show_progress(__('Syncing Orders...'), 20, 100, 'Please wait');
      frappe.call({
        method: "shopee_bridge.api.sync_orders_range",
        args: { time_from, time_to, page_size: parseInt(values.page_size || 50, 10) },
        callback: (r) => handle_sync_result(frm, r, label),
        error: (r) => handle_sync_error(r)
      });
      d.hide();
    }
  });
  d.show();
}

function handle_sync_result(frm, r, label) {
  frappe.hide_progress();
  const res = r.message || {};
  const ok = (res.errors === 0) || res.success;

  // pilih angka processed (recent vs migrate wrapper)
  const processedOrders = res.processed_orders ?? res.processed_total ?? 0;
  const uniqueOrders = res.unique_order_sns ?? (res.processed_total ?? null);
  const rawTotal = res.raw_processed_total;

  let windowsHtml = '';
  if (Array.isArray(res.windows) && res.windows.length) {
    const rows = res.windows.slice(0, 50).map(w => `
      <tr>
        <td>${w.from ? new Date(w.from*1000).toLocaleString() : '-'}</td>
        <td>${w.to ? new Date(w.to*1000).toLocaleString() : '-'}</td>
        <td class="text-right">${w.processed_orders ?? '-'}</td>
        <td class="text-right">${w.api_calls ?? '-'}</td>
        <td class="text-right ${w.error ? 'text-danger' : ''}">${w.errors ?? (w.error ? 1 : 0)}</td>
      </tr>`).join('');
    windowsHtml = `
      <hr>
      <div class="small text-muted">Window summary (first ${Math.min(res.windows.length, 50)} rows):</div>
      <div style="max-height:280px; overflow:auto; border:1px solid #eee;">
        <table class="table table-sm table-bordered" style="margin:0;">
          <thead>
            <tr>
              <th>From</th><th>To</th><th class="text-right">Processed</th><th class="text-right">API Calls</th><th class="text-right">Errors</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  let extraStats = '';
  if (res.migrate_from || typeof res.processed_total === 'number') {
    extraStats = `
      <tr><td>Unique Orders</td><td><b>${uniqueOrders ?? processedOrders}</b></td></tr>
      ${rawTotal && rawTotal !== uniqueOrders ? `<tr><td>Raw Window Sum</td><td><b>${rawTotal}</b></td></tr>` : ''}`;
  }

  frappe.msgprint({
    title: ok ? __(label + ' Completed') : __(label + ' Completed with Errors'),
    message:
      `<div class="alert ${ok ? 'alert-success' : 'alert-warning'}">
         <strong>Results:</strong>
         <table class="table table-sm">
           <tr><td>Processed</td><td><b>${processedOrders}</b></td></tr>
           ${extraStats}
           <tr><td>Errors</td><td><b>${res.errors || 0}</b></td></tr>
           <tr><td>From</td><td>${res.from ? new Date(res.from*1000).toLocaleString() : '-'}</td></tr>
           <tr><td>To</td><td>${res.to ? new Date(res.to*1000).toLocaleString() : '-'}</td></tr>
         </table>
         ${windowsHtml}
       </div>`,
    indicator: ok ? 'green' : 'orange',
    wide: 1
  });

  if (ok) frm.reload_doc();
}

function handle_sync_error(r) {
  frappe.hide_progress();
  frappe.msgprint({ title: __('Sync Failed'), message: (r.message || 'Unknown error'), indicator: 'red' });
}

/* MIGRATE / BACKFILL */
function open_migrate_dialog(frm) {
  const d = new frappe.ui.Dialog({
    title: __('Backfill / Migrasi Orders Shopee'),
    fields: [
      { fieldname: 'start_mode', label: 'Start Mode', fieldtype: 'Select',
        options: ['Year Only', 'Date (UTC)', 'Epoch (UTC)'], default: 'Year Only', reqd: 1 },
      { fieldname: 'year', label: 'Year (4-digit)', fieldtype: 'Int', default: new Date().getUTCFullYear() },
      { fieldname: 'start_date', label: 'Start Date (UTC)', fieldtype: 'Date' },
      { fieldname: 'start_timestamp', label: 'Start Timestamp (epoch sec, UTC)', fieldtype: 'Int' },
      { fieldname: 'chunk_days', label: 'Chunk Days (<=15)', fieldtype: 'Int', default: 10, reqd: 1 },
      { fieldname: 'page_size', label: 'Page Size', fieldtype: 'Int', default: 50, reqd: 1 },
      { fieldname: 'order_status', label: 'Order Status (optional)', fieldtype: 'Data' },
      { fieldname: 'help', fieldtype: 'HTML', options:
        '<div class="text-muted small">• Pilih salah satu mode start.<br>' +
        '• <b>Year Only</b>: pakai 1 Jan (UTC) tahun dipilih.<br>' +
        '• <b>Date (UTC)</b>: 00:00:00 UTC hari tersebut.<br>' +
        '• <b>Epoch (UTC)</b>: input detik.<br>' +
        '• chunk_days maks 15.</div>' }
    ],
    primary_action_label: __('Run Backfill'),
    primary_action(values) {
      const chunk = cint(values.chunk_days);
      if (chunk <= 0 || chunk > 15) {
        frappe.msgprint({ message: 'chunk_days harus 1–15.', indicator: 'red' }); return;
      }
      const pageSize = cint(values.page_size || 50);
      if (pageSize <= 0) {
        frappe.msgprint({ message: 'page_size harus > 0.', indicator: 'red' }); return;
      }
      let start_timestamp = null;
      let year = null;
      if (values.start_mode === 'Epoch (UTC)') {
        start_timestamp = values.start_timestamp ? cint(values.start_timestamp) : null;
        if (!start_timestamp) {
          frappe.msgprint({ message: 'Isi Start Timestamp.', indicator: 'red' }); return;
        }
      } else if (values.start_mode === 'Date (UTC)') {
        if (!values.start_date) {
          frappe.msgprint({ message: 'Pilih Start Date.', indicator: 'red' }); return;
        }
        start_timestamp = Math.floor(new Date(values.start_date + 'T00:00:00Z').getTime() / 1000);
      } else {
        year = values.year || new Date().getUTCFullYear();
        if (!year || year < 1970) {
          frappe.msgprint({ message: 'Year tidak valid.', indicator: 'red' }); return;
        }
      }
      frappe.show_progress(__('Running Backfill...'), 35, 100, 'Please wait');
      frappe.call({
        method: "shopee_bridge.api.migrate_orders_from",
        args: {
          start_timestamp: start_timestamp,
          year: year,
          chunk_days: chunk,
          page_size: pageSize,
          order_status: values.order_status || null
        },
        callback(r) {
          frappe.hide_progress();
          handle_sync_result(frm, r, 'Backfill');
        },
        error(r) {
          frappe.hide_progress();
          handle_sync_error(r);
        }
      });
      d.hide();
    }
  });
  d.show();
}

/* SYNC ITEMS */
function sync_shopee_items(frm) {
  const hours = prompt(__('Sync items from how many hours ago?'), '168');
  if (!hours || isNaN(hours)) return;
  frappe.show_progress(__('Syncing Items...'), 20, 100, 'This may take a while');
  frappe.call({
    method: "shopee_bridge.api.sync_items",
    args: { hours: parseInt(hours, 10) },
    callback(r) {
      frappe.hide_progress();
      if (r.message && r.message.ok) {
        const x = r.message;
        frappe.msgprint({
          title: __('Item Sync Completed'),
          message:
            `<div class="alert alert-success">
               <strong>Sync Results:</strong><br>
               <table class="table table-sm">
                 <tr><td>Created Items:</td><td><strong>${x.created || 0}</strong></td></tr>
                 <tr><td>Updated Items:</td><td><strong>${x.updated || 0}</strong></td></tr>
                 <tr><td>Total Processed:</td><td><strong>${x.processed_items || 0}</strong></td></tr>
                 <tr><td>Errors:</td><td><strong>${x.errors || 0}</strong></td></tr>
               </table>
             </div>`,
          indicator: 'green'
        });
      } else {
        frappe.msgprint({
          title: __('Item Sync Failed'),
          message: `<div class="alert alert-danger">
                      ${(r.message && r.message.message) || 'An error occurred during item sync'}
                    </div>`,
          indicator: 'red'
        });
      }
    }
  });
}

/* TOKEN */
function refresh_token(frm) {
  frappe.show_progress(__('Refreshing Token...'), 30, 100, 'Please wait');
  frappe.call({
    method: "shopee_bridge.api.refresh_if_needed",
    callback(r) {
      frappe.hide_progress();
      if (!r.message) {
        frappe.show_alert({ message: __('No response from server'), indicator: 'red' }, 5);
        return;
      }
      const result = r.message;
      const status = result.status;
      let message = '', indicator = 'blue', should_reload = false;
      switch(status) {
        case 'refreshed': message = __('Token refreshed successfully'); indicator = 'green'; should_reload = true; break;
        case 'token_still_valid': message = __('Token is still valid, no refresh needed'); indicator = 'blue'; break;
        case 'no_refresh_token': message = __('No refresh token available. Please reconnect to Shopee.'); indicator = 'orange'; reset_tokens(frm); frm.save(); break;
        case 'error':
          message = __('Token refresh failed: ') + (result.message || 'Unknown error');
          indicator = 'red';
          if (result.message && /(invalid|expired|unauthorized)/i.test(result.message)) {
            reset_tokens(frm); frm.save(); message += '. Please reconnect to Shopee.';
          }
          break;
        case 'no_new_token': message = __('No new token received from Shopee'); indicator = 'orange'; break;
        default: message = __('Unexpected response: ') + status; indicator = 'red';
      }
      frappe.show_alert({ message, indicator }, 5);
      if (should_reload) {
        setTimeout(() => { frm.reload_doc(); }, 1000);
      } else {
        show_connection_status(frm);
      }
    },
    error(r) {
      frappe.hide_progress();
      let error_message = r.message || 'Server error occurred';
      if (/unauthorized|invalid token|expired/i.test(error_message)) {
        error_message += '. Please reconnect to Shopee.';
        reset_tokens(frm); frm.save();
      }
      frappe.show_alert({ message: __('Token refresh error: ') + error_message, indicator: 'red' }, 8);
      show_connection_status(frm);
    }
  });
}

function get_sync_status(frm) {
  frappe.call({
    method: "shopee_bridge.api.get_sync_status",
    callback(r) {
      if (r.message && r.message.success) {
        const s = r.message;
        frappe.msgprint({
          title: __('Sync Status'),
          message:
            `<div class="alert alert-info">
               <strong>Current Status:</strong><br>
               <table class="table table-sm">
                 <tr><td>Token Status:</td>
                     <td><span class="badge ${s.token_status === 'valid' ? 'badge-success' : 'badge-warning'}">${s.token_status}</span></td></tr>
                 <tr><td>Token Expires:</td><td>${s.token_expires ? new Date(s.token_expires).toLocaleString() : 'N/A'}</td></tr>
                 <tr><td>Last Sync:</td><td>${s.last_sync ? new Date(s.last_sync).toLocaleString() : 'Never'}</td></tr>
                 <tr><td>Total Orders Synced:</td><td><strong>${s.total_synced_orders || 0}</strong></td></tr>
                 <tr><td>Recent Errors (24h):</td><td><strong>${s.recent_errors || 0}</strong></td></tr>
                 <tr><td>Environment:</td><td><span class="badge badge-info">${s.environment || 'Test'}</span></td></tr>
               </table>
             </div>`,
          indicator: s.token_status === 'valid' ? 'green' : 'orange'
        });
      }
    }
  });
}

/* DEBUG */
function debug_signature(frm) {
  frappe.call({
    method: "shopee_bridge.api.debug_sign",
    callback(r) {
      if (!r.message) return;
      const d = r.message;
      frappe.msgprint({
        title: __('Debug Signature'),
        message:
          `<div class="alert alert-info">
             <strong>Signature Debug Info:</strong><br>
             <table class="table table-sm">
               <tr><td>Partner ID:</td><td><code>${d.partner_id}</code></td></tr>
               <tr><td>Partner Key Length:</td><td>${d.partner_key_length}</td></tr>
               <tr><td>Path:</td><td><code>${d.path}</code></td></tr>
               <tr><td>Timestamp:</td><td><code>${d.timestamp}</code></td></tr>
               <tr><td>Base String:</td><td><code>${d.base_string}</code></td></tr>
               <tr><td>Signature:</td><td><code>${d.signature}</code></td></tr>
               <tr><td>Environment:</td><td>${d.environment}</td></tr>
             </table>
           </div>`,
        indicator: 'blue'
      });
    }
  });
}

/* STATUS DISPLAY */
function show_connection_status(frm) {
  if (frm._status_shown) return;
  frm._status_shown = true;
  const html = get_status_html(frm);
  const existing = frm.$wrapper.find('#connection-status-wrapper');
  if (existing.length > 0) existing.remove();
  const wrap = $(`
    <div class="form-group" id="connection-status-wrapper">
      <div class="clearfix">
        <label class="control-label" style="margin-bottom: 5px;">Connection Status</label>
      </div>
      <div class="control-input-wrapper">
        <div class="control-input">${html}</div>
      </div>
    </div>
  `);
  if (frm.fields_dict.partner_key) {
    wrap.insertAfter(frm.fields_dict.partner_key.wrapper);
  }
}

function get_status_html(frm) {
  if (!frm.doc.partner_id || !frm.doc.partner_key) {
    return `<div class="alert alert-info" style="margin-bottom:0;"><i class="fa fa-info-circle"></i>
            <strong>Setup Required:</strong> Please configure Partner ID and Partner Key first</div>`;
  }
  if (!frm.doc.access_token) {
    return `<div class="alert alert-warning" style="margin-bottom:0;"><i class="fa fa-exclamation-triangle"></i>
            <strong>Not Connected:</strong> Click "Connect to Shopee" to authorize access.</div>`;
  }
  const now = new Date();
  const exp = frm.doc.token_expire_at ? new Date(frm.doc.token_expire_at * 1000) : null;
  if (!exp) {
    return `<div class="alert alert-warning" style="margin-bottom:0;">
              <i class="fa fa-question-circle"></i>
              <strong>Connected:</strong> Token expiration unknown.
              <button class="btn btn-sm btn-warning" onclick="refresh_token(cur_frm)" style="margin-left:10px;">Check Token</button>
            </div>`;
  }
  const timeUntilExpiry = exp - now;
  const isExpired = timeUntilExpiry <= 0;
  const isSoon = timeUntilExpiry > 0 && timeUntilExpiry < 3600000;
  if (isExpired) {
    return `<div class="alert alert-danger" style="margin-bottom:0;">
              <i class="fa fa-times-circle"></i>
              <strong>Token Expired:</strong> Expired at ${exp.toLocaleString()}.
              <button class="btn btn-sm btn-primary" onclick="connect_to_shopee(cur_frm)" style="margin-left:10px;">Reconnect</button>
            </div>`;
  }
  if (isSoon) {
    return `<div class="alert alert-warning" style="margin-bottom:0;">
              <i class="fa fa-clock-o"></i>
              <strong>Token Expires Soon:</strong> ${exp.toLocaleString()}
              <button class="btn btn-sm btn-warning" onclick="refresh_token(cur_frm)" style="margin-left:10px;">Refresh Now</button>
            </div>`;
  }
  return `<div class="alert alert-success" style="margin-bottom:0;">
            <i class="fa fa-check-circle"></i>
            <strong>Connected:</strong> Token expires ${exp.toLocaleString()}
            <button class="btn btn-sm btn-secondary" onclick="refresh_token(cur_frm)" style="margin-left:10px;">Refresh Token</button>
          </div>`;
}

/* OAUTH CALLBACK */
function handle_oauth_callback(frm) {
  const qs = new URLSearchParams(window.location.search);
  const code = qs.get('code');
  const shop_id = qs.get('shop_id');
  const error = qs.get('error');
  if (error) {
    frappe.msgprint({ title: __('Shopee Authorization Failed'), message: __('Error: ') + error + '<br><br>Please try connecting again.', indicator: 'red' });
    clear_query_string();
    return;
  }
  if (code && !frm._oauth_handled) {
    frm._oauth_handled = true;
    frappe.show_progress(__('Connecting to Shopee...'), 50, 100, 'Exchanging authorization code...');
    frappe.call({
      method: "shopee_bridge.api.exchange_code",
      args: { code: code, shop_id: shop_id || null },
      callback(r) {
        frappe.hide_progress();
        if (r.message && r.message.ok) {
          frappe.show_alert({ message: __('Shopee connected successfully!'), indicator: 'green' }, 5);
          setTimeout(() => { frm.reload_doc(); }, 1500);
        } else {
          let error_msg = 'Failed to connect to Shopee.';
          if (r.message && r.message.message) error_msg += '<br>Error: ' + r.message.message;
          frappe.msgprint({ title: __('Connection Failed'), message: error_msg, indicator: 'red' });
        }
        clear_query_string();
      },
      error(r) {
        frappe.hide_progress();
        let error_msg = r.message || 'Error during connect';
        frappe.msgprint({
          title: __('Connection Error'),
          message: 'Failed to exchange authorization code.<br>Error: ' + error_msg,
          indicator: 'red'
        });
        clear_query_string();
      }
    });
  }
}

function clear_query_string() {
  if (window.history && window.history.replaceState) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

/* Utils */
function toUnixStart(dateStr) { return Math.floor(new Date(dateStr + ' 00:00:00').getTime() / 1000); }
function toUnixEnd(dateStr)   { return Math.floor(new Date(dateStr + ' 23:59:59').getTime() / 1000); }