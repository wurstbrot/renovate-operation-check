import logging
import time

from scripts.exceptions import BitbucketApiError, ConfigurationError
from scripts.renovate_rules import is_renovate_branch, source_branch
from scripts.utils.logging_utils import log_exception, sanitize_for_log

logger = logging.getLogger("renovate-operation-check.core.cleanup_manager")


class CleanupManager:
    """Cleanup policy and flows: which PRs and branches are declined or
    deleted, and in which order. The Bitbucket client only executes the
    individual API operations."""

    def __init__(self, bitbucket, config):
        self.bitbucket = bitbucket
        self.config = config or {}
        # WorkflowManager passes the 'cleanup' section of config.yaml directly,
        # other callers may pass the full config - accept both shapes.
        nested_cleanup = self.config.get("cleanup") if isinstance(self.config, dict) else None
        self.cleanup_config = nested_cleanup if isinstance(nested_cleanup, dict) else self.config

    @staticmethod
    def _is_enabled(value):
        # config.yaml delivers booleans, command line options deliver 'true'/'false' strings
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)

    def _branch_reset_targets(self):
        """Explicit source and target branch for the branch reset.

        Deliberately without defaults: the branch reset force-rewrites the
        target branch, so guessing a branch name here is not acceptable.
        """
        target_branch = self.cleanup_config.get("branch_reset_target")
        reference_branch = self.cleanup_config.get("branch_reset_source")
        if not target_branch or not reference_branch:
            raise ConfigurationError(
                "cleanup.enable_branch_reset is enabled, but cleanup.branch_reset_target "
                "and cleanup.branch_reset_source are not both set. Name both branches "
                "explicitly - the branch reset rewrites the target branch."
            )
        return target_branch, reference_branch

    def cleanup(self):
        enable_branch_reset = self._is_enabled(
            self.cleanup_config.get("enable_branch_reset", False)
        )
        skip_branch_reset = not enable_branch_reset
        enable_pr_decline = self._is_enabled(self.cleanup_config.get("enable_pr_decline", True))
        skip_pr_cleanup = not enable_pr_decline
        full_cleanup_mode = self._is_enabled(self.cleanup_config.get("enable_pr_cleanup", False))

        if not self.bitbucket.verify_permissions():
            logger.error(
                "Could not authenticate against Bitbucket or read the repository. "
                "Aborting cleanup instead of reporting an empty run as success."
            )
            return False

        if not skip_branch_reset:
            target_branch, reference_branch = self._branch_reset_targets()
            try:
                success = self.reset_branch_from_reference(target_branch, reference_branch)
                if not success:
                    logger.error("Failed to reset branch")
                    return False
            except Exception as e:
                log_exception(logger, "Failed to reset branch", e)
                logger.error(
                    "If this is a permission issue, set cleanup.enable_branch_reset: false in config.yaml"
                )
                return False
        else:
            if full_cleanup_mode:
                logger.info("In PR cleanup mode: Skipping branch reset")
            else:
                logger.warning("Skipping branch reset as requested")

        if full_cleanup_mode or not skip_pr_cleanup:
            try:
                if full_cleanup_mode:
                    logger.info("In PR cleanup mode: Declining up renovate PRs")

                delete_prs_after_decline = (
                    self._is_enabled(
                        self.cleanup_config.get("delete_branches_after_pr_decline", True)
                    )
                    if self.cleanup_config
                    else False
                )
                self.decline_renovate_prs(delete_prs_after_decline=delete_prs_after_decline)

                if full_cleanup_mode:
                    logger.info("Cleaning up renovate branches...")
                    self.cleanup_renovate_branches(
                        delete_prs_after_decline=delete_prs_after_decline
                    )

                    if self._is_enabled(
                        self.cleanup_config.get("decline_all_in_pr_cleanup", False)
                    ):
                        logger.info("Declining all branches except protected ones...")
                        self.decline_all_branches_except(
                            self.cleanup_config.get("exclude_branches") or None
                        )
            except Exception as e:
                log_exception(logger, "Failed to clean up PRs/branches", e)
                if full_cleanup_mode:
                    logger.error(
                        "If this is a permission issue, run with --enable-pr-cleanup false"
                    )
                else:
                    logger.error(
                        "If this is a permission issue, set cleanup.enable_pr_decline: false in config.yaml"
                    )
                return False
        else:
            logger.warning("Skipping PR cleanup as requested")

        return True

    def cleanup_declined_prs(self):
        try:
            self.delete_declined_renovate_prs()
            return True
        except Exception as e:
            log_exception(logger, "Failed to delete declined PRs", e)
            return False

    def _delete_source_branch_if_renovate(self, branch_name, pr_id, context):
        """Delete a renovate/* source branch after its PR left the OPEN state."""
        if not is_renovate_branch(branch_name):
            return False
        logger.info(f"Deleting branch '{sanitize_for_log(branch_name)}' {context} PR #{pr_id}")
        deleted = self.bitbucket.delete_branch_by_name(branch_name)
        if deleted:
            logger.info(
                f"Successfully deleted branch '{sanitize_for_log(branch_name)}' {context} PR #{pr_id}"
            )
        else:
            logger.warning(
                f"Failed to delete branch '{sanitize_for_log(branch_name)}' {context} PR #{pr_id}"
            )
        return deleted

    def _decline_pr_and_delete_renovate_branch(self, pr_id, branch_name):
        """Decline a PR; if it succeeds, a renovate/* source branch is deleted."""
        if not self.bitbucket.decline_pull_request(pr_id):
            return False
        self._delete_source_branch_if_renovate(branch_name, pr_id, "after declining")
        return True

    def _delete_pr_after_decline(self, pr_id):
        logger.info(f"Now deleting PR #{pr_id}")
        if self.bitbucket.delete_pull_request(pr_id):
            logger.info(f"Successfully deleted PR #{pr_id}")
        else:
            logger.warning(f"Failed to delete PR #{pr_id}")

    def delete_declined_renovate_prs(self):
        return self.decline_renovate_prs_by_state(["DECLINED"], True)

    def decline_renovate_prs(self, delete_prs_after_decline=True):
        return self.decline_renovate_prs_by_state(["OPEN", "DECLINED"], delete_prs_after_decline)

    def decline_renovate_prs_by_state(self, states=None, delete_prs_after_decline=True):
        states = states or ["OPEN", "DECLINED"]
        logger.info("Checking for all PRs from renovate branches to decline...")
        if delete_prs_after_decline:
            logger.info(
                "Will also delete PRs after declining (delete_branches_after_pr_decline is enabled)"
            )

        declined_count = 0
        for state in states:
            prs = self.bitbucket.get_pull_requests(state=state)

            for pr in prs.get("values", []):
                pr_id = pr.get("id")
                pr_title = pr.get("title", "")
                branch_name = source_branch(pr) or ""

                if not (pr_id and is_renovate_branch(branch_name)):
                    logger.debug(f"Skipping PR #{pr_id}: Not from a renovate branch")
                    continue

                logger.info(
                    f"Trying to decline PR #{pr_id}: '{sanitize_for_log(pr_title)}' from renovate branch '{sanitize_for_log(branch_name)}'"
                )
                if state == "DECLINED":
                    logger.info(
                        f"PR #{pr_id} is already declined, checking if branch '{sanitize_for_log(branch_name)}' still exists"
                    )
                    if self.bitbucket.delete_branch_by_name(branch_name):
                        logger.info(
                            f"Successfully deleted branch '{sanitize_for_log(branch_name)}' for already declined PR #{pr_id}"
                        )
                        declined_count += 1

                    if delete_prs_after_decline:
                        self._delete_pr_after_decline(pr_id)
                elif self._decline_pr_and_delete_renovate_branch(pr_id, branch_name):
                    declined_count += 1
                    if delete_prs_after_decline:
                        self._delete_pr_after_decline(pr_id)
                else:
                    logger.warning(
                        f"Failed to decline PR #{pr_id} from branch '{sanitize_for_log(branch_name)}'"
                    )
                    if self.bitbucket.delete_branch_by_name(branch_name):
                        logger.info(
                            f"Successfully deleted branch '{sanitize_for_log(branch_name)}' after failed PR decline"
                        )
                        declined_count += 1

        if declined_count > 0:
            logger.info(f"Declined {declined_count} PRs from renovate branches")
        else:
            logger.info("No PRs from renovate branches found to decline")

        return declined_count

    def cleanup_renovate_branches(self, delete_prs_after_decline=False):
        logger.info("Cleaning up stale Renovate branches...")
        if delete_prs_after_decline:
            logger.info(
                "Will also delete PRs after declining (delete_branches_after_pr_decline is enabled)"
            )

        branches = self.bitbucket.get_branches()
        deleted_count = 0
        renovate_branch_count = 0

        if "values" not in branches:
            logger.warning("Could not retrieve branches from repository")
            return 0

        all_prs = {}
        pr_count_by_state = {"OPEN": 0, "DECLINED": 0, "MERGED": 0}

        logger.info("Fetching PRs to check for associated branches...")

        for state in ["OPEN", "DECLINED", "MERGED"]:
            prs = self.bitbucket.get_pull_requests(state=state)
            values = prs.get("values", [])
            pr_count_by_state[state] = len(values)
            for pr in values:
                if pr.get("id"):
                    all_prs[pr["id"]] = pr

        logger.info(
            f"Processing branches: Total PRs found: {len(all_prs)} (OPEN: {pr_count_by_state['OPEN']}, DECLINED: {pr_count_by_state['DECLINED']}, MERGED: {pr_count_by_state['MERGED']})"
        )

        for branch in branches["values"]:
            branch_name = branch.get("displayId", branch.get("name"))

            if branch_name and is_renovate_branch(branch_name):
                renovate_branch_count += 1
                logger.info(f"Processing renovate branch: {sanitize_for_log(branch_name)}")

                associated_pr_id = None
                associated_pr_state = None
                associated_pr_title = None

                for pr_id, pr in all_prs.items():
                    if source_branch(pr) == branch_name:
                        associated_pr_id = pr_id
                        associated_pr_state = pr.get("state")
                        associated_pr_title = pr.get("title", "No title")
                        break

                if associated_pr_id is None:
                    logger.info(
                        f"Found stale renovate branch {sanitize_for_log(branch_name)} with no PR, deleting branch"
                    )
                    if self.bitbucket.delete_branch_by_name(branch_name):
                        deleted_count += 1
                        logger.info(
                            f"Successfully deleted stale branch {sanitize_for_log(branch_name)}"
                        )
                    else:
                        logger.warning(
                            f"Failed to delete stale branch {sanitize_for_log(branch_name)}"
                        )

                elif associated_pr_state == "OPEN":
                    logger.info(
                        f"Branch {sanitize_for_log(branch_name)} has an open PR #{associated_pr_id}: '{sanitize_for_log(associated_pr_title)}', attempting to decline PR"
                    )
                    if self._decline_pr_and_delete_renovate_branch(
                        associated_pr_id, branch_name
                    ):
                        logger.info(f"Successfully declined PR #{associated_pr_id}")
                        if delete_prs_after_decline:
                            self._delete_pr_after_decline(associated_pr_id)
                    else:
                        logger.warning(
                            f"Failed to decline PR #{associated_pr_id}, trying direct branch deletion"
                        )
                        if self.bitbucket.delete_branch_by_name(branch_name):
                            deleted_count += 1
                            logger.info(
                                f"Successfully deleted branch {sanitize_for_log(branch_name)} despite PR decline failure"
                            )

                elif associated_pr_state in ("DECLINED", "MERGED"):
                    logger.info(
                        f"Found branch {sanitize_for_log(branch_name)} with {associated_pr_state.lower()} PR #{associated_pr_id}: '{sanitize_for_log(associated_pr_title)}', deleting branch"
                    )
                    if self.bitbucket.delete_branch_by_name(branch_name):
                        deleted_count += 1
                        logger.info(
                            f"Successfully deleted branch {sanitize_for_log(branch_name)} associated with {associated_pr_state.lower()} PR #{associated_pr_id}"
                        )
                    else:
                        logger.warning(
                            f"Failed to delete branch {sanitize_for_log(branch_name)} associated with {associated_pr_state.lower()} PR #{associated_pr_id}"
                        )

                    if delete_prs_after_decline:
                        self._delete_pr_after_decline(associated_pr_id)
            time.sleep(0.2)
        logger.info(
            f"Renovate branch deletion completed. Processed {renovate_branch_count} renovate branches. Deleted {deleted_count} branches."
        )
        return deleted_count

    def decline_open_prs_for_branch(self, branch_name):
        """Decline all open PRs originating from a branch."""
        logger.info(f"Branch deletion requested for '{sanitize_for_log(branch_name)}'")

        branch_info = self.bitbucket.get_branch_info(branch_name)
        if not branch_info:
            logger.warning(f"Branch '{sanitize_for_log(branch_name)}' not found, skipping deletion")
            return True

        try:
            prs = self.bitbucket.get_pull_requests(state="OPEN")
            declined_count = 0

            for pr in prs.get("values", []):
                if source_branch(pr) == branch_name and pr.get("id"):
                    pr_id = pr["id"]
                    logger.info(
                        f"Found PR #{pr_id} for branch {sanitize_for_log(branch_name)}, declining it"
                    )
                    if self._decline_pr_and_delete_renovate_branch(pr_id, branch_name):
                        declined_count += 1
                    else:
                        logger.warning(f"Failed to decline PR #{pr_id}")

            if declined_count > 0:
                logger.info(
                    f"Declined {declined_count} PRs for branch {sanitize_for_log(branch_name)}"
                )
            else:
                logger.info(
                    f"No open PRs found for branch {sanitize_for_log(branch_name)}, nothing to decline"
                )

            return True
        except BitbucketApiError:
            # Unknown outcome - must not be swallowed into a "cleanup passed".
            raise
        except Exception as e:
            logger.error(f"Error declining PRs for branch '{sanitize_for_log(branch_name)}': {e}")
            return False

    def decline_all_branches_except(self, except_branches=None):
        except_branches = except_branches or ["reference", "main", "master"]
        logger.info(f"Declining all branches except {except_branches}...")

        branches = self.bitbucket.get_branches()
        deleted_count = 0

        for branch in branches.get("values", []):
            name = branch.get("displayId", branch.get("name"))
            if not name or name in except_branches:
                continue
            if branch.get("isDefault"):
                logger.info(f"Skipping default branch '{sanitize_for_log(name)}'")
                continue

            logger.info(f"Declining branch: {sanitize_for_log(name)}")

            prs = self.bitbucket.get_pull_requests(state="OPEN")
            pr_found = False

            for pr in prs.get("values", []):
                if source_branch(pr) == name and pr.get("id"):
                    pr_id = pr["id"]
                    logger.info(
                        f"Found PR #{pr_id} for branch {sanitize_for_log(name)}, declining it"
                    )
                    if self._decline_pr_and_delete_renovate_branch(pr_id, name):
                        deleted_count += 1
                        pr_found = True
                    else:
                        logger.warning(f"Failed to decline PR #{pr_id}")

            if not pr_found:
                logger.info(
                    f"No open PR found for branch {sanitize_for_log(name)}, deleting branch directly"
                )
                if is_renovate_branch(name):
                    if self.bitbucket.delete_branch_by_name(name):
                        deleted_count += 1
                        logger.info(
                            f"Successfully deleted renovate branch {sanitize_for_log(name)}"
                        )
                    else:
                        logger.warning(f"Failed to delete renovate branch {sanitize_for_log(name)}")
                elif self.decline_open_prs_for_branch(name):
                    deleted_count += 1

        logger.info(f"Branch cleanup completed. Declined/deleted {deleted_count} branches.")

        for pr in self.bitbucket.get_pull_requests(state="DECLINED").get("values", []):
            time.sleep(0.2)
            pr_id = pr.get("id")
            logger.info(f"Found declined PR {pr_id}")
            self.bitbucket.delete_pull_request(pr_id)

        return deleted_count

    def reset_branch_from_reference(self, target_branch, reference_branch):
        logger.info(f"Resetting {target_branch} branch from {reference_branch}...")

        ref_branch_info = self.bitbucket.get_branch_info(reference_branch)
        if not ref_branch_info:
            logger.error(f"Could not get info for {reference_branch} branch")

            fallback_branches = ["master", "main", "develop"]
            for fallback in fallback_branches:
                if fallback != reference_branch:
                    logger.info(f"Trying fallback branch: {fallback}")
                    fallback_info = self.bitbucket.get_branch_info(fallback)
                    if fallback_info:
                        logger.info(f"Using {fallback} as fallback reference branch")
                        reference_branch = fallback
                        ref_branch_info = fallback_info
                        break

            if not ref_branch_info:
                logger.error("Could not find any usable reference branch")
                return False

        ref_hash = ref_branch_info.get("latestCommit")
        if not ref_hash:
            logger.error(f"Could not get hash for {reference_branch} branch")
            return False

        merge_branch_name = f"merge-{reference_branch}-to-{target_branch}-{int(time.time())}"

        if not self.bitbucket.create_branch(merge_branch_name, ref_hash):
            logger.error(f"Failed to create merge branch {merge_branch_name}")
            return False

        logger.info(f"Created merge branch {merge_branch_name} from {reference_branch}")

        pr_result = self.bitbucket.create_pull_request(
            title=f"Reset {target_branch} to match {reference_branch}",
            source_branch=merge_branch_name,
            target_branch=target_branch,
            description="Automated branch reset",
        )

        if not pr_result or "id" not in pr_result:
            logger.error("Failed to create pull request")
            return False

        pr_id = pr_result["id"]
        logger.info(f"Created PR #{pr_id} to merge {merge_branch_name} into {target_branch}")

        if self.bitbucket.merge_pull_request(pr_id):
            logger.info(f"Successfully reset {target_branch} to match {reference_branch}")
            return True

        logger.error(f"Failed to merge PR #{pr_id}")
        logger.info("Attempting alternative reset approach...")
        return self._force_reset_branch(target_branch, ref_hash)

    def _force_reset_branch(self, target_branch, reference_commit_hash):
        logger.info(f"Using direct branch update method to reset {target_branch}...")  # nosec B608 - log message, not an SQL query

        try:
            try:
                if self.bitbucket.create_branch_forced(target_branch, reference_commit_hash):
                    logger.info(
                        f"Successfully reset {target_branch} to commit {reference_commit_hash[:7]}"
                    )
                    return True
            except Exception:
                logger.warning("Direct branch update failed, trying alternative approach")

            if not self.bitbucket.get_branch_info(target_branch):
                logger.error(f"Could not get info for {target_branch} branch")
                return False

            if not self.bitbucket.create_branch(target_branch, reference_commit_hash):
                logger.error(f"Failed to create new {target_branch} from {reference_commit_hash}")
                return False

            logger.info(f"Successfully reset {target_branch} to commit {reference_commit_hash[:7]}")
            return True

        except Exception as e:
            logger.error(f"Error in force reset: {e}")
            return False
