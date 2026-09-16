"""Shared Renovate domain rules.

Single home for the policy "what marks a PR or branch as Renovate-managed"
and for parsing the author out of Bitbucket's PR representation. Both the
client layer and the core layer need these; keeping them here stops the
`renovate/` prefix and the three-way author lookup from being duplicated.
"""

RENOVATE_BRANCH_PREFIX = "renovate/"


def source_branch(pr):
    """Source branch name of a PR from the Bitbucket API response."""
    from_ref = pr.get("fromRef", {})
    branch = from_ref.get("displayId", from_ref.get("name"))
    if not branch:
        branch_ref = from_ref.get("id", "")
        if branch_ref.startswith("refs/heads/"):
            branch = branch_ref[len("refs/heads/") :]
    return branch


def pr_author(pr, default=""):
    """Author name of a PR; Bitbucket variants deliver it in three shapes."""
    if "author" in pr and "user" in pr["author"]:
        return pr["author"]["user"].get("name", default)
    if "author" in pr and "username" in pr["author"]:
        return pr["author"].get("username", default)
    if "author" in pr and "name" in pr["author"]:
        return pr["author"].get("name", default)
    return default


def is_renovate_branch(branch_name):
    return bool(branch_name) and branch_name.startswith(RENOVATE_BRANCH_PREFIX)


def is_renovate_pr(pr):
    author = pr_author(pr)
    title = pr.get("title", "")
    return (
        (author and "renovate" in author.lower())
        or "renovate" in title.lower()
        or is_renovate_branch(source_branch(pr) or "")
    )
