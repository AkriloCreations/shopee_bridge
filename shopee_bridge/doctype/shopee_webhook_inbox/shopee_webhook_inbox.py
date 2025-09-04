from frappe.model.document import Document
import frappe

class ShopeeWebhookInbox(Document):
    """
    Controller for Shopee Webhook Inbox doctype.
    Provides thin helpers for logging and job enqueueing.
    """

    def enqueue_processing(self) -> None:
        """
        Enqueue async processing of this webhook inbox entry.

        Enqueues shopee_bridge.jobs.process_webhook.run with inbox=self.name on 'short' queue.

        Idempotency: Only enqueues, does not process.
        """
        frappe.enqueue(
            "shopee_bridge.jobs.process_webhook.run",
            inbox=self.name,
            queue="short"
        )

    def make_summary(self) -> str:
        """
        Returns a short status summary for listview display.

        Returns:
            str: Summary text including status, event_type, and attempts.
        """
        return f"{self.status or 'queued'} | {self.event_type or ''} | attempts: {self.attempts or 0}"