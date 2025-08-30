# Copyright (c) 2025, AkriloCreations and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ShopeeWebhookLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		error_message: DF.LongText | None
		event_type: DF.Data | None
		headers: DF.LongText | None
		ip_address: DF.Data | None
		order_sn: DF.Data | None
		processing_time: DF.Float
		raw_data: DF.LongText | None
		response_status: DF.Literal["Success", "Failed", "Error"]
		shop_id: DF.Data | None
		source: DF.Literal["Shopee Test", "Shopee Live", "Postman Test"]
		status: DF.Literal["PROCESSED", "READY_TO_SHIP", "SHIPPED", "CANCELLED"]
		timestamp: DF.Datetime
	# end: auto-generated types
	pass
