# Renovate Operation Check
The repo under test contains package managers (see docs/sample-monitoring-content).
The repo under test is scanned by Renovate and PRs are created. This Renovate Operation Check is checking for PRs and alerts you if the expected PRs (see `scripts/config/config.yaml`) doesn't exists.

## Run modes and switches

Two switches decide what a run actually does, and the CLI flag is the
authoritative one:

- `--enable-pr-check true` runs the `pr_checks` definitions. Without it the
  checks are skipped.
- `--enable-pr-cleanup true` runs the full cleanup (decline renovate PRs,
  delete branches, optionally decline all non-excluded branches). Without it
  the run only deletes already-declined PRs.

### New PR Checks

Adjust your config.yaml:

```yaml
pr_checks:
  - name: new_check
    titleRegex: "pattern"
    contentRegex: "pattern"
```
e.g.:
```yaml
pr_checks:
  - name: NPM Major
    titleRegex: 'Update npm \(major\)$'
```
