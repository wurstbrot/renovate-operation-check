#!/usr/bin/env python3

import logging
import os
import re
import traceback

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_for_log(value, max_length=300):
    """Neutralize control characters in untrusted text before logging.

    CR/LF and other control characters allow forged log lines when
    third-party text (PR titles, branch names, ...) is logged verbatim.
    """
    text = _CONTROL_CHARS.sub(" ", str(value))
    if len(text) > max_length:
        text = text[:max_length] + "...[truncated]"
    return text


def setup_enhanced_logging(level=logging.INFO, log_file=None):
    format_string = (
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    )

    handlers = [logging.StreamHandler()]

    if log_file:
        try:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(logging.Formatter(format_string))
            handlers.append(file_handler)
        except Exception as e:
            print(f"Warning: Failed to set up log file {log_file}: {e}")

    logging.basicConfig(
        level=level,
        format=format_string,
        handlers=handlers,
        force=True,
    )

    return logging.getLogger()


def log_exception(logger_instance, message, exception, include_traceback=True):
    error_details = [f"{message}: {str(exception)}", f"Exception type: {type(exception).__name__}"]

    if include_traceback:
        tb_lines = traceback.format_exc().strip().split("\n")
        error_details.extend(["Traceback:", *tb_lines])

    logger_instance.error("\n".join(error_details))
