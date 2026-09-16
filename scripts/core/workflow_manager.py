import logging
from scripts.core.cleanup_manager import CleanupManager
from scripts.utils.logging_utils import log_exception
from scripts.clients.bitbucket_server import BitbucketServerClient
from scripts.core.check_pr_manager import CheckPrManager
from scripts.exceptions import ConfigurationError, BitbucketAuthError

logger = logging.getLogger("renovate-operation-check.core.workflow_manager")


class WorkflowManager:
    def __init__(self, args=None, config=None):
        self.args = args
        self.config = config
        self.pr_check_manager = None
        logger.info("Initializing WorkflowManager")

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Initializing with configuration:")
            if not self.config:
                logger.debug("  No configuration provided")

            if self.args:
                logger.debug("Command-line arguments:")
                for arg in vars(self.args):
                    logger.debug(f"  {arg}: {getattr(self.args, arg)}")
        self.bitbucket = self._create_bitbucket_client()
        try:
            self.pr_check_manager = CheckPrManager(self.bitbucket, config)
            logger.info("Successfully initialized Renovate Operation Check with CheckPrManager")
        except Exception as e:
            log_exception(logger, "Failed to initialize Renovate Operation Check", e)

    def _arg_enabled(self, name):
        value = getattr(self.args, name, None) if self.args else None
        return value is not None and value.lower() == "true"

    def _create_bitbucket_client(self):
        """Create and configure Bitbucket client"""
        bb_config = self.config.get("client", {}) if self.config else {}

        base_url = bb_config.get("base_url")
        project_key = bb_config.get("project_key")
        repo_slug = bb_config.get("repo_slug")
        token = bb_config.get("token")
        username = bb_config.get("username", "renovate")

        if not all([base_url, project_key, repo_slug]):
            raise ConfigurationError(
                "Missing required Bitbucket connection parameters. "
                "Please provide base_url, project_key, and repo_slug in config.yaml"
            )

        if not token:
            raise BitbucketAuthError(
                "No authentication token provided. "
                "Set the RENOVATE_TOKEN environment variable (substituted into "
                "client.token in config.yaml) or configure client.token directly"
            )

        ca_cert_path = bb_config.get("ca_cert_path", self.config.get("ca_cert_path", ""))
        relax_x509_strict = bb_config.get(
            "relax_x509_strict", self.config.get("relax_x509_strict", False)
        )
        ssl_config = {"ca_cert_path": ca_cert_path, "relax_x509_strict": relax_x509_strict}

        logger.info(f"Connecting to Bitbucket server at {base_url}")

        return BitbucketServerClient(
            base_url=base_url,
            project_key=project_key,
            repo_slug=repo_slug,
            token=token,
            username=username,
            ssl_config=ssl_config,
        )

    def check_prs(self):
        """Check Renovate PRs for compliance.

        Returns (error_message, summary): error_message is "" on success,
        summary is None when the checks were skipped or never ran.
        """
        if not self.bitbucket or not self.pr_check_manager:
            logger.error("Failed to initiate bitbucket or pr check manager")
            return "Failed to initiate bitbucket or pr check manager", None

        if not self._arg_enabled("enable_pr_check"):
            logger.info("PR checks are disabled. Skipping Renovate PR checks.")
            return "", None

        logger.info("Checking Renovate PRs...")
        pr_check_errors, check_summary = self.pr_check_manager.check_renovate_prs()
        check_status = "SUCCESS" if pr_check_errors == "" else "FAILURE"
        logger.info(f"PR check status: {check_status}")
        return pr_check_errors, check_summary

    def cleanup_prs(self):
        """Clean up PRs and branches"""
        if not self.bitbucket:
            logger.error("Failed to initiate bitbucket client")
            return False

        cleanup_manager = CleanupManager(self.bitbucket, self.config.get("cleanup"))
        if self._arg_enabled("enable_pr_cleanup"):
            logger.info("Running in PR cleanup mode: Will clean up PRs and branches")
            cleanup_passed = cleanup_manager.cleanup()
            logger.info(f"cleanup_passed: {cleanup_passed}")
            return cleanup_passed

        logger.info("PR cleanup is disabled. Skipping PR cleanup. Deleting declined PRs")
        return cleanup_manager.cleanup_declined_prs()
