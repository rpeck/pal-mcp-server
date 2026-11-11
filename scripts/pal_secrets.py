#!/usr/bin/env python3
"""Run pal-secrets from a checkout of the repository: .pal_venv/bin/python scripts/pal_secrets.py <command>.

Use the Python that runs PAL. See utils/pal_secrets.py and docs/1password.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.pal_secrets import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
