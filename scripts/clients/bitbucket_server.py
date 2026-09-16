import requests
from requests.auth import HTTPBasicAuth
import logging
import time
import os

from jsonschema import validate, ValidationError

from scripts.exceptions import BitbucketApiError, ResponseValidationError
from scripts.renovate_rules import is_renovate_pr
from scripts.utils.logging_utils import sanitize_for_log
from scripts.utils.ssl_adapter import mount_if_enabled
from .schema_validator import validate_response_schema

logger = logging.getLogger("renovate-operation-check.clients-bitbucket_server")

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
MAX_RETRY_AFTER = 300


class BitbucketServerClient:

    def __init__(
        self,
        base_url,
        project_key,
        repo_slug,
        token=None,
        username="renovate",
        ssl_config=None,
        app_password=None,
    ):
        self.base_url = base_url
        self.project_key = project_key
        self.repo_slug = repo_slug
        self.token = token
        self.username = username
        self.app_password = app_password

        self.session = requests.Session()
        mount_if_enabled(
            self.session,
            bool(ssl_config and ssl_config.get("relax_x509_strict")),
            "BitbucketServerClient",
        )
        if ssl_config and ssl_config.get("ca_cert_path"):
            ca_path = ssl_config["ca_cert_path"]
            if os.path.exists(ca_path):
                logger.info(f"Using custom CA certificate from: {ca_path}")
                self.session.verify = ca_path
            else:
                logger.warning(f"Specified CA certificate path not found: {ca_path}")
                logger.warning("Falling back to default CA certificates")

        self.auth = HTTPBasicAuth(token or username, app_password or "")

        self.headers = {
            "Authorization": f"Bearer {token}" if token else "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        self.api_url = f"{base_url}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"

        self.successful_auth_method = None
        self.stats = {"prs_declined": 0, "prs_deleted": 0, "branches_deleted": 0}

    def _auth_kwargs(self):
        """Request kwargs for the verified auth method (or the defaults)."""
        method = self.successful_auth_method or {"auth": self.auth, "headers": self.headers}
        kwargs = {}
        if method.get("auth"):
            kwargs["auth"] = method["auth"]
        if method.get("headers"):
            kwargs["headers"] = method["headers"]
        return kwargs

    def _make_request(self, method, url, raise_for_status=True, **kwargs):
        """Single entry point for all API calls: verified auth, default
        timeout and central 429 retry handling."""
        if not self.successful_auth_method:
            if not self.verify_permissions():
                raise BitbucketApiError("Failed to authenticate with Bitbucket Server")

        auth_kwargs = self._auth_kwargs()
        if "auth" in auth_kwargs:
            kwargs.setdefault("auth", auth_kwargs["auth"])
        if "headers" in auth_kwargs:
            merged_headers = dict(auth_kwargs["headers"])
            merged_headers.update(kwargs.get("headers") or {})
            kwargs["headers"] = merged_headers

        kwargs.setdefault("timeout", DEFAULT_TIMEOUT)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Making {method} request to {url}")
            if kwargs.get("params"):
                logger.debug(f"Request params: {kwargs['params']}")
            body = kwargs.get("json")
            if isinstance(body, dict):
                safe_body = {
                    k: (v if k.lower() not in ["password", "token", "secret", "key"] else "***")
                    for k, v in body.items()
                }
                logger.debug(f"Request body: {safe_body}")

        try:
            for attempt in range(MAX_RETRIES + 1):
                response = self.session.request(method, url, **kwargs)

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Response status: {response.status_code}")
                    if len(response.text) > 0:
                        preview_length = 1000
                        text_preview = sanitize_for_log(response.text, max_length=preview_length)
                        logger.debug(f"Response body preview: {text_preview}")

                if response.status_code == 429 and attempt < MAX_RETRIES:
                    retry_after = self._get_retry_after(response)
                    logger.warning(
                        f"Rate limit hit for {method} {url}, sleeping {retry_after} seconds before retry (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(retry_after)
                    continue

                if raise_for_status:
                    response.raise_for_status()
                return response
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL Error: {e}")
            logger.error(
                "If the server uses an internal CA, provide its certificate via ca_cert_path in config.yaml"
            )
            raise BitbucketApiError(f"SSL Error when connecting to {url}: {str(e)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise BitbucketApiError(f"Failed to {method} {url}: {str(e)}")

    @staticmethod
    def _get_retry_after(response, default=30):
        try:
            return min(MAX_RETRY_AFTER, max(1, int(response.headers.get("Retry-After", default))))
        except (TypeError, ValueError):
            return default

    def get_pull_requests(self, state=None):
        url = f"{self.api_url}/pull-requests"
        params = {}

        if state:
            params["state"] = state

        try:
            response = self._make_request("GET", url, params=params)
            pull_requests = response.json()
            validate_response_schema(pull_requests, "pull_requests")

            logger.debug(f"Returning {len(pull_requests.get('values', []))} open Renovate PRs")

            return pull_requests
        except ResponseValidationError as e:
            logger.error(f"Pull requests response validation failed: {e}")
            raise BitbucketApiError(
                f"Invalid Bitbucket Server response format for pull requests: {e}"
            )
        except Exception as e:
            logger.error(f"Error getting pull requests: {e}")
            raise BitbucketApiError(f"Could not list pull requests (state={state}): {e}")

    def decline_pull_request(self, pr_id):
        """Decline a PR via the API.

        Pure client operation: whether the source branch is deleted afterwards
        is the cleanup layer's decision, not this method's.
        """
        url = f"{self.api_url}/pull-requests/{pr_id}/decline"
        logger.debug(f"Declining pull request #{pr_id} via API endpoint: {url}")

        pr_info = self.get_pull_request_by_id(pr_id)
        if not pr_info:
            logger.warning(f"Could not find PR #{pr_id} to decline")
            return False

        version = pr_info.get("version", 0)

        response = self._make_request(
            "POST", url, raise_for_status=False, json={"version": version}
        )

        if response.status_code == 200:
            logger.info(f"Successfully declined PR #{pr_id}")
            self.stats["prs_declined"] += 1
        elif response.status_code == 409:
            logger.info(f"PR #{pr_id} is already in terminal state (declined or merged)")
        elif response.status_code == 403:
            logger.warning(
                f"[SEC] Permission denied when declining PR #{pr_id} - insufficient privileges"
            )
            return False
        elif response.status_code == 404:
            logger.warning(f"PR #{pr_id} not found")
            return False
        else:
            logger.warning(f"Failed to decline PR #{pr_id}. Status code: {response.status_code}")
            return False

        return True

    def delete_pull_request(self, pr_id):
        """Delete a pull request completely from Bitbucket Server"""
        url = f"{self.api_url}/pull-requests/{pr_id}"
        logger.debug(f"Deleting pull request #{pr_id} via API endpoint: {url}")

        try:
            pr_info = self.get_pull_request_by_id(pr_id)
            if not pr_info:
                logger.warning(f"Could not find PR #{pr_id} to delete")
                return False

            version = pr_info.get("version", 0)
            logger.debug(f"Deleting PR #{pr_id} with version: {version}")

            response = self._make_request(
                "DELETE", url, raise_for_status=False, json={"version": version}
            )

            if response.status_code == 204:
                logger.info(f"Successfully deleted PR #{pr_id}")
                self.stats["prs_deleted"] += 1
                return True
            elif response.status_code == 403:
                logger.warning(
                    f"[SEC] Permission denied when deleting PR #{pr_id} - insufficient privileges"
                )
            elif response.status_code == 404:
                logger.warning(f"PR #{pr_id} not found")
            elif response.status_code == 409:
                logger.warning(f"PR #{pr_id} cannot be deleted in its current state")
            else:
                logger.error(f"Failed to delete PR #{pr_id}. Status code: {response.status_code}")
            return False

        except BitbucketApiError:
            # Unknown outcome - must not be swallowed into a "cleanup passed".
            raise
        except Exception as e:
            logger.error(f"Error deleting PR #{pr_id}: {str(e)}")
            return False

    def get_pull_request_by_id(self, pull_request_id):
        """Return the PR, or None if the server says it does not exist.

        Any other failure raises: a PR whose state could not be read must not
        be treated like a PR that is gone.
        """
        url = f"{self.api_url}/pull-requests/{pull_request_id}"
        response = self._make_request("GET", url, raise_for_status=False)

        if response.status_code == 404:
            logger.warning(f"PR #{pull_request_id} not found")
            return None
        if response.status_code != 200:
            raise BitbucketApiError(
                f"Could not read pull request {pull_request_id}: HTTP {response.status_code}"
            )

        try:
            pr_data = response.json()
            validate_response_schema(pr_data, "pull_request")
        except ResponseValidationError as e:
            logger.error(f"Pull request response validation failed: {e}")
            raise BitbucketApiError(
                f"Invalid Bitbucket Server response format for pull request {pull_request_id}: {e}"
            )
        except ValueError as e:
            logger.error(f"Pull request response was not valid JSON: {e}")
            raise BitbucketApiError(
                f"Invalid Bitbucket Server response for pull request {pull_request_id}: {e}"
            )

        return pr_data

    def get_branches(self):
        url = f"{self.api_url}/branches"
        try:
            response = self._make_request("GET", url)
            branches_data = response.json()
            validate_response_schema(branches_data, "branches")

            return branches_data
        except ResponseValidationError as e:
            logger.error(f"Branches response validation failed: {e}")
            raise BitbucketApiError(f"Invalid Bitbucket Server response format for branches: {e}")
        except Exception as e:
            # See get_pull_requests: a failed listing must not read as "no branches".
            logger.error(f"Error getting branches: {e}")
            raise BitbucketApiError(f"Could not list branches: {e}")

    def get_branch_info(self, branch_name):
        url = f"{self.api_url}/branches"
        params = {"filterText": branch_name}

        try:
            response = self._make_request("GET", url, params=params)
            branches = response.json()
            validate_response_schema(branches, "branches")

            for branch in branches.get("values", []):
                if branch.get("displayId", branch.get("name")) == branch_name:
                    validate_response_schema(branch, "branch")
                    return branch
            return None
        except ResponseValidationError as e:
            logger.error(f"Branch info response validation failed: {e}")
            raise BitbucketApiError(
                f"Invalid Bitbucket Server response format for branch info '{sanitize_for_log(branch_name)}': {e}"
            )
        except Exception as e:
            logger.error(f"Error getting branch info for '{sanitize_for_log(branch_name)}': {e}")
            raise BitbucketApiError(
                f"Could not look up branch '{sanitize_for_log(branch_name)}': {e}"
            )

    def create_branch(self, branch_name, start_point):
        url = f"{self.api_url}/branches"

        data = {"name": branch_name, "startPoint": start_point}

        try:
            response = self._make_request("POST", url, json=data)
            if response.status_code in [200, 201]:
                logger.info(
                    f"Branch '{sanitize_for_log(branch_name)}' created successfully from '{sanitize_for_log(start_point)}'"
                )
                return True
            logger.error(
                f"Failed to create branch '{sanitize_for_log(branch_name)}'. Status code: {response.status_code}"
            )
            return False
        except Exception as e:
            logger.error(
                f"Error creating branch '{sanitize_for_log(branch_name)}' from '{sanitize_for_log(start_point)}': {e}"
            )
            return False

    def create_branch_forced(self, branch_name, start_point):
        """Force-move a branch to a commit (used only by the branch reset)."""
        url = f"{self.api_url}/branches"
        payload = {"name": branch_name, "startPoint": start_point, "force": True}

        response = self._make_request("POST", url, json=payload)
        return response.status_code in [200, 201, 204]

    def create_pull_request(self, title, source_branch, target_branch, description=None):
        url = f"{self.api_url}/pull-requests"

        data = {
            "title": title,
            "state": "OPEN",
            "fromRef": {
                "id": f"refs/heads/{source_branch}",
                "repository": {"slug": self.repo_slug, "project": {"key": self.project_key}},
            },
            "toRef": {
                "id": f"refs/heads/{target_branch}",
                "repository": {"slug": self.repo_slug, "project": {"key": self.project_key}},
            },
        }

        if description:
            data["description"] = description

        try:
            response = self._make_request("POST", url, json=data)
            pr_data = response.json()
            validate_response_schema(pr_data, "pull_request")

            return pr_data
        except ResponseValidationError as e:
            logger.error(f"Create pull request response validation failed: {e}")
            raise Exception(
                f"Invalid Bitbucket Server response format for create pull request: {e}"
            )
        except Exception as e:
            logger.error(f"Error creating pull request: {e}")
            return None

    def merge_pull_request(self, pull_request_id):
        url = f"{self.api_url}/pull-requests/{pull_request_id}/merge"

        try:
            pr_info = self.get_pull_request_by_id(pull_request_id)
            if not pr_info or "version" not in pr_info:
                logger.error(f"Could not get PR #{pull_request_id} version information")
                return False

            data = {"version": pr_info["version"]}

            retry_delay = 2
            for attempt in range(MAX_RETRIES):
                try:
                    response = self._make_request("POST", url, raise_for_status=False, json=data)
                    if response.status_code in [200, 201]:
                        logger.info(f"Successfully merged PR #{pull_request_id}")
                        return True

                    logger.warning(f"Merge response code: {response.status_code}")
                    if response.status_code == 400 and self._merge_conflict_reported(response):
                        logger.info("Merge conflicts detected, cannot continue with this approach")
                        return False
                except Exception as e:
                    logger.warning(f"Merge attempt {attempt + 1} failed: {e}")

                if attempt < MAX_RETRIES - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2

            logger.error(f"Failed to merge PR #{pull_request_id} after {MAX_RETRIES} attempts")
            return False
        except Exception as e:
            logger.error(f"Error merging pull request: {e}")
            return False

    @staticmethod
    def _merge_conflict_reported(response):
        error_schema = {
            "type": "object",
            "properties": {
                "errors": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"message": {"type": "string"}}},
                },
                "error": {"type": "object", "properties": {"message": {"type": "string"}}},
            },
        }
        try:
            error_data = response.json()
            validate(instance=error_data, schema=error_schema)
        except (ValidationError, ValueError):
            return False

        error_message = ""
        if error_data.get("errors"):
            error_message = error_data["errors"][0].get("message", "")
        elif "error" in error_data:
            error_message = error_data.get("error", {}).get("message", "")

        if "conflicts" in error_message.lower():
            logger.warning(f"Merge conflicts detected: {sanitize_for_log(error_message)}")
            return True
        return False

    def verify_permissions(self):
        logger.info("Verifying Bitbucket Server permissions...")

        try:
            url = self.api_url
            auth_methods = [
                {"method": "HTTP Basic Auth", "auth": self.auth},
                {"method": "Bearer Token", "headers": self.headers},
            ]

            for auth_method in auth_methods:
                try:
                    kwargs = {}
                    if "auth" in auth_method:
                        kwargs["auth"] = auth_method["auth"]
                    if "headers" in auth_method:
                        kwargs["headers"] = auth_method["headers"]

                    logger.info(f"Testing {auth_method['method']} authentication")

                    response = self.session.get(url, timeout=10, **kwargs)

                    if response.status_code == 200:
                        logger.info(f"{auth_method['method']} authentication successful")
                        self.successful_auth_method = auth_method

                        branches_url = f"{self.api_url}/branches"
                        branch_response = self.session.get(branches_url, timeout=10, **kwargs)
                        branch_response.raise_for_status()

                        logger.info("Basic repository access verified")
                        return True

                    logger.warning(
                        f"[SEC] {auth_method['method']} rejected: "
                        f"HTTP {response.status_code} for {url} - "
                        f"{sanitize_for_log(response.text, max_length=200)}"
                    )
                except Exception as e:
                    logger.warning(f"[SEC] {auth_method['method']} authentication failed: {str(e)}")

            logger.error(f"[SEC] All authentication methods failed for {self.api_url}")
            self.successful_auth_method = None
            return False
        except Exception as e:
            logger.error(f"Permission verification failed: {e}")
            logger.error("Make sure your access token has appropriate permissions")
            return False

    def delete_branch_by_name(self, branch_name):
        logger.info(f"Attempting to delete branch: {sanitize_for_log(branch_name)}")

        try:
            branch_info = self.get_branch_info(branch_name)
            if not branch_info:
                logger.warning(
                    f"Branch '{sanitize_for_log(branch_name)}' not found, nothing to delete"
                )
                return True

            url = f"{self.base_url}/rest/branch-utils/1.0/projects/{self.project_key}/repos/{self.repo_slug}/branches"

            data = {"name": branch_name, "dryRun": False}

            response = self._make_request("DELETE", url, raise_for_status=False, json=data)

            if response.status_code == 204:
                logger.info(f"Successfully deleted branch '{sanitize_for_log(branch_name)}'")
                self.stats["branches_deleted"] += 1
                return True
            elif response.status_code == 403:
                logger.warning(
                    f"[SEC] Permission denied when deleting branch '{sanitize_for_log(branch_name)}' - insufficient privileges"
                )
                return False
            elif response.status_code == 404:
                logger.warning(f"Branch '{sanitize_for_log(branch_name)}' not found on server")
                return True

            response.raise_for_status()
            return True

        except BitbucketApiError:
            # Unknown outcome - must not be swallowed into a "cleanup passed".
            raise
        except Exception as e:
            logger.error(f"Error deleting branch '{sanitize_for_log(branch_name)}': {str(e)}")
            return False

    def get_renovate_prs(self):
        logger.info("Fetching Renovate PRs...")

        prs = self.get_pull_requests(state="OPEN")

        renovate_prs = [pr for pr in prs.get("values", []) if is_renovate_pr(pr)]

        logger.info(f"Found {len(renovate_prs)} open Renovate PRs")
        return renovate_prs
