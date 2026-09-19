from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_SITE = ROOT / ".venv" / "Lib" / "site-packages"

if VENV_SITE.exists() and str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure local 'tests' directory takes precedence over any user-site 'tests' package
try:
    import tests
    tests_dir = str(ROOT / "tests")
    if tests_dir not in tests.__path__:
        tests.__path__.insert(0, tests_dir)
except ImportError:
    pass

from mura.config import CoreSettings
CoreSettings.model_config["env_file"] = None

