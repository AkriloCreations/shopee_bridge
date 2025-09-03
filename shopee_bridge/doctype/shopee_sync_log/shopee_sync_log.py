"""
Shopee Sync Log doctype server methods.
"""

def log_sync(job: str, key: str, status: str, payload_hash: str, message: str, meta_json: str = ""):
    """
    Log sync event to Shopee Sync Log.
    Args:
        job: Job name.
        key: Idempotency key.
        status: ok/fail/skip.
        payload_hash: SHA1 hash of payload.
        message: Log message.
        meta_json: Optional metadata.
    """
    # TODO: Insert log row
    pass
