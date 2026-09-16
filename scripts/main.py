"""Entry point: argument parsing, logging setup and run orchestration.

Runnable as ``python -m scripts.main`` or via the ``renovate-operation-check``
console script; configuration handling lives in ``scripts.config_loader``.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime

from scripts.config_loader import load_yaml_config, log_effective_config
from scripts.core.workflow_manager import WorkflowManager
from scripts.notifications.notification_factory import NotificationFactory
from scripts.utils.logging_utils import log_exception, setup_enhanced_logging

setup_enhanced_logging()
logger = logging.getLogger("renovate-operation-check.main")


def configure_logging(args, config):
    log_level = None
    log_file = None

    if args.log_level:
        log_level = args.log_level
        logger.info(f"Using --log-level command line option: {log_level}")
    elif args.verbose:
        log_level = "DEBUG"
        logger.info("Using --verbose command line flag for logging level: DEBUG")
    elif config and "logging" in config:
        if "level" in config["logging"]:
            log_level = config["logging"]["level"]
            logger.info(f"Using config.yaml logging level: {log_level}")

    if args.log_file:
        log_file = args.log_file
        logger.info(f"Using --log-file command line option: {log_file}")
    elif config and "logging" in config:
        if "file" in config["logging"]:
            log_file = config["logging"]["file"]
            logger.info(f"Using config.yaml log file: {log_file}")

    if log_level:
        try:
            numeric_level = getattr(logging, log_level.upper())
            logging.getLogger().setLevel(numeric_level)
            logger.setLevel(numeric_level)
            logger.info(f"Logging level set to {log_level.upper()}")
        except (AttributeError, TypeError) as e:
            log_exception(
                logger,
                f"Invalid log level specified: {log_level}. Using default",
                e,
                include_traceback=False,
            )

    if log_file:
        if not log_file.strip():
            logger.warning("Empty log file path specified. Skipping file logging.")
        else:
            try:
                log_dir = os.path.dirname(log_file)
                if log_dir:
                    os.makedirs(log_dir, exist_ok=True)

                file_handler = logging.FileHandler(log_file)
                file_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
                    )
                )
                logging.getLogger().addHandler(file_handler)
                logger.info(f"Logging to file: {log_file}")
            except Exception as e:
                log_exception(logger, "Failed to configure log file", e)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Renovate PR Check Framework")

    check_group = parser.add_argument_group("Checking")
    check_group.add_argument(
        "--enable-pr-cleanup",
        type=str,
        choices=["true", "false"],
        default=None,
        help="If true, run the full PR and branch cleanup; if false, only delete declined PRs",
    )
    check_group.add_argument(
        "--enable-pr-check",
        type=str,
        choices=["true", "false"],
        default=None,
        help="If true, enable running Renovate bot PR checks; if false, skip PR checks",
    )

    config_group = parser.add_argument_group("Configuration")
    config_group.add_argument("--config", help="Path to config.yaml")
    config_group.add_argument(
        "--verbose", action="store_true", help="Enable verbose logging (sets log level to DEBUG)"
    )
    config_group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set specific logging level (overrides --verbose and config.yaml setting)",
    )
    config_group.add_argument("--log-file", help="Path to log file (overrides config.yaml setting)")

    return parser.parse_args()


def _base_result(config, started):
    """Result skeleton shared by the normal and the aborted run."""
    client_config = config.get("client") if isinstance(config.get("client"), dict) else {}
    return {
        "project": client_config.get("project_key", "?"),
        "repo": client_config.get("repo_slug", "?"),
        "base_url": client_config.get("base_url", ""),
        "finished_at": datetime.now().astimezone(),
        "duration_seconds": time.monotonic() - started,
        "run_url": os.environ.get("JOB_URL", ""),
    }


def _notify_aborted_run(config, started, exception):
    """Send a failure notification for a run that never reached its checks.

    Only the exception type goes into the message - the details stay in the
    log, so no internal paths or credentials leak into a chat channel.
    """
    result = _base_result(config, started)
    result.update(
        {
            "success": False,
            "pr_check_errors": f"Run aborted before completion ({type(exception).__name__}), "
            "see job log",
            "check_summary": None,
            "cleanup_passed": False,
            "cleanup_stats": {},
        }
    )
    try:
        NotificationFactory.create_notification(
            result, config.get("notifications"), global_config=config
        )
    except Exception as notification_error:
        log_exception(logger, "Could not send the failure notification", notification_error)


def main():
    args = parse_arguments()
    started = time.monotonic()
    config = {}

    try:
        config = load_yaml_config(args.config if args.config else None)
        configure_logging(args, config)
        log_effective_config(config)
        return _run(args, config, started)
    except Exception as e:
        # Without this the run died on a bare traceback: exit code 1, but no
        # notification for the very failures operators most need to see.
        log_exception(logger, "Renovate Operation Check aborted", e)
        _notify_aborted_run(config, started, e)
        return 1


def _run(args, config, started):
    workflow_manager = WorkflowManager(args, config)

    logger.info("Starting PR checks...")
    pr_check_errors, check_summary = workflow_manager.check_prs()

    logger.info("Starting cleanup operations...")
    # Run cleanup even when checks fail, otherwise declined PRs pile up and block
    # Renovate from recreating them. A failed check still fails the run below.
    cleanup_passed = workflow_manager.cleanup_prs()

    success = pr_check_errors == "" and cleanup_passed

    bitbucket = getattr(workflow_manager, "bitbucket", None)
    result = _base_result(config, started)
    result.update(
        {
            "success": success,
            "pr_check_errors": pr_check_errors,
            "check_summary": check_summary,
            "cleanup_passed": cleanup_passed,
            "cleanup_stats": getattr(bitbucket, "stats", None) or {},
        }
    )

    if pr_check_errors != "":
        logger.error(f"PR checks failed {pr_check_errors}")
    if not cleanup_passed:
        logger.error("Cleanup operations failed")

    notification_sent = NotificationFactory.create_notification(
        result, config.get("notifications"), global_config=config
    )

    if success:
        logger.info("Renovate Operation Check operations completed successfully")
        return 0 if notification_sent else 1
    logger.error("Renovate Operation Check operations failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
