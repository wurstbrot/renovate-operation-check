#!/bin/bash
# Lint and SAST gate.
#
# Scope is the whole project, not this directory: pointing flake8 and bandit
# at the test folder left main.py and all application code unscanned while
# the gate still reported "all checks passed".
#
# Deliberately no 'set -e' - every check has to run even when an earlier one
# fails, otherwise a lint error silently skips the security scan.

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"

FLAKE8_CONFIG="${SCRIPT_DIR}/.flake8"
BANDIT_CONFIG="${SCRIPT_DIR}/.bandit.yaml"
BANDIT_TEST_CONFIG="${SCRIPT_DIR}/.bandit-tests.yaml"

APP_TARGETS=("${REPO_ROOT}/scripts")
TEST_TARGET="${REPO_ROOT}/scripts/tests"
BANDIT_EXCLUDES="${TEST_TARGET},${REPO_ROOT}/.venv,${REPO_ROOT}/venv,${REPO_ROOT}/build,${REPO_ROOT}/dist"

echo -e "${YELLOW}Repository root: ${REPO_ROOT}${NC}"
echo -e "${YELLOW}Flake8 config:   ${FLAKE8_CONFIG}${NC}"
echo -e "${YELLOW}Bandit configs:  ${BANDIT_CONFIG}, ${BANDIT_TEST_CONFIG}${NC}"

for config_file in "${FLAKE8_CONFIG}" "${BANDIT_CONFIG}" "${BANDIT_TEST_CONFIG}"; do
    if [ ! -f "${config_file}" ]; then
        echo -e "${RED}Error: config file not found at ${config_file}${NC}"
        exit 1
    fi
done

HAS_ERRORS=0

run_check() {
    local label="$1"
    shift
    echo -e "${YELLOW}Running ${label}...${NC}"
    if "$@"; then
        echo -e "${GREEN}${label} passed${NC}"
    else
        echo -e "${RED}${label} failed!${NC}"
        HAS_ERRORS=1
    fi
}

echo -e "${YELLOW}Starting code quality checks...${NC}"

run_check "Flake8 (application and test code)" \
    flake8 "${APP_TARGETS[@]}" --config="${FLAKE8_CONFIG}"

# Application code is scanned without any skips.
run_check "Bandit (application code)" \
    bandit -r "${APP_TARGETS[@]}" -c "${BANDIT_CONFIG}" --exclude "${BANDIT_EXCLUDES}"

# Test code is scanned separately because B101 (assert) is the assertion
# mechanism of pytest and only acceptable there.
run_check "Bandit (test code)" \
    bandit -r "${TEST_TARGET}" -c "${BANDIT_TEST_CONFIG}"

if [ "${HAS_ERRORS}" -ne 0 ]; then
    echo -e "${RED}Code quality checks failed!${NC}"
    exit 1
fi

echo -e "${GREEN}All code quality checks passed!${NC}"
exit 0
