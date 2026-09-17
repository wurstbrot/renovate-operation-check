import subprocess
import logging

logger = logging.getLogger("renovate-operation-check")


class LintChecker:

    @staticmethod
    def lint_python_code(file_path):
        results = {}

        try:
            bandit_result = subprocess.run(
                ["bandit", "-r", file_path, "-f", "json", "-c", ".bandit"],
                capture_output=True,
                text=True,
            )
            results["bandit"] = {
                "success": bandit_result.returncode == 0,
                "output": bandit_result.stdout,
            }
            logger.info(
                f"Bandit security results for {file_path}: {'PASS' if bandit_result.returncode == 0 else 'FAIL'}"
            )

            flake8_result = subprocess.run(["flake8", file_path], capture_output=True, text=True)
            results["flake8"] = {
                "success": flake8_result.returncode == 0,
                "output": flake8_result.stdout,
            }
            logger.info(
                f"Flake8 lint results for {file_path}: {'PASS' if flake8_result.returncode == 0 else 'FAIL'}"
            )

            secrets_result = subprocess.run(
                ["detect-secrets", "scan", file_path], capture_output=True, text=True
            )
            has_secrets = "No secrets found" not in secrets_result.stdout
            results["secrets"] = {"success": not has_secrets, "output": secrets_result.stdout}
            if has_secrets:
                logger.warning(f"Potential secrets found in {file_path}")

            return all(r["success"] for r in results.values()), results
        except Exception as e:
            logger.error(f"Error running Python linters: {e}")
            return False, {"error": str(e)}

    @staticmethod
    def lint_dockerfile(file_path):
        try:
            result = subprocess.run(
                ["hadolint", "--failure-threshold", "warning", file_path],
                capture_output=True,
                text=True,
            )
            logger.info(f"Dockerfile lint results for {file_path}:\n{result.stdout}")
            return result.returncode == 0, result.stdout
        except Exception as e:
            logger.error(f"Error running Dockerfile linter: {e}")
            return False, str(e)
