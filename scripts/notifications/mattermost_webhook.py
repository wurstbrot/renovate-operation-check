import requests
import os
import logging

from scripts.notifications import REQUEST_TIMEOUT
from scripts.utils.ssl_adapter import mount_if_enabled

logger = logging.getLogger("renovate-operation-check.mattermost")


class MattermostWebhook:
    def __init__(self, config):
        self.api_url = config.get("url")
        self.webhook_key = config.get("webhook_key")
        self.success_msg = config.get("success_msg", False)

        self.session = requests.Session()
        mount_if_enabled(self.session, config.get("relax_x509_strict", False), "MattermostWebhook")
        if config.get("ca_cert_path"):
            ca_path = config["ca_cert_path"]
            if os.path.exists(ca_path):
                logger.info(f"Using custom CA certificate from: {ca_path}")
                self.session.verify = ca_path
            else:
                logger.warning(f"Specified CA certificate path not found: {ca_path}")
                logger.warning("Falling back to default CA certificates")

    def send_message(self, title, message, is_success=None):
        headers = {"Content-Type": "application/json"}

        attachment = {"title": title, "text": message}
        if is_success is not None:
            attachment["color"] = "#2fa44f" if is_success else "#d24b47"
        payload = {"attachments": [attachment]}

        try:
            response = self.session.post(
                f"{self.api_url}/hooks/{self.webhook_key}",
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in [200, 201]:
                logger.info("Message sent successfully to webhook")
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending message to Mattermost: {str(e)}")
            return False

    def should_send_success_message(self):
        return self.success_msg
