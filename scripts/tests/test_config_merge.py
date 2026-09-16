"""Tests for the config layering: committed defaults + optional local override."""

import textwrap

from main import deep_merge, load_yaml_config


def write(path, content):
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_deep_merge_merges_dicts_and_replaces_scalars_and_lists():
    base = {
        "client": {"base_url": "https://git.local", "username": "a"},
        "cleanup": {"exclude_branches": ["main", "master"]},
        "timezone": "Europe/Berlin",
    }
    override = {
        "client": {"username": "b"},
        "cleanup": {"exclude_branches": ["main"]},
    }

    merged = deep_merge(base, override)

    assert merged["client"] == {"base_url": "https://git.local", "username": "b"}
    assert merged["cleanup"]["exclude_branches"] == ["main"]
    assert merged["timezone"] == "Europe/Berlin"
    # inputs stay untouched
    assert base["client"]["username"] == "a"


def test_load_without_local_override_keeps_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_MERGE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    write(
        config_file,
        """
        client:
          base_url: https://git.local
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["base_url"] == "https://git.local"


def test_local_override_next_to_config_is_merged(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_MERGE_PATH", raising=False)
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
    write(
        tmp_path / "config.local.yaml",
        """
        client:
          base_url: https://git.internal.example
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["base_url"] == "https://git.internal.example"
    assert config["client"]["project_key"] == "project"
    assert config["logging"]["level"] == "INFO"


def test_env_substitution_runs_after_merge(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_MERGE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERRIDE_TOKEN", "from-env")
    config_file = tmp_path / "config.yaml"
    write(
        config_file,
        """
        client:
          token: default-token
        """,
    )
    write(
        tmp_path / "config.local.yaml",
        """
        client:
          token: ${OVERRIDE_TOKEN}
        """,
    )

    config = load_yaml_config(str(config_file))

    assert config["client"]["token"] == "from-env"


def test_explicit_override_path_wins(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_MERGE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = tmp_path / "config.yaml"
    write(config_file, "timezone: Europe/Berlin\n")
    write(tmp_path / "config.local.yaml", "timezone: UTC\n")
    explicit = tmp_path / "special.yaml"
    write(explicit, "timezone: Europe/Vienna\n")

    config = load_yaml_config(str(config_file), str(explicit))

    assert config["timezone"] == "Europe/Vienna"


def test_retry_after_is_clamped():
    from scripts.clients.bitbucket_server import BitbucketServerClient

    class FakeResponse:
        def __init__(self, retry_after):
            self.headers = {"Retry-After": retry_after}

    assert BitbucketServerClient._get_retry_after(FakeResponse("86400")) == 300
    assert BitbucketServerClient._get_retry_after(FakeResponse("0")) == 1
    assert BitbucketServerClient._get_retry_after(FakeResponse("42")) == 42
    assert BitbucketServerClient._get_retry_after(FakeResponse("not-a-number")) == 30
