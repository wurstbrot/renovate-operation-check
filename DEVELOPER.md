# Renovate Operation Check Developer Guide
## Environment Variables

- RENOVATE_TOKEN (required; substituted into client.token in config.yaml)
- BITBUCKET_BASE_URL (required; substituted into client.base_url - internal
  host names never appear in the repository)
- PROJECT_KEY (required; substituted into client.project_key)
- RENOVATE_USERNAME (optional; substituted into client.username)
- WEBHOOK_KEY (optional; Mattermost webhook key)
- MATTERMOST_WEBHOOK_URL (required when the webhook provider is enabled;
  substituted into notifications.mattermostWebhook.url)
- MATTERMOST_TOKEN (optional; Mattermost API client)
- JOB_URL (optional; when set, notifications link to the pipeline/job log)

Substitution variables are defined in `config.yaml`. The substitution walks
the whole loaded config recursively and replaces every `${VAR}` pattern, at
any nesting depth - adding a new variable needs no code change. Default
values use `${VAR:default}` with a plain colon; the shell syntax
`${VAR:-default}` is **not** supported and would insert the literal text
`-default`.

## Testing

### Running Tests

```bash
# All tests
python -m pytest scripts/tests/ -v

# Core tests only
python -m pytest scripts/tests/ -m core -v

# With coverage
python -m pytest scripts/tests/ -v --cov --cov-report=html

# Lint + SAST gate (flake8, bandit)
scripts/tests/test_quality.sh
```

# Deployment
## Bitbucket User Access
In our bitbucket, only the account created a PR is able to delete it. Others are only able to decline. 
Decline in renovate results in renovate will not re-create the PR. For normal use, that is great. For checking the functionality, it is not good.
Workaround: Use the renovate user to run this check