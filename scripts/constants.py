#!/usr/bin/env python3

PR_CHECK_PASS = "pass"  # nosec B105 - PR check status label, not a password
PR_CHECK_FAIL = "fail"
PR_CHECK_WARN = "warn"
PR_CHECK_SKIP = "skip"

PR_TYPE_SECURITY = "security"
PR_TYPE_REGULAR = "regular"
PR_TYPE_DOCKER = "docker"
PR_TYPE_UNKNOWN = "unknown"

RETURN_STATUS_XXX = 0
STATUS_ERROR = 1
STATUS_WARN = 2
