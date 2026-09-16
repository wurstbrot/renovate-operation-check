"""Negative security tests: rejected inputs, denied access, no secrets in logs.

Complements the functional regression tests with proofs that the insecure
paths are refused (see .ai-security-rules/verification.md).
"""

import logging

import pytest

from scripts.core.check_pr_manager import CheckPrManager
from scripts.exceptions import ResponseValidationError
from scripts.clients.schema_validator import validate_response_schema
from scripts.notifications.mattermost_webhook import MattermostWebhook
from scripts.utils.logging_utils import sanitize_for_log

from fake_bitbucket import (
    API_URL,
    OPEN_PR_BRANCH,
    OPEN_PR_ID,
    FakeBitbucketSession,
    FakeResponse,
    make_client,
    make_session,
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    import scripts.clients.bitbucket_server as bitbucket_server_module
    import scripts.core.cleanup_manager as cleanup_manager_module

    monkeypatch.setattr(bitbucket_server_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cleanup_manager_module.time, "sleep", lambda seconds: None)


# --- rejected inputs ---------------------------------------------------------


@pytest.mark.core
def test_schema_validator_rejects_malformed_payload():
    with pytest.raises(ResponseValidationError):
        validate_response_schema({"values": [{"title": 123}]}, "pull_requests")


@pytest.mark.core
def test_malformed_pr_listing_from_server_is_rejected():
    """A Bitbucket response that does not match the schema must raise, not be processed."""

    class MalformedListingSession(FakeBitbucketSession):
        def _handle(self, method, url, params=None, json=None, **kwargs):
            if method == "GET" and url == f"{API_URL}/pull-requests":
                # 'id' and 'state' missing, wrong types
                return FakeResponse(200, {"values": [{"title": 123}]})
            return super()._handle(method, url, params=params, json=json, **kwargs)

    session = make_session()
    session.__class__ = MalformedListingSession
    client = make_client(session)

    with pytest.raises(Exception, match="Invalid Bitbucket Server response format"):
        client.get_pull_requests(state="OPEN")


# --- denied access -----------------------------------------------------------


@pytest.mark.core
def test_decline_with_denied_permission_returns_false_and_keeps_branch():
    class ForbiddenDeclineSession(FakeBitbucketSession):
        def _handle(self, method, url, params=None, json=None, **kwargs):
            if method == "POST" and url.endswith("/decline"):
                return FakeResponse(403, {"errors": [{"message": "insufficient permissions"}]})
            return super()._handle(method, url, params=params, json=json, **kwargs)

    session = make_session()
    session.__class__ = ForbiddenDeclineSession
    client = make_client(session)

    assert client.decline_pull_request(OPEN_PR_ID) is False
    # denied decline must not silently fall through to branch deletion
    assert OPEN_PR_BRANCH in session.branches


@pytest.mark.core
def test_failed_authentication_blocks_api_calls():
    class UnauthorizedSession(FakeBitbucketSession):
        def _handle(self, method, url, params=None, json=None, **kwargs):
            return FakeResponse(401, {"errors": [{"message": "unauthorized"}]})

    session = make_session()
    session.__class__ = UnauthorizedSession
    client = make_client(session)

    assert client.verify_permissions() is False
    assert client.successful_auth_method is None
    with pytest.raises(Exception, match="Failed to authenticate"):
        client._make_request("GET", f"{API_URL}/pull-requests")


# --- no secrets in logs ------------------------------------------------------


class _RecordingHttpSession:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls = []
        self.verify = True

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.status_code, {})


@pytest.mark.core
def test_webhook_key_does_not_appear_in_logs(caplog):
    secret = "SECRET-WEBHOOK-KEY-12345"  # nosec B105 - synthetic canary value for the log test
    with caplog.at_level(logging.DEBUG):
        webhook = MattermostWebhook(
            {
                "url": "https://mattermost.example.test",
                "webhook_key": secret,
                "enabled": True,
            }
        )
        webhook.session = _RecordingHttpSession()
        assert webhook.send_message("Title", "Message") is True

    assert secret not in caplog.text


# --- timeouts on outbound requests -------------------------------------------


@pytest.mark.core
def test_every_bitbucket_request_sets_a_timeout():
    class TimeoutRecordingSession(FakeBitbucketSession):
        kwargs_seen = None

        def request(self, method, url, **kwargs):
            self.kwargs_seen.append(kwargs)
            return super().request(method, url, **kwargs)

        def get(self, url, **kwargs):
            self.kwargs_seen.append(kwargs)
            return super().get(url, **kwargs)

    session = make_session()
    session.__class__ = TimeoutRecordingSession
    session.kwargs_seen = []
    client = make_client(session)

    client.get_pull_requests(state="OPEN")
    client.decline_pull_request(OPEN_PR_ID)

    assert session.kwargs_seen, "no requests were recorded"
    for kwargs in session.kwargs_seen:
        assert kwargs.get("timeout"), f"request without timeout: {kwargs}"


@pytest.mark.core
def test_webhook_request_sets_a_timeout():
    webhook = MattermostWebhook(
        {"url": "https://mattermost.example.test", "webhook_key": "k", "enabled": True}
    )
    webhook.session = _RecordingHttpSession()
    webhook.send_message("Title", "Message")

    ((_, kwargs),) = webhook.session.calls
    assert kwargs.get("timeout")


# --- TLS strictness ------------------------------------------------------------


@pytest.mark.core
def test_x509_strict_verification_is_the_default():
    from scripts.clients.bitbucket_server import BitbucketServerClient
    from scripts.utils.ssl_adapter import RelaxedX509StrictAdapter

    client = BitbucketServerClient(
        base_url="https://bitbucket.example.test",
        project_key="p",
        repo_slug="r",
        token="t",  # nosec B106 - synthetic test value
        ssl_config={"ca_cert_path": ""},
    )
    adapter = client.session.get_adapter("https://bitbucket.example.test")
    assert not isinstance(adapter, RelaxedX509StrictAdapter)


@pytest.mark.core
def test_relax_x509_strict_must_be_explicitly_enabled_and_only_clears_strict_flag():
    import ssl

    from scripts.clients.bitbucket_server import BitbucketServerClient
    from scripts.utils.ssl_adapter import RelaxedX509StrictAdapter

    client = BitbucketServerClient(
        base_url="https://bitbucket.example.test",
        project_key="p",
        repo_slug="r",
        token="t",  # nosec B106 - synthetic test value
        ssl_config={"ca_cert_path": "", "relax_x509_strict": True},
    )
    adapter = client.session.get_adapter("https://bitbucket.example.test")
    assert isinstance(adapter, RelaxedX509StrictAdapter)

    ctx = adapter.poolmanager.connection_pool_kw.get("ssl_context")
    assert ctx is not None
    # only the strict flag is cleared - certificate verification stays on
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.core
def test_webhook_relax_x509_strict_defaults_to_strict():
    from scripts.utils.ssl_adapter import RelaxedX509StrictAdapter

    webhook = MattermostWebhook(
        {"url": "https://mattermost.example.test", "webhook_key": "k", "enabled": True}
    )
    adapter = webhook.session.get_adapter("https://mattermost.example.test")
    assert not isinstance(adapter, RelaxedX509StrictAdapter)


# --- log injection ------------------------------------------------------------


@pytest.mark.core
def test_sanitize_for_log_neutralizes_control_characters():
    forged = "Update axios\n2026-08-25 - INFO - FORGED LOG LINE\r\x1b[31m"
    sanitized = sanitize_for_log(forged)
    assert "\n" not in sanitized
    assert "\r" not in sanitized
    assert "\x1b" not in sanitized


@pytest.mark.core
def test_pr_details_do_not_allow_forged_log_lines():
    manager = CheckPrManager(bitbucket_facade=None, config={"pr_checks": [{"name": "x"}]})
    pr = {
        "id": 1,
        "title": "Update axios\nFORGED-LINE",
        "state": "OPEN",
        "author": {"user": {"name": "renovate\nFORGED-AUTHOR"}},
    }
    details = manager.get_pr_details_string(pr)
    assert "\nFORGED-LINE" not in details
    assert "\nFORGED-AUTHOR" not in details


@pytest.mark.core
def test_debug_log_redacts_secret_request_body_fields(caplog):
    session = make_session()
    client = make_client(session)

    with caplog.at_level(logging.DEBUG):
        client._make_request(
            "POST",
            f"{API_URL}/pull-requests/{OPEN_PR_ID}/decline",
            json={"version": 1, "token": "SECRET-BODY-TOKEN"},  # nosec B105 - canary value
        )

    assert "SECRET-BODY-TOKEN" not in caplog.text
    assert "***" in caplog.text
