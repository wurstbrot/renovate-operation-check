"""Render a run result into notification title and Markdown body."""

import re

from scripts.utils.logging_utils import sanitize_for_log

# Mattermost truncates posts around 16k characters; stay safely below.
MAX_BODY_LENGTH = 14000

_MENTION_PATTERN = re.compile(r"@(channel|all|here)\b", re.IGNORECASE)


def sanitize_for_message(value, max_length=300):
    """Neutralize control characters and Mattermost mentions in dynamic text.

    A zero-width space after the '@' keeps the text readable but prevents
    third-party-influenced strings from pinging the whole channel.
    """
    text = sanitize_for_log(value, max_length)
    return _MENTION_PATTERN.sub("@​\\1", text)


def format_result(result):
    """Build (title, markdown_body) from the structured run result."""
    status = "OK" if result.get("success") else "FAILED"
    repo = f"{result.get('project', '?')}/{result.get('repo', '?')}"
    title = f"[{status}] Renovate Operation Check - {sanitize_for_message(repo)}"

    lines = []

    if result.get("base_url"):
        lines.append(f"Bitbucket: {sanitize_for_message(result['base_url'])}")

    finished_at = result.get("finished_at")
    if finished_at is not None:
        stamp = finished_at.strftime("%Y-%m-%d %H:%M:%S %Z")
        duration = result.get("duration_seconds")
        suffix = f" ({duration:.0f}s)" if duration is not None else ""
        lines.append(f"Finished: {stamp}{suffix}")

    lines.extend(_check_lines(result))
    lines.append(_cleanup_line(result))

    if result.get("run_url"):
        lines.append(f"[Job log]({sanitize_for_message(result['run_url'])})")

    body = "\n".join(lines)
    if len(body) > MAX_BODY_LENGTH:
        body = body[:MAX_BODY_LENGTH] + "\n...[truncated]"
    return title, body


def _check_lines(result):
    summary = result.get("check_summary")
    error_message = result.get("pr_check_errors", "")

    if summary is None:
        if error_message:
            return [f"PR checks: FAILED - {sanitize_for_message(error_message, 1000)}"]
        return ["PR checks: skipped"]

    checks = summary.get("check_results") or []
    passed = [check for check in checks if check.get("passed")]
    failed = [check for check in checks if not check.get("passed")]

    lines = [
        f"PR checks: {len(passed)}/{len(checks)} passed - "
        f"{summary.get('open_pr_count', 0)} open Renovate PRs"
    ]
    for check in failed:
        lines.append(
            f"- FAILED {sanitize_for_message(check.get('check_name', '?'))}: no matching PR"
        )
    if passed:
        lines.append(
            "Passed: "
            + " | ".join(
                f"{sanitize_for_message(check.get('check_name', '?'))}"
                f" ({check.get('match_count', 0)})"
                for check in passed
            )
        )
    return lines


def _cleanup_line(result):
    stats = result.get("cleanup_stats") or {}
    state = "OK" if result.get("cleanup_passed") else "FAILED"
    return (
        f"Cleanup: {state} - {stats.get('prs_declined', 0)} PRs declined | "
        f"{stats.get('prs_deleted', 0)} PRs deleted | "
        f"{stats.get('branches_deleted', 0)} branches deleted"
    )
