"""Local configuration. Never loads .env or host credentials."""
from dataclasses import dataclass
from pathlib import Path

MAX_UPLOAD = 20 * 1024 * 1024
MAX_PAGES = 200
MAX_IMAGES = 100
MAX_JSON = 32 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_EDGE = 1600
INDEX_VERSION = 1


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    allowed_roots: tuple[Path, ...]
