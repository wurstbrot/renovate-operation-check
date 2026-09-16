"""Tests for config loading: env placeholder substitution, fail-fast and
the redacted effective-config log."""

import logging
import textwrap

import pytest

from scripts.config_loader import load_yaml_config, log_effective_config
from scripts.core.workflow_manager import WorkflowManager
from scripts.exceptions import ConfigurationError


def write(path, content):
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.mark.core
def test_env_placeholders_are_substituted(tmp_path, monkeypatch):
    monkeypatch.setenv("BITBUCKET_BASE_URL", "https://git.internal.example")
    monkeypatch.setenv("PROJECT_KEY", "my-project")
    config_file = tmp_path / "config.yaml"
    write(
        config_file,
        """
        client:
          base_url: ${BITBUCKET_BASE_URL}
          project_key: ${PROJECT_KEY}
          repo_slug: repo
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["base_url"] == "https://git.internal.example"
    assert config["client"]["project_key"] == "my-project"
    assert config["client"]["repo_slug"] == "repo"


@pytest.mark.core
def test_missing_required_variable_fails_the_run_at_client_construction(tmp_path, monkeypatch):
    """An unset required variable must abort the run, not reach a wrong target."""
    for missing in ("BITBUCKET_BASE_URL", "PROJECT_KEY"):
        monkeypatch.delenv("BITBUCKET_BASE_URL", raising=False)
        monkeypatch.delenv("PROJECT_KEY", raising=False)
        for name in ("BITBUCKET_BASE_URL", "PROJECT_KEY"):
            if name != missing:
                monkeypatch.setenv(name, "some-value")

        config_file = tmp_path / "config.yaml"
        write(
            config_file,
            """
            client:
              base_url: ${BITBUCKET_BASE_URL}
              project_key: ${PROJECT_KEY}
              repo_slug: repo
              token: dummy-token
            """,
        )

        config = load_yaml_config(str(config_file))

        with pytest.raises(ConfigurationError):
            WorkflowManager(args=None, config=config)


@pytest.mark.core
def test_effective_config_log_shows_targets_but_never_secrets(caplog):
    """The startup log must reveal a wrong project key, but no credential."""
    config = {
        "client": {
            "base_url": "https://git.internal.example",
            "project_key": "my-project",
            "repo_slug": "my-repo",
            "token": "SECRET-TOKEN-123",  # nosec B105 - synthetic canary value
        },
        "notifications": {
            "enabled": True,
            "mattermostWebhook": {
                "enabled": True,
                "webhook_key": "SECRET-HOOK-456",  # nosec B105 - canary value
                "url": "https://mattermost.internal.example",
            },
        },
        "pr_checks": [{"name": "NPM", "titleRegex": "Update npm$"}],
    }

    with caplog.at_level(logging.INFO):
        log_effective_config(config)

    assert "SECRET-TOKEN-123" not in caplog.text
    assert "SECRET-HOOK-456" not in caplog.text
    assert "***" in caplog.text
    # the values one needs for diagnosis stay readable
    assert "my-project" in caplog.text
    assert "my-repo" in caplog.text
    assert "https://git.internal.example" in caplog.text
    assert "Update npm$" in caplog.text


@pytest.mark.core
def test_effective_config_log_shows_unset_secret_as_empty(caplog):
    """An unset ${RENOVATE_TOKEN} must be recognizable, not masked as set."""
    with caplog.at_level(logging.INFO):
        log_effective_config({"client": {"token": "", "project_key": "p"}})

    assert '"token": ""' in caplog.text


@pytest.mark.core
def test_retry_after_is_clamped():
    from scripts.clients.bitbucket_server import BitbucketServerClient

    class FakeResponse:
        def __init__(self, retry_after):
            self.headers = {"Retry-After": retry_after}

    assert BitbucketServerClient._get_retry_after(FakeResponse("86400")) == 300
    assert BitbucketServerClient._get_retry_after(FakeResponse("0")) == 1
    assert BitbucketServerClient._get_retry_after(FakeResponse("42")) == 42
    assert BitbucketServerClient._get_retry_after(FakeResponse("not-a-number")) == 30
