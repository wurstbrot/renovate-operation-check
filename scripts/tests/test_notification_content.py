"""Tests for the notification content: context, check summary, cleanup
counters, mention neutralization and length capping."""

from datetime import datetime

import pytest

from scripts.notifications.message_formatter import (
    MAX_BODY_LENGTH,
    format_result,
    sanitize_for_message,
)
from scripts.notifications.mattermost_webhook import MattermostWebhook
from scripts.notifications.mattermost import Mattermost
import scripts.notifications.notification_factory as notification_factory_module
from scripts.notifications.notification_factory import NotificationFactory
from scripts.core.cleanup_manager import CleanupManager

from fake_bitbucket import (
    DECLINED_PR_ID,
    OPEN_PR_ID,
    make_cleanup_config,
    make_client,
    make_session,
)
from test_security_negative import _RecordingHttpSession


def make_result(**overrides):
    result = {
        "success": False,
        "project": "project",
        "repo": "project-renovate-test-content",
        "base_url": "https://git.local",
        "pr_check_errors": "",
        "check_summary": {
            "open_pr_count": 5,
            "check_results": [
                {"check_name": "NuGet", "passed": True, "match_count": 2},
                {"check_name": "Clojure", "passed": False, "match_count": 0},
            ],
        },
        "cleanup_passed": True,
        "cleanup_stats": {"prs_declined": 3, "prs_deleted": 2, "branches_deleted": 4},
        "finished_at": datetime(2026, 8, 25, 12, 0, 0).astimezone(),
        "duration_seconds": 42.3,
        "run_url": "https://ci.example/job/17",
    }
    result.update(overrides)
    return result


@pytest.mark.core
def test_message_contains_context_checks_and_cleanup_counters():
    title, body = format_result(make_result())

    assert "project/project-renovate-test-content" in title
    assert title.startswith("[FAILED]")
    assert "https://git.local" in body
    assert "5 open Renovate PRs" in body
    assert "1/2 passed" in body
    assert "FAILED Clojure: no matching PR" in body
    assert "Passed: NuGet (2)" in body
    assert "3 PRs declined | 2 PRs deleted | 4 branches deleted" in body
    assert "(42s)" in body
    assert "https://ci.example/job/17" in body


@pytest.mark.core
def test_success_message_and_skipped_checks():
    title, body = format_result(make_result(success=True, check_summary=None, pr_check_errors=""))

    assert title.startswith("[OK]")
    assert "PR checks: skipped" in body


@pytest.mark.core
def test_mentions_and_control_characters_are_neutralized():
    result = make_result(
        check_summary=None,
        pr_check_errors="@channel @all @here evil\ntitle",
    )
    _, body = format_result(result)

    assert "@channel" not in body
    assert "@all" not in body
    assert "@here" not in body
    assert "evil title" in body  # newline replaced, text preserved

    assert "@channel" not in sanitize_for_message("hi @ChAnNeL")


@pytest.mark.core
def test_body_length_is_capped():
    checks = [
        {"check_name": f"check-{i}-{'x' * 200}", "passed": False, "match_count": 0}
        for i in range(200)
    ]
    _, body = format_result(
        make_result(check_summary={"open_pr_count": 1, "check_results": checks})
    )

    assert len(body) <= MAX_BODY_LENGTH + 100
    assert body.endswith("...[truncated]")


@pytest.mark.core
def test_webhook_sends_colored_attachment():
    webhook = MattermostWebhook(
        {"url": "https://mattermost.example.test", "webhook_key": "k", "enabled": True}
    )
    webhook.session = _RecordingHttpSession()

    assert webhook.send_message("Title", "Body", is_success=True) is True

    ((_, kwargs),) = webhook.session.calls
    attachment = kwargs["json"]["attachments"][0]
    assert attachment["title"] == "Title"
    assert attachment["text"] == "Body"
    assert attachment["color"] == "#2fa44f"


@pytest.mark.core
def test_cleanup_counters_match_fake_server_state(monkeypatch):
    import scripts.clients.bitbucket_server as bitbucket_server_module
    import scripts.core.cleanup_manager as cleanup_manager_module

    monkeypatch.setattr(bitbucket_server_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cleanup_manager_module.time, "sleep", lambda seconds: None)
    session = make_session()
    client = make_client(session)
    cleanup_manager = CleanupManager(client, make_cleanup_config())

    assert cleanup_manager.cleanup() is True

    assert client.stats["prs_deleted"] == len(session.deleted_pr_ids)
    assert client.stats["branches_deleted"] == len(session.deleted_branch_names)
    assert client.stats["prs_declined"] == len(session.declined_pr_ids)
    assert OPEN_PR_ID in session.deleted_pr_ids
    assert DECLINED_PR_ID in session.deleted_pr_ids


# --- NotificationFactory behavior ---------------------------------------------


class StubProviderClient:
    def __init__(self, config, success_msg=True):
        self.config = config
        self.success_msg = success_msg
        self.sent = []

    def should_send_success_message(self):
        return self.success_msg

    def send_message(self, title, body, is_success=None):
        self.sent.append((title, body, is_success))
        return True


@pytest.mark.core
def test_factory_disabled_config_sends_nothing_and_succeeds():
    assert NotificationFactory.create_notification(make_result(), None) is True
    assert NotificationFactory.create_notification(make_result(), {"enabled": False}) is True


@pytest.mark.core
def test_factory_unknown_provider_reports_failure():
    config = {"enabled": True, "carrier-pigeon": {"enabled": True}}
    assert NotificationFactory.create_notification(make_result(), config) is False


@pytest.mark.core
def test_factory_success_msg_false_skips_success_but_not_failure(monkeypatch):
    created = []

    def build(config):
        client = StubProviderClient(config, success_msg=False)
        created.append(client)
        return client

    monkeypatch.setitem(notification_factory_module.PROVIDERS, "stub", build)
    config = {"enabled": True, "stub": {"enabled": True}}

    assert NotificationFactory.create_notification(make_result(success=True), config) is True
    assert created[-1].sent == []

    assert NotificationFactory.create_notification(make_result(success=False), config) is True
    assert created[-1].sent and created[-1].sent[0][2] is False


@pytest.mark.core
def test_factory_propagates_global_tls_settings(monkeypatch):
    created = []

    def build(config):
        client = StubProviderClient(config)
        created.append(client)
        return client

    monkeypatch.setitem(notification_factory_module.PROVIDERS, "stub", build)
    config = {"enabled": True, "stub": {"enabled": True}}

    NotificationFactory.create_notification(
        make_result(),
        config,
        global_config={"ca_cert_path": "/etc/ssl/internal-ca.pem", "relax_x509_strict": True},
    )

    assert created[0].config["ca_cert_path"] == "/etc/ssl/internal-ca.pem"
    assert created[0].config["relax_x509_strict"] is True


# --- Mattermost API client -----------------------------------------------------


@pytest.mark.core
def test_mattermost_api_client_requires_token_and_channel():
    no_token = Mattermost("https://mm.example.test", config={"channel_id": "c"})
    no_token.token = ""  # nosec B105 - deliberately empty for the guard test
    no_token.session = _RecordingHttpSession()
    assert no_token.send_message("Title", "Body") is False
    assert no_token.session.calls == []

    no_channel = Mattermost("https://mm.example.test", token="x", config={})  # nosec B106
    no_channel.session = _RecordingHttpSession()
    assert no_channel.send_message("Title", "Body") is False
    assert no_channel.session.calls == []


@pytest.mark.core
def test_mattermost_api_client_sends_markdown_with_timeout():
    client = Mattermost(
        "https://mm.example.test", token="x", config={"channel_id": "chan"}  # nosec B106
    )
    client.session = _RecordingHttpSession()

    assert client.send_message("Title", "Body") is True
    ((url, kwargs),) = client.session.calls
    assert url == "https://mm.example.test/api/v4/posts"
    assert kwargs["json"]["channel_id"] == "chan"
    assert kwargs["json"]["message"].startswith("### Title")
    assert kwargs["timeout"]
