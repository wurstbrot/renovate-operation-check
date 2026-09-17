import logging
import re
from datetime import datetime

from scripts.exceptions import BitbucketApiError
from scripts.renovate_rules import pr_author
from scripts.utils.logging_utils import sanitize_for_log

logger = logging.getLogger("renovate-operation-check")


class CheckPrManager:

    def __init__(self, bitbucket_facade, config=None):
        self.bitbucket = bitbucket_facade
        self.config = config
        self.check_definitions = None
        self._load_check_definitions()

    def check_renovate_prs(self):
        """Run all check definitions.

        Returns (error_message, summary): error_message is "" on success,
        summary holds the open-PR count and per-check results for reporting.
        """
        logger.info("Checking Renovate PRs...")

        if not self.check_definitions:
            logger.error("No check definitions loaded!")
            return "No check definitions loaded", None

        try:
            renovate_prs = self.bitbucket.get_renovate_prs()
        except BitbucketApiError as e:
            # The PR list is unknown, not empty - report a failed check instead
            # of claiming "Renovate created no PRs".
            logger.error(f"Could not fetch Renovate PRs: {e}")
            return f"Could not fetch Renovate PRs: {e}", None

        if not renovate_prs:
            logger.error(
                "No open Renovate PRs found! Renovate didn't create PRs since last deletion. Please check Renovate is doing it's job"
            )
            return "No open Renovate PRs found", {"open_pr_count": 0, "check_results": []}

        logger.info(f"Found {len(renovate_prs)} open Renovate PRs")
        logger.info(f"Running {len(self.check_definitions)} check definitions")

        for pr in renovate_prs:
            logger.info(f"Found Renovate PR:\n{self.get_pr_details_string(pr)}")

        check_results = []
        error_message = ""
        for check_def in self.check_definitions:
            check_name = check_def.get("name", "Unnamed check")
            logger.info(f"\n--- Running check definition '{check_name}' ---")

            matching_prs = []
            invalid_regex_error = None
            try:
                for pr in renovate_prs:
                    if self.check_pr_against_definition(pr, check_def):
                        matching_prs.append(pr)
                        logger.info(f"PR #{pr.get('id')} matches check '{check_name}'")
            except re.error as e:
                invalid_regex_error = e
                matching_prs = []
                logger.error(f"Invalid regular expression in check definition '{check_name}': {e}")

            if invalid_regex_error is not None:
                error_message = (
                    error_message
                    + f"\nCheck definition '{check_name}' has an invalid regular expression"
                )
            elif matching_prs:
                logger.info(f"Check definition '{check_name}' matched {len(matching_prs)} PR(s)")
            else:
                error_message = (
                    error_message + f"\nCheck definition '{check_name}' has no matching PRs"
                )
                logger.warning(f"Check definition '{check_name}' has no matching PRs")

            check_results.append(
                {
                    "check_name": check_name,
                    "passed": bool(matching_prs),
                    "matching_prs": [pr.get("id") for pr in matching_prs],
                    "match_count": len(matching_prs),
                }
            )

        logger.info("\n===== PR Check Summary =====")
        passed_count = sum(1 for r in check_results if r["passed"])
        logger.info(f"Total check definitions: {len(check_results)}")
        logger.info(f"Check definitions with matching PRs: {passed_count}")
        logger.info(f"Check definitions without matching PRs: {len(check_results) - passed_count}")

        result_status = "PASSED" if error_message == "" else "FAILED"
        logger.info(f"PR check completed with result: {result_status}")

        return error_message, {
            "open_pr_count": len(renovate_prs),
            "check_results": check_results,
        }

    def check_pr_against_definition(self, pr, check_def):
        check_name = check_def.get("name", "Unnamed check")
        title_regex = check_def.get("titleRegex")
        content_regex = check_def.get("contentRegex")

        title = pr.get("title", "")
        description = pr.get("description", "")

        if title_regex and not re.search(title_regex, title):
            logger.debug(
                f"PR {pr.get('id')} failed check '{check_name}': title does not match '{title_regex}'"
            )
            return False

        if content_regex and not re.search(content_regex, description):
            logger.debug(
                f"PR {pr.get('id')} failed check '{check_name}': description does not match '{content_regex}'"
            )
            return False

        logger.info(f"PR {pr.get('id')} passed check '{check_name}'")
        return True

    def _load_check_definitions(self):
        try:
            if self.config and "pr_checks" in self.config:
                self.check_definitions = self.config["pr_checks"]

            if self.check_definitions:
                logger.info(f"Loaded {len(self.check_definitions)} PR check definitions")
            else:
                logger.warning("No 'pr_checks' section found in config")
        except Exception as e:
            logger.error(f"Error loading check definitions: {e}")

    def get_pr_details_string(self, pr):
        details = []

        details.append(f"PR #{pr.get('id')}: {sanitize_for_log(pr.get('title', 'No Title'))}")

        details.append(f"Author: {sanitize_for_log(pr_author(pr, default='Unknown'))}")

        if "fromRef" in pr and "displayId" in pr["fromRef"]:
            details.append(
                f"Source Branch: {sanitize_for_log(pr['fromRef'].get('displayId', 'Unknown'))}"
            )

        if "toRef" in pr and "displayId" in pr["toRef"]:
            details.append(
                f"Target Branch: {sanitize_for_log(pr['toRef'].get('displayId', 'Unknown'))}"
            )

        created_date = pr.get("createdDate", "Unknown")
        if created_date and created_date != "Unknown":
            try:
                if isinstance(created_date, (int, float)):
                    created_date = datetime.fromtimestamp(created_date / 1000).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
            except Exception:  # nosec B110 - best-effort date formatting; fall back to raw value
                pass
            details.append(f"Created: {created_date}")

        if "state" in pr:
            details.append(f"State: {pr.get('state', 'Unknown')}")

        description = pr.get("description", "")
        if description:
            details.append(f"Description: {sanitize_for_log(description, max_length=100)}")

        return "\n".join(details)
