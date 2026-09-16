"""Configuration layer: loading, env substitution and the redacted config log.

The committed ``scripts/config/config.yaml`` is a template; deployments mount
their complete configuration and supply domains/secrets as environment
variables (see DEVELOPER.md).
"""

import json
import logging
import os
import re
from pathlib import Path

import yaml

from scripts.exceptions import ConfigurationError
from scripts.utils.logging_utils import log_exception, sanitize_for_log

logger = logging.getLogger("renovate-operation-check.config_loader")

ENV_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")


def substitute_env_vars(value):
    if isinstance(value, str):

        def replace(match):
            environment_variable, default_value = match.group(1), match.group(2)
            env_value = os.environ.get(environment_variable)
            if env_value is not None:
                logger.info(f"Substituting environment variable: {environment_variable}")
                return env_value
            if default_value:
                logger.info(f"Environment variable {environment_variable} not found, using default")
                return default_value
            logger.warning(
                f"Environment variable {environment_variable} not found, using empty string"
            )
            return ""

        return ENV_VAR_PATTERN.sub(replace, value)
    elif isinstance(value, dict):
        return {k: substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [substitute_env_vars(list_item) for list_item in value]
    else:
        return value


def load_yaml_config(config_path=None):
    if not config_path:
        config_path = os.environ.get("CONFIG_PATH")
        if not config_path:
            possible_paths = [
                "./config.yaml",
                "scripts/config/config.yaml",
                "/app/config/config.yaml",
                Path(__file__).parent / "config" / "config.yaml",
            ]

            for path in possible_paths:
                if os.path.exists(path):
                    config_path = path
                    break

    try:
        if config_path and os.path.exists(config_path):
            with open(config_path, "r") as config_file:
                yaml_config = yaml.safe_load(config_file) or {}

            if not isinstance(yaml_config, dict):
                raise ConfigurationError(
                    f"Config file {sanitize_for_log(str(config_path))} must contain a mapping "
                    f"at the top level, got {type(yaml_config).__name__}"
                )

            yaml_config = substitute_env_vars(yaml_config)

            logger.info(
                f"Configuration loaded from {config_path} with environment variable substitution"
            )
            return yaml_config
        else:
            if config_path:
                logger.warning(f"Config file {config_path} not found, using defaults")
            else:
                logger.warning("No config file found, using defaults")
            return {}
    except ConfigurationError:
        # A misconfiguration must not degrade into "no config, using defaults".
        raise
    except Exception as e:
        log_exception(logger, "Error reading YAML config file", e)
        return {}


# Exact key names whose values are masked in the effective-config log.
# Exact matching on purpose: 'project_key' must stay readable.
SECRET_CONFIG_KEYS = {"token", "password", "secret", "key", "webhook_key", "app_password"}


def _redact_config(value, key=None):
    """Deep-copy a config tree for logging: secret values become '***'
    (empty secrets stay visibly empty), all strings are control-character
    sanitized so config/env content cannot forge log lines."""
    if isinstance(value, dict):
        return {k: _redact_config(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    if key and key.lower() in SECRET_CONFIG_KEYS:
        return "***" if value else ""
    if isinstance(value, str):
        return sanitize_for_log(value)
    return value


def log_effective_config(config):
    """Log the configuration as the run understood it (after env substitution).

    Diagnosis aid: a wrong PROJECT_KEY or base_url is visible here
    immediately, instead of surfacing later as an HTTP 404. Secrets are
    masked; an empty value shows as empty so an unset variable is visible.
    """
    logger.info(
        "Effective configuration (secrets masked):\n"
        + json.dumps(_redact_config(config), indent=2, default=str)
    )
