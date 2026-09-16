"""Regression tests: a declined renovate PR must be deleted by the cleanup run.

Scenario (see config.yaml): the repository contains one OPEN and one DECLINED
renovate PR. Running the cleanup ("--enable-pr-cleanup=true") must remove the
declined PR from Bitbucket, not just decline PRs or delete branches.
"""

import sys

import pytest

import scripts.main as main_module
import scripts.clients.bitbucket_server as bitbucket_server_module
import scripts.core.cleanup_manager as cleanup_manager_module
from scripts.core.cleanup_manager import CleanupManager

from fake_bitbucket import (
    DECLINED_PR_BRANCH,
    DECLINED_PR_ID,
    OPEN_PR_BRANCH,
    OPEN_PR_ID,
    make_cleanup_config,
    make_client,
    make_session,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(bitbucket_server_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cleanup_manager_module.time, "sleep", lambda seconds: None)


@pytest.mark.core
def test_cleanup_deletes_declined_pr_when_open_and_declined_prs_exist():
    """One OPEN and one DECLINED renovate PR: the cleanup run must delete the declined PR."""
    session = make_session()
    client = make_client(session)
    # wired exactly like WorkflowManager.cleanup_prs does it
    cleanup_manager = CleanupManager(client, make_cleanup_config())

    assert cleanup_manager.cleanup() is True

    assert DECLINED_PR_ID in session.deleted_pr_ids
    assert DECLINED_PR_ID not in session.prs
    assert DECLINED_PR_BRANCH not in session.branches

    # the open renovate PR is declined first and then deleted as well
    assert OPEN_PR_ID in session.declined_pr_ids
    assert OPEN_PR_ID in session.deleted_pr_ids
    assert OPEN_PR_BRANCH not in session.branches

    # the default branch survives
    assert "master" in session.branches


@pytest.mark.core
def test_declined_pr_is_deleted_even_when_bitbucket_rate_limits_the_pr_list():
    """A 429 on the DECLINED PR listing must not make the cleanup silently skip the PR."""
    session = make_session()
    session.rate_limited["('state', 'DECLINED')"] = 1
    client = make_client(session)
    cleanup_manager = CleanupManager(client, make_cleanup_config())

    assert cleanup_manager.cleanup() is True
    assert DECLINED_PR_ID in session.deleted_pr_ids
    assert DECLINED_PR_ID not in session.prs


@pytest.mark.core
def test_default_cleanup_path_deletes_declined_pr_and_reports_success():
    """Without --enable-pr-cleanup=true the script only deletes declined PRs; the
    result must be truthy, otherwise main.py reports the whole run as failed."""
    session = make_session()
    client = make_client(session)
    cleanup_manager = CleanupManager(client, make_cleanup_config())

    cleanup_succeeded = cleanup_manager.cleanup_declined_prs()

    assert DECLINED_PR_ID in session.deleted_pr_ids
    assert DECLINED_PR_ID not in session.prs
    # the open PR stays untouched on this path
    assert OPEN_PR_ID in session.prs
    assert cleanup_succeeded


@pytest.mark.core
def test_enable_pr_cleanup_true_from_config_yaml_activates_branch_cleanup():
    """enable_pr_cleanup: true (a YAML boolean) must activate the renovate branch
    cleanup - it used to be compared against the string 'true' and never matched."""
    session = make_session(extra_branches=("renovate/docker-stale",))
    client = make_client(session)
    cleanup_manager = CleanupManager(client, make_cleanup_config())

    assert cleanup_manager.cleanup() is True
    assert "renovate/docker-stale" not in session.branches


class FakeConfig:
    """Minimal config stand-in for driving main.main()."""

    def get(self, key, default=None):
        return default


class RecordingWorkflowManager:
    """Records whether cleanup ran; check_prs always reports a failed check."""

    instances = []

    def __init__(self, args, config):
        self.args = args
        self.config = config
        self.cleanup_called = False
        RecordingWorkflowManager.instances.append(self)

    def check_prs(self):
        # A non-empty string is how check_prs signals a failed check, e.g. the
        # 'Update deps-ednNICHT_VORHANDNE' definition that never matches a PR.
        return "\nCheck definition 'Clojure' has no matching PRs", None

    def cleanup_prs(self):
        self.cleanup_called = True
        return True


@pytest.mark.core
def test_cleanup_runs_even_when_pr_check_fails(monkeypatch):
    """A failing PR check must not skip cleanup, otherwise declined PRs are never
    deleted when --enable-pr-check=true and a check definition has no match."""
    RecordingWorkflowManager.instances = []
    monkeypatch.setattr(main_module, "WorkflowManager", RecordingWorkflowManager)
    monkeypatch.setattr(main_module, "configure_logging", lambda args, config: None)
    monkeypatch.setattr(main_module, "load_yaml_config", lambda path: FakeConfig())
    monkeypatch.setattr(main_module, "log_effective_config", lambda config: None)
    monkeypatch.setattr(
        main_module.NotificationFactory, "create_notification", lambda *a, **k: True
    )
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--enable-pr-check=true", "--enable-pr-cleanup=true"]
    )

    return_code = main_module.main()

    wm = RecordingWorkflowManager.instances[0]
    assert wm.cleanup_called is True
    # the failed check still fails the whole run
    assert return_code == 1


@pytest.mark.core
def test_decline_all_branches_except_respects_exclude_list():
    session = make_session(extra_branches=("renovate/excluded", "renovate/doomed"))
    cleanup_manager = CleanupManager(make_client(session), make_cleanup_config())

    cleanup_manager.decline_all_branches_except(
        ["master", OPEN_PR_BRANCH, DECLINED_PR_BRANCH, "renovate/excluded"]
    )

    assert "renovate/excluded" in session.branches
    assert "renovate/doomed" not in session.branches


@pytest.mark.core
def test_cleanup_wires_exclude_branches_from_config(monkeypatch):
    """Regression: cleanup.exclude_branches from config.yaml was silently ignored."""
    session = make_session()
    cleanup_manager = CleanupManager(make_client(session), make_cleanup_config())
    received = {}
    monkeypatch.setattr(
        cleanup_manager,
        "decline_all_branches_except",
        lambda except_branches=None: received.setdefault("except_branches", except_branches),
    )

    assert cleanup_manager.cleanup() is True
    assert received["except_branches"] == ["org", "reference", "main", "master"]
