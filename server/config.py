"""Environment-driven configuration. No config files, no defaults for secrets."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Database file. Defaults to a gitignored file next to the repo.
DB_PATH = Path(os.environ.get("OMNICLOUD_DB", REPO_ROOT / "omnicloud.db"))

# Fernet master key (base64). Required to store or use provider credentials.
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MASTER_KEY = os.environ.get("OMNICLOUD_MASTER_KEY", "")

SESSION_TTL_DAYS = int(os.environ.get("OMNICLOUD_SESSION_TTL_DAYS", "30"))

# Default sync interval for new accounts (minutes); editable per-account in Settings.
DEFAULT_SYNC_INTERVAL_MIN = int(os.environ.get("OMNICLOUD_SYNC_INTERVAL_MIN", "5"))
