# Copyright (c) 2025, AkriloCreations and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ShopeeSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_token: DF.Data | None
		environment: DF.Data | None
		last_success_update_time: DF.Int
		migration_cutoff_date: DF.Date | None
		migration_mode: DF.Check
		overlap_seconds: DF.Int
		partner_id: DF.Data
		partner_key: DF.Data
		refresh_token: DF.Data | None
		region: DF.Data | None
		shop_id: DF.Data | None
		token_expire_at: DF.Int
		use_sales_order_flow: DF.Check
		webhook_key: DF.Data | None
		webhook_test_key: DF.Data | None
	# end: auto-generated types
	pass
