import sys
from pathlib import Path

# Make the repository root importable so `import scripts...` works no matter
# how pytest is invoked.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
