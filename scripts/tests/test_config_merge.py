"""Tests for config loading and environment-variable substitution."""

import textwrap

from scripts.config_loader import load_yaml_config


def write(path, content):
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_load_reads_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    write(
        config_file,
        """
        client:
          base_url: https://git.local
          project_key: project
        logging:
          level: INFO
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["base_url"] == "https://git.local"
    assert config["client"]["project_key"] == "project"
    assert config["logging"]["level"] == "INFO"


def test_env_substitution_is_applied(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERRIDE_TOKEN", "from-env")
    config_file = tmp_path / "config.yaml"
    write(
        config_file,
        """
        client:
          token: ${OVERRIDE_TOKEN}
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["token"] == "from-env"


def test_missing_config_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    config = load_yaml_config(str(tmp_path / "does-not-exist.yaml"))

    assert config == {}


def test_retry_after_is_clamped():
    from scripts.clients.bitbucket_server import BitbucketServerClient

    class FakeResponse:
        def __init__(self, retry_after):
            self.headers = {"Retry-After": retry_after}

    assert BitbucketServerClient._get_retry_after(FakeResponse("86400")) == 300
    assert BitbucketServerClient._get_retry_after(FakeResponse("0")) == 1
    assert BitbucketServerClient._get_retry_after(FakeResponse("42")) == 42
    assert BitbucketServerClient._get_retry_after(FakeResponse("not-a-number")) == 30
