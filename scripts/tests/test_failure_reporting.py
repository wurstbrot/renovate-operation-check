"""Negative tests for the failure reporting hardened after the 2026-08-25 review.

Core property under test: an *unknown* outcome must never be reported as a
successful run. Before this change an unreachable or unauthorized Bitbucket
made every listing return an empty result, so the job finished green with
"Cleanup: OK - 0 PRs declined".
"""

import sys

import pytest
import requests

import scripts.main as main_module
from scripts.core.check_pr_manager import CheckPrManager
from scripts.core.cleanup_manager import CleanupManager
from scripts.exceptions import BitbucketApiError, ConfigurationError
from scripts.notifications.message_formatter import format_result
from scripts.notifications.notification_factory import NotificationFactory

from fake_bitbucket import (
    FakeBitbucketSession,
    FakeResponse,
    make_cleanup_config,
    make_client,
    make_session,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scripts.clients.bitbucket_server as bitbucket_server_module
    import scripts.core.cleanup_manager as cleanup_manager_module

    monkeypatch.setattr(bitbucket_server_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cleanup_manager_module.time, "sleep", lambda seconds: None)


class UnreachableSession(FakeBitbucketSession):
    """Every call fails at the transport layer."""

    def _handle(self, method, url, params=None, json=None, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")


class UnauthorizedSession(FakeBitbucketSession):
    """The server answers, but rejects the credentials."""

    def _handle(self, method, url, params=None, json=None, **kwargs):
        return FakeResponse(401, {"errors": [{"message": "unauthorized"}]})


def make_failing_client(session_class):
    session = make_session()
    session.__class__ = session_class
    return make_client(session)


# --- a failed run must not report success -------------------------------------


@pytest.mark.core
def test_unreachable_bitbucket_fails_the_default_cleanup_path():
    """The container entrypoint runs exactly this path without extra flags."""
    cleanup_manager = CleanupManager(
        make_failing_client(UnreachableSession), make_cleanup_config()
    )

    assert cleanup_manager.cleanup_declined_prs() is False


@pytest.mark.core
def test_rejected_auth_logs_status_code_and_url(caplog):
    """The log must name the HTTP status and target URL, otherwise a wrong
    PROJECT_KEY (404) is indistinguishable from a bad token (401)."""
    import logging

    client = make_failing_client(UnauthorizedSession)

    with caplog.at_level(logging.WARNING):
        assert client.verify_permissions() is False

    assert "HTTP 401" in caplog.text
    assert "/rest/api/1.0/projects/" in caplog.text


@pytest.mark.core
def test_unauthorized_bitbucket_fails_both_cleanup_paths():
    cleanup_manager = CleanupManager(
        make_failing_client(UnauthorizedSession), make_cleanup_config()
    )

    assert cleanup_manager.cleanup() is False
    assert cleanup_manager.cleanup_declined_prs() is False


@pytest.mark.core
def test_failed_pr_listing_raises_instead_of_reporting_zero_prs():
    client = make_failing_client(UnreachableSession)

    with pytest.raises(BitbucketApiError):
        client.get_pull_requests(state="OPEN")


@pytest.mark.core
def test_failed_branch_listing_raises_instead_of_reporting_zero_branches():
    client = make_failing_client(UnreachableSession)

    with pytest.raises(BitbucketApiError):
        client.get_branches()


@pytest.mark.core
def test_absent_branch_returns_none_but_a_failed_lookup_raises():
    """The two cases must stay distinguishable: 'not there' vs. 'do not know'."""
    session = make_session()
    client = make_client(session)

    assert client.get_branch_info("does-not-exist") is None

    session.__class__ = UnreachableSession
    with pytest.raises(BitbucketApiError):
        client.get_branch_info("master")


@pytest.mark.core
def test_pr_check_reports_a_fetch_failure_instead_of_no_prs():
    class ExplodingBitbucket:
        def get_renovate_prs(self):
            raise BitbucketApiError("connection refused")

    errors, summary = CheckPrManager(
        ExplodingBitbucket(), {"pr_checks": [{"name": "NPM", "titleRegex": "Update npm$"}]}
    ).check_renovate_prs()

    assert "Could not fetch Renovate PRs" in errors
    assert "No open Renovate PRs found" not in errors
    assert summary is None


# --- configuration mistakes must not run silently ------------------------------


@pytest.mark.core
def test_non_mapping_config_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    config_file.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        main_module.load_yaml_config(str(config_file))


@pytest.mark.core
def test_branch_reset_without_explicit_branches_is_refused():
    """No defaults: the reset force-rewrites the target branch."""
    cleanup_config = make_cleanup_config()
    cleanup_config["enable_branch_reset"] = True
    cleanup_manager = CleanupManager(make_client(make_session()), cleanup_config)

    with pytest.raises(ConfigurationError):
        cleanup_manager.cleanup()


@pytest.mark.core
def test_branch_reset_uses_the_configured_branches():
    cleanup_config = make_cleanup_config()
    cleanup_config.update(
        enable_branch_reset=True,
        branch_reset_target="master",
        branch_reset_source="reference",
    )
    cleanup_manager = CleanupManager(make_client(make_session()), cleanup_config)

    seen = {}
    cleanup_manager.reset_branch_from_reference = lambda target, source: seen.update(
        target=target, source=source
    ) or True

    assert cleanup_manager.cleanup() is True
    assert seen == {"target": "master", "source": "reference"}


# --- an aborted run still has to reach the operator ----------------------------


@pytest.mark.core
def test_aborted_run_sends_a_failure_notification_without_leaking_details(monkeypatch):
    sent = []

    def exploding_workflow_manager(args, config):
        raise ConfigurationError("Missing required Bitbucket connection parameters")

    def record(result, notifications_config, global_config=None):
        sent.append(result)
        return True

    monkeypatch.setattr(main_module, "WorkflowManager", exploding_workflow_manager)
    monkeypatch.setattr(main_module, "configure_logging", lambda args, config: None)
    monkeypatch.setattr(main_module, "load_yaml_config", lambda path: {})
    monkeypatch.setattr(main_module.NotificationFactory, "create_notification", record)
    monkeypatch.setattr(sys, "argv", ["main.py"])

    assert main_module.main() == 1

    assert len(sent) == 1
    assert sent[0]["success"] is False
    assert sent[0]["cleanup_passed"] is False
    assert "ConfigurationError" in sent[0]["pr_check_errors"]

    # the exception detail stays in the log, not in the chat message
    _, body = format_result(sent[0])
    assert "Missing required Bitbucket connection parameters" not in body


@pytest.mark.core
def test_incomplete_provider_config_does_not_crash_the_run():
    """Constructing a provider used to raise outside the guard and kill the run."""
    result = {"success": False, "project": "project", "repo": "repo"}

    # 'mattermost' without url: the constructor raises
    assert (
        NotificationFactory.create_notification(
            result, {"enabled": True, "mattermost": {"enabled": True}}
        )
        is False
    )

    # a provider entry that is not a mapping at all
    assert (
        NotificationFactory.create_notification(result, {"enabled": True, "bogus": "nope"})
        is False
    )
