import logging
from scripts.notifications.mattermost_webhook import MattermostWebhook
from scripts.notifications.mattermost import Mattermost
from scripts.notifications.message_formatter import format_result
from scripts.utils.logging_utils import log_exception

logger = logging.getLogger("renovate-operation-check.mattermost")

PROVIDERS = {
    "mattermostWebhook": lambda config: MattermostWebhook(config),
    "mattermost": lambda config: Mattermost(config.get("url"), config=config),
}

INHERITED_GLOBAL_KEYS = ("ca_cert_path", "relax_x509_strict")


class NotificationFactory:
    @staticmethod
    def create_notification(result, notifications_config, global_config=None):
        title, body = format_result(result)
        is_success = bool(result.get("success"))

        logger.info(f"## {title}\n\n{body}")

        if not isinstance(notifications_config, dict) or not notifications_config.get(
            "enabled", False
        ):
            if notifications_config and not isinstance(notifications_config, dict):
                logger.warning("'notifications' is not a configuration mapping, ignoring it")
            else:
                logger.info("Notifications are disabled in config")
            return True

        notification_sent = True
        for provider_name, provider_config in notifications_config.items():
            if provider_name == "enabled":
                continue

            if not isinstance(provider_config, dict):
                notification_sent = False
                logger.warning(
                    f"Notification provider '{provider_name}' is not a configuration mapping, "
                    "skipping it"
                )
                continue

            if not provider_config.get("enabled", False):
                logger.debug(f"Skipping disabled provider: {provider_name}")
                continue

            provider_config = provider_config.copy()
            for key in INHERITED_GLOBAL_KEYS:
                if global_config and global_config.get(key) and key not in provider_config:
                    provider_config[key] = global_config[key]

            create_client = PROVIDERS.get(provider_name)
            if not create_client:
                notification_sent = False
                logger.warning(f"Unknown notification provider: {provider_name}")
                continue

            try:
                # Client construction belongs inside the guard: an incomplete
                # provider config (e.g. mattermost without url) used to raise
                # here and take down the whole run after all work was done.
                client = create_client(provider_config)

                if (
                    is_success
                    and hasattr(client, "should_send_success_message")
                    and not client.should_send_success_message()
                ):
                    logger.info(f"Skipping success message for {provider_name} (success_msg=false)")
                    continue

                sent = client.send_message(title, body, is_success=is_success)
                logger.info(
                    f"Notification sent via {provider_name}: {'SUCCESS' if sent else 'FAILED'}"
                )
                if not sent:
                    notification_sent = False
            except Exception as e:
                notification_sent = False
                log_exception(logger, f"Failed to send notification via {provider_name}", e)

        return notification_sent
