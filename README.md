# Test Renovate 
This repo contains package managers.
This repo is scanned by Renovate and PRs are created. Renovate Operation Test is checking for PRs.

## Run modes and switches

Two switches decide what a run actually does, and the CLI flag is the
authoritative one:

- `--enable-pr-check true` runs the `pr_checks` definitions. Without it the
  checks are skipped.
- `--enable-pr-cleanup true` runs the full cleanup (decline renovate PRs,
  delete branches, optionally decline all non-excluded branches). Without it
  the run only deletes already-declined PRs.

The container entrypoint sets **neither** flag, so a plain
`docker run <image>` only deletes declined PRs. Append the flags in the
Kubernetes `args` to get the other modes. The `cleanup.*` keys in
`config.yaml` are the second level: they are only read once
`--enable-pr-cleanup true` is passed.

### New PR Checks

Add to config.yaml:

```yaml
pr_checks:
  - name: new_check
    titleRegex: "pattern"
    contentRegex: "pattern"
```