"""In-memory stand-in for the Bitbucket Server REST endpoints the client uses.

Shared test infrastructure - imported by all test modules that drive the real
client/manager code against a fake server.
"""

import copy
import json as jsonlib

import requests

from scripts.clients.bitbucket_server import BitbucketServerClient

BASE_URL = "https://bitbucket.example.test"
PROJECT_KEY = "project"
REPO_SLUG = "project-renovate-test-content"
API_URL = f"{BASE_URL}/rest/api/1.0/projects/{PROJECT_KEY}/repos/{REPO_SLUG}"
BRANCH_UTILS_URL = (
    f"{BASE_URL}/rest/branch-utils/1.0/projects/{PROJECT_KEY}/repos/{REPO_SLUG}/branches"
)

OPEN_PR_ID = 101
OPEN_PR_BRANCH = "renovate/npm-axios-vulnerability"
DECLINED_PR_ID = 202
DECLINED_PR_BRANCH = "renovate/maven-log4j"


def make_pr(pr_id, title, state, source_branch):
    return {
        "id": pr_id,
        "version": 1,
        "title": title,
        "state": state,
        "open": state == "OPEN",
        "createdDate": 1751900000000,
        "fromRef": {
            "id": f"refs/heads/{source_branch}",
            "displayId": source_branch,
            "repository": {"slug": REPO_SLUG, "project": {"key": PROJECT_KEY}},
        },
        "toRef": {
            "id": "refs/heads/master",
            "displayId": "master",
            "repository": {"slug": REPO_SLUG, "project": {"key": PROJECT_KEY}},
        },
        "author": {"user": {"name": "renovate"}},
    }


def make_branch(name, is_default=False):
    return {
        "id": f"refs/heads/{name}",
        "displayId": name,
        "isDefault": is_default,
        "latestCommit": "0123456789abcdef0123456789abcdef01234567",
    }


class FakeResponse:
    def __init__(self, status_code, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}
        self.headers = headers or {}
        self.text = jsonlib.dumps(self._json_body)

    def json(self):
        return copy.deepcopy(self._json_body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} Error for url", response=self)


class FakeBitbucketSession:
    """In-memory stand-in for the Bitbucket Server REST endpoints the client uses."""

    def __init__(self, prs, branches):
        self.prs = {pr["id"]: copy.deepcopy(pr) for pr in prs}
        self.branches = {branch["displayId"]: copy.deepcopy(branch) for branch in branches}
        self.declined_pr_ids = []
        self.deleted_pr_ids = []
        self.deleted_branch_names = []
        self.merged_pr_ids = []
        self.created_branch_names = []
        # when True, every merge attempt answers HTTP 400 with a conflict message
        self.merge_conflict = False
        # substring of a request signature -> how many requests answer with HTTP 429
        self.rate_limited = {}
        self.requests_seen = []

    # --- requests.Session interface ---------------------------------------
    def request(self, method, url, **kwargs):
        return self._handle(method.upper(), url, **kwargs)

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._handle("DELETE", url, **kwargs)

    def mount(self, prefix, adapter):
        pass

    # --- fake server --------------------------------------------------------
    def _handle(self, method, url, params=None, json=None, **kwargs):
        params = params or {}
        signature = f"{method} {url} {sorted(params.items())}"
        self.requests_seen.append(signature)

        for matcher, remaining in self.rate_limited.items():
            if remaining > 0 and matcher in signature:
                self.rate_limited[matcher] = remaining - 1
                return FakeResponse(
                    429,
                    {"errors": [{"message": "Rate limit exceeded"}]},
                    headers={"Retry-After": "1"},
                )

        if method == "GET" and url == API_URL:
            return FakeResponse(200, {"slug": REPO_SLUG})

        if method == "GET" and url == f"{API_URL}/pull-requests":
            state = params.get("state")
            values = [pr for pr in self.prs.values() if not state or pr["state"] == state]
            return FakeResponse(200, {"values": values})

        if (
            method == "POST"
            and url.startswith(f"{API_URL}/pull-requests/")
            and url.endswith("/decline")
        ):
            pr_id = int(url.split("/")[-2])
            pr = self.prs.get(pr_id)
            if pr is None:
                return FakeResponse(404, {"errors": [{"message": "PR not found"}]})
            if pr["state"] != "OPEN":
                return FakeResponse(409, {"errors": [{"message": "Already in a terminal state"}]})
            pr["state"] = "DECLINED"
            pr["open"] = False
            pr["version"] += 1
            self.declined_pr_ids.append(pr_id)
            return FakeResponse(200, pr)

        if (
            method == "POST"
            and url.startswith(f"{API_URL}/pull-requests/")
            and url.endswith("/merge")
        ):
            pr_id = int(url.split("/")[-2])
            pr = self.prs.get(pr_id)
            if pr is None:
                return FakeResponse(404, {"errors": [{"message": "PR not found"}]})
            if self.merge_conflict:
                return FakeResponse(
                    400, {"errors": [{"message": "The pull request has conflicts"}]}
                )
            pr["state"] = "MERGED"
            pr["version"] += 1
            source = pr["fromRef"]["displayId"]
            target = pr["toRef"]["displayId"]
            if source in self.branches and target in self.branches:
                self.branches[target]["latestCommit"] = self.branches[source]["latestCommit"]
            self.merged_pr_ids.append(pr_id)
            return FakeResponse(200, pr)

        if method == "POST" and url == f"{API_URL}/pull-requests":
            body = json or {}
            new_id = max(self.prs, default=300) + 1
            source = body["fromRef"]["id"].split("refs/heads/")[-1]
            target = body["toRef"]["id"].split("refs/heads/")[-1]
            pr = make_pr(new_id, body.get("title", ""), "OPEN", source)
            pr["toRef"]["id"] = f"refs/heads/{target}"
            pr["toRef"]["displayId"] = target
            self.prs[new_id] = pr
            return FakeResponse(201, pr)

        if method == "POST" and url == f"{API_URL}/branches":
            body = json or {}
            name = body.get("name")
            if name in self.branches and not body.get("force"):
                return FakeResponse(409, {"errors": [{"message": "Branch already exists"}]})
            branch = self.branches.get(name) or make_branch(name)
            branch["latestCommit"] = body.get("startPoint")
            self.branches[name] = branch
            self.created_branch_names.append(name)
            return FakeResponse(200, branch)

        if url.startswith(f"{API_URL}/pull-requests/"):
            pr_id = int(url.rstrip("/").split("/")[-1])
            pr = self.prs.get(pr_id)
            if pr is None:
                return FakeResponse(404, {"errors": [{"message": "PR not found"}]})
            if method == "GET":
                return FakeResponse(200, pr)
            if method == "DELETE":
                del self.prs[pr_id]
                self.deleted_pr_ids.append(pr_id)
                return FakeResponse(204)

        if method == "GET" and url == f"{API_URL}/branches":
            filter_text = params.get("filterText", "")
            values = [branch for name, branch in self.branches.items() if filter_text in name]
            return FakeResponse(200, {"values": values})

        if method == "DELETE" and url == BRANCH_UTILS_URL:
            branch_name = (json or {}).get("name")
            if branch_name in self.branches:
                del self.branches[branch_name]
                self.deleted_branch_names.append(branch_name)
                return FakeResponse(204)
            return FakeResponse(404, {"errors": [{"message": "Branch not found"}]})

        return FakeResponse(404, {"errors": [{"message": f"Unhandled request: {signature}"}]})


def make_cleanup_config():
    # Mirrors the 'cleanup' section of scripts/config/config.yaml - YAML delivers
    # real booleans here, not the strings the command line produces.
    return {
        "enable_branch_reset": False,
        "enable_pr_cleanup": True,
        "delete_branches_after_pr_decline": True,
        "decline_all_in_pr_cleanup": True,
        "exclude_branches": ["org", "reference", "main", "master"],
    }


def make_session(extra_branches=()):
    open_pr = make_pr(
        OPEN_PR_ID, "Update dependency axios to v1.13.5 [SECURITY]", "OPEN", OPEN_PR_BRANCH
    )
    declined_pr = make_pr(
        DECLINED_PR_ID,
        "Update dependency org.apache.logging.log4j:log4j-api to v2.25.3",
        "DECLINED",
        DECLINED_PR_BRANCH,
    )
    branches = [
        make_branch("master", is_default=True),
        make_branch(OPEN_PR_BRANCH),
        make_branch(DECLINED_PR_BRANCH),
    ]
    branches.extend(make_branch(name) for name in extra_branches)
    return FakeBitbucketSession([open_pr, declined_pr], branches)


def make_client(session):
    client = BitbucketServerClient(
        base_url=BASE_URL,
        project_key=PROJECT_KEY,
        repo_slug=REPO_SLUG,
        token="dummy-token",  # nosec B106 - synthetic test value, no real credential
    )
    client.session = session
    return client
