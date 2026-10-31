"""Process-wide singletons and helpers shared by the web blueprints."""
import logging
from pathlib import Path

from src.config_loader import PROJECT_ROOT
from src.metadata_service import MetadataService
from src.queue_manager import QueueManager
from src.security import SecurityManager
from src.sync_engine import SyncEngine

logger = logging.getLogger("WebServer")

queue_manager = QueueManager()
metadata_service = MetadataService()
sync_engine = SyncEngine()

_PROTECTED_DIRS = (".cache", "config", "data", ".venv", "bin", "src")


def _is_user_media_path(p: Path) -> bool:
    """Inside the data root, but never in credential/config/database/code folders."""
    if not SecurityManager.validate_safe_path(p, PROJECT_ROOT):
        return False
    root = PROJECT_ROOT.resolve()
    return not any(SecurityManager.validate_safe_path(p, root / d) for d in _PROTECTED_DIRS)
