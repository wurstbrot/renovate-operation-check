import requests
import os
import logging

from scripts.notifications import REQUEST_TIMEOUT
from scripts.utils.ssl_adapter import mount_if_enabled

logger = logging.getLogger("renovate-operation-check.mattermost")


class Mattermost:
    def __init__(self, url, token=None, config=None):
        self.base_url = url.rstrip("/")
        if "/login" in self.base_url:
            self.base_url = self.base_url.replace("/login", "")

        if not self.base_url.endswith("/api/v4"):
            self.api_url = f"{self.base_url}/api/v4"
        else:
            self.api_url = self.base_url

        self.token = (
            token
            or (config.get("token") if config else None)
            or os.environ.get("MATTERMOST_TOKEN", "")
        )
        self.channel_id = config.get("channel_id") if config else None

        self.session = requests.Session()
        mount_if_enabled(
            self.session,
            bool(config and config.get("relax_x509_strict")),
            "Mattermost",
        )
        if config and config.get("ca_cert_path") and config["ca_cert_path"].strip():
            ca_path = config["ca_cert_path"].strip()
            if os.path.exists(ca_path):
                logger.info(f"Using custom CA certificate from: {ca_path}")
                self.session.verify = ca_path
            else:
                logger.warning(f"Specified CA certificate path not found: {ca_path}")
                logger.warning("Falling back to default CA certificates")

    def send_message(self, title, message, is_success=None):
        if not self.token:
            logger.error("Cannot send message: No Mattermost token available")
            return False

        if not self.channel_id:
            logger.error("Cannot send message: No channel ID specified")
            return False

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        payload = {
            "channel_id": self.channel_id,
            "message": f"### {title}\n{message}",
        }

        try:
            response = self.session.post(
                f"{self.api_url}/posts",
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code in [200, 201]:
                logger.info(f"Message sent successfully to channel {self.channel_id}")
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending message to Mattermost: {str(e)}")
            return False
