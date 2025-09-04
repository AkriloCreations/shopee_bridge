def get_data():
    """Expose Shopee Bridge desk module with Shopee Settings card."""
    return [
        {
            "label": "Shopee Bridge",
            "items": [
                {
                    "type": "doctype",
                    "name": "Shopee Settings",
                    "label": "Shopee Settings",
                    "icon": "octicon octicon-link",
                    "description": "Connect and configure Shopee integration"
                }
            ]
        }
    ]