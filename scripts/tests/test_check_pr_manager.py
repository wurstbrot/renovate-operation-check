"""Tests for the PR check logic: check definitions vs. open Renovate PRs."""

import pytest

from scripts.core.check_pr_manager import CheckPrManager


class FakeBitbucket:
    def __init__(self, prs):
        self._prs = prs

    def get_renovate_prs(self):
        return self._prs


def make_pr(pr_id, title, description=""):
    return {"id": pr_id, "title": title, "description": description, "state": "OPEN"}


PRS = [
    make_pr(1, "Update npm", "axios bump"),
    make_pr(
        2,
        "Update dependency requests to v2.34.2 CVSS-9.8 [KNOWN-VULNERABILITY]",
        "requests",
    ),
]


def make_manager(checks, prs=PRS):
    return CheckPrManager(FakeBitbucket(prs), {"pr_checks": checks})


@pytest.mark.core
def test_all_checks_matching_returns_empty_error_and_summary():
    errors, summary = make_manager(
        [
            {"name": "NPM", "titleRegex": "Update npm$"},
            {
                "name": "Python Security",
                "titleRegex": r"CVSS-.* \[KNOWN-VULNERABILITY\]$",
                "contentRegex": "requests",
            },
        ]
    ).check_renovate_prs()

    assert errors == ""
    assert summary["open_pr_count"] == 2
    assert [check["passed"] for check in summary["check_results"]] == [True, True]
    assert summary["check_results"][0]["matching_prs"] == [1]
    assert summary["check_results"][1]["matching_prs"] == [2]


@pytest.mark.core
def test_check_without_match_is_reported():
    errors, summary = make_manager(
        [{"name": "Clojure", "titleRegex": "Update deps-edn$"}]
    ).check_renovate_prs()

    assert "Check definition 'Clojure' has no matching PRs" in errors
    assert summary["check_results"][0]["passed"] is False
    assert summary["check_results"][0]["match_count"] == 0


@pytest.mark.core
def test_title_and_content_regex_must_both_match():
    errors, summary = make_manager(
        [{"name": "combo", "titleRegex": "Update npm$", "contentRegex": "does-not-exist"}]
    ).check_renovate_prs()

    assert "has no matching PRs" in errors
    assert summary["check_results"][0]["passed"] is False


@pytest.mark.core
def test_invalid_regex_fails_controlled_instead_of_crashing():
    errors, summary = make_manager(
        [
            {"name": "broken", "titleRegex": "Update npm ("},
            {"name": "NPM", "titleRegex": "Update npm$"},
        ]
    ).check_renovate_prs()

    assert "Check definition 'broken' has an invalid regular expression" in errors
    assert summary["check_results"][0]["passed"] is False
    # the remaining checks still run
    assert summary["check_results"][1]["passed"] is True


@pytest.mark.core
def test_no_check_definitions_and_no_open_prs():
    errors, summary = CheckPrManager(FakeBitbucket(PRS), {}).check_renovate_prs()
    assert errors == "No check definitions loaded"
    assert summary is None

    errors, summary = make_manager([{"name": "x", "titleRegex": "y"}], prs=[]).check_renovate_prs()
    assert errors == "No open Renovate PRs found"
    assert summary == {"open_pr_count": 0, "check_results": []}
