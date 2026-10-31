import logging
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("FileOrganizer")

RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}


class FileOrganizer:
    """
    Filename formatting, Windows path containment,
    traversal prevention, invalid character cleanup, and collision handling.
    """

    @staticmethod
    def sanitize(component: str, max_length: int = 120) -> str:
        """
        Sanitizes a single path segment strictly for Windows filesystem rules:
        - Removes control characters and illegal characters: \\ / : * ? " < > |
        - Disallows traversal patterns (..)
        - Strips trailing dots and whitespace
        - Neutralizes reserved Windows device names (CON, NUL, AUX, PRN, etc.)
        """
        if not component:
            return "Unknown"

        # Disallow directory traversal
        if component in ("..", "."):
            return "Unknown"

        # Strip control characters (0-31) and illegal Windows characters
        cleaned = re.sub(r'[\x00-\x1f\\/*?:"<>|]', "", component).strip()
        # Collapse multiple whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # Trailing dots/spaces are illegal in Windows filenames
        cleaned = cleaned.rstrip(". ")

        # Check for reserved DOS device names (e.g., CON, PRN, NUL)
        stem_upper = cleaned.split(".")[0].upper()
        if stem_upper in RESERVED_NAMES:
            cleaned = f"_{cleaned}"

        if not cleaned:
            cleaned = "Unknown"

        return cleaned[:max_length]

    @classmethod
    def resolve_output_path(
        cls,
        base_dir: Path,
        template: str,
        meta: dict,
        output_format: str,
        collision_strategy: str = "skip",
        create_parent: bool = False,
        folder_organization: Optional[str] = None
    ) -> Tuple[Path, bool]:
        """
        Renders template into destination path and guarantees path containment.
        Honors folder_organization ('playlist', 'artist_album', 'flat').
        Returns (target_path, should_skip).
        Raises ValueError if any path traversal outside base_dir is detected.
        """
        ext = output_format.lower().lstrip(".")
        artists_list = meta.get("artists", ["Unknown Artist"])
        artists_str = ", ".join(artists_list) if isinstance(artists_list, list) else str(artists_list)
        primary_artist = artists_list[0] if isinstance(artists_list, list) and artists_list else artists_str

        # Resolve folder_organization setting if not explicitly provided
        if folder_organization is None:
            try:
                from src.config_loader import get_config
                folder_organization = get_config().get("naming", {}).get("folder_organization", "playlist")
            except Exception:
                folder_organization = "playlist"

        effective_template = template
        pl_name = str(meta.get("playlist_name", "") or meta.get("collection_name", "")).strip()
        is_playlist = (
            meta.get("is_playlist", False)
            or bool(meta.get("collection_id"))
            or meta.get("kind") == "playlist"
            or (bool(pl_name) and pl_name.lower() not in ("downloads", "singles", "unknown", ""))
        )

        has_dir = ("/" in template) or ("\\" in template)

        if folder_organization == "playlist":
            if is_playlist and pl_name and pl_name.lower() not in ("downloads", "") and "{playlist}" not in template:
                effective_template = f"{{playlist}}/{template}"
        elif folder_organization == "artist_album":
            if not has_dir and "{artist}" not in template and "{album}" not in template:
                effective_template = f"{{artist}}/{{album}}/{template}"

        subs = {
            "title": cls.sanitize(meta.get("title", "Untitled")),
            "artist": cls.sanitize(primary_artist),
            "artists": cls.sanitize(artists_str),
            "album": cls.sanitize(meta.get("album") or "Singles"),
            "album_artist": cls.sanitize(meta.get("album_artist") or primary_artist),
            "year": cls.sanitize(str(meta.get("year", "0000"))),
            "release_date": cls.sanitize(str(meta.get("release_date", ""))),
            "track_number": f"{int(meta.get('track_number', 1)):02d}",
            "track_count": str(meta.get("total_tracks", 1)),
            "disc_number": f"{int(meta.get('disc_number', 1)):02d}",
            "disc_count": str(meta.get("total_discs", 1)),
            "playlist": cls.sanitize(pl_name or "Downloads"),
            "position": f"{int(meta.get('position', 1)):02d}",
            "isrc": cls.sanitize(meta.get("isrc", "")),
            "extension": ext,
            "ext": ext
        }

        # Apply substitutions
        rel_str = effective_template
        for k, v in subs.items():
            rel_str = rel_str.replace(f"{{{k}}}", str(v))

        # Handle file extension
        if not rel_str.endswith(f".{ext}"):
            rel_str += f".{ext}"

        # Clean parts and assemble
        raw_parts = [p for p in Path(rel_str).parts if p]
        sanitized_parts = [cls.sanitize(p) for p in raw_parts if p not in ("..", ".")]

        base_resolved = base_dir.resolve()
        target_path = base_resolved.joinpath(*sanitized_parts)

        # STRICT PATH CONTAINMENT CHECK: Ensure target does not escape base_dir
        try:
            target_path.resolve().relative_to(base_resolved)
        except (ValueError, RuntimeError):
            logger.error(f"Path traversal detected in template! Target: {target_path}, Base: {base_resolved}")
            raise ValueError(f"Path traversal detected: Target '{target_path}' is outside '{base_resolved}'") from None

        # Windows MAX_PATH length handling (keep total length under 250 characters safely)
        if len(str(target_path)) >= 245:
            excess = len(str(target_path)) - 240
            stem = target_path.stem
            truncated_stem = stem[:-excess] if len(stem) > excess else stem[:50]
            target_path = target_path.with_name(f"{truncated_stem}.{ext}")

        if create_parent:
            target_path.parent.mkdir(parents=True, exist_ok=True)

        # Collision Handling
        if target_path.exists():
            if collision_strategy == "skip":
                return target_path, True
            elif collision_strategy == "overwrite":
                return target_path, False
            elif collision_strategy == "rename":
                counter = 1
                while target_path.exists():
                    stem = target_path.stem
                    base_stem = re.sub(r"\s\(\d+\)$", "", stem)
                    target_path = target_path.with_name(f"{base_stem} ({counter}).{ext}")
                    counter += 1
                return target_path, False

        return target_path, False
