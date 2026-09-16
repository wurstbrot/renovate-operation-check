"""Tests for the branch-reset flow (disabled by default in config.yaml)."""

import pytest

import scripts.clients.bitbucket_server as bitbucket_server_module
import scripts.core.cleanup_manager as cleanup_manager_module
from scripts.core.cleanup_manager import CleanupManager

from fake_bitbucket import (
    FakeBitbucketSession,
    make_branch,
    make_client,
    make_session,
)

REF_HASH = "feedfacefeedfacefeedfacefeedfacefeedface"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(bitbucket_server_module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cleanup_manager_module.time, "sleep", lambda seconds: None)


def make_reset_session():
    session = make_session()
    reference = make_branch("reference")
    reference["latestCommit"] = REF_HASH
    session.branches["reference"] = reference
    return session


def make_manager(session):
    return CleanupManager(make_client(session), {})


@pytest.mark.core
def test_reset_creates_merge_branch_and_merges_into_target():
    session = make_reset_session()

    assert make_manager(session).reset_branch_from_reference("master", "reference") is True

    assert session.branches["master"]["latestCommit"] == REF_HASH
    assert any(
        name.startswith("merge-reference-to-master-") for name in session.created_branch_names
    )
    assert session.merged_pr_ids


@pytest.mark.core
def test_reset_falls_back_to_force_update_on_merge_conflicts():
    session = make_reset_session()
    session.merge_conflict = True

    assert make_manager(session).reset_branch_from_reference("master", "reference") is True

    # no merge happened - the force branch update did the reset
    assert session.merged_pr_ids == []
    assert session.branches["master"]["latestCommit"] == REF_HASH


@pytest.mark.core
def test_reset_fails_when_no_reference_branch_exists():
    session = FakeBitbucketSession([], [make_branch("trunk")])

    assert make_manager(session).reset_branch_from_reference("trunk", "reference") is False
