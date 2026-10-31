from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class MatchOutcome(str, Enum):
    EXACT_IDENTIFIER_MATCH = "EXACT_IDENTIFIER_MATCH"
    HIGH_CONFIDENCE_CANDIDATE = "HIGH_CONFIDENCE_CANDIDATE"
    POSSIBLE_MATCH = "POSSIBLE_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    NO_MATCH = "NO_MATCH"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"


class EntityType(str, Enum):
    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"
    VIDEO = "video"


class ProviderName(str, Enum):
    SPOTIFY = "spotify"
    YTMUSIC = "ytmusic"
    YOUTUBE = "youtube"
    LOCAL = "local"


@dataclass
class NormalizedItem:
    """
    Common normalized music entity contract across Spotify, YouTube Music, and YouTube.
    Preserves full provenance, original provider IDs, canonical URLs, and capabilities.
    """
    provider: str                       # 'spotify' | 'ytmusic' | 'youtube' | 'local'
    provider_item_id: str               # Authentic provider ID (e.g. Spotify 22-char or YouTube 11-char)
    entity_type: str                    # 'track' | 'album' | 'artist' | 'playlist' | 'video'
    title: str
    primary_creator: str                # Primary artist, composer, or creator/channel
    contributors: List[str] = field(default_factory=list)
    album: Optional[str] = None
    duration_ms: Optional[int] = None
    release_date: Optional[str] = None
    thumbnail_url: Optional[str] = None
    canonical_url: str = ""
    isrc: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    availability: str = "available"     # 'available' | 'restricted' | 'unavailable'
    capabilities: Dict[str, Any] = field(default_factory=dict)
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    match_status: Optional[str] = None
    is_alternative: bool = False
    original_item_id: Optional[str] = None
    original_provider: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "NormalizedItem":
        keys = set(cls.__annotations__.keys())
        filtered = {k: v for k, v in data.items() if k in keys}
        return cls(**filtered)


class MusicProvider(ABC):
    """
    Abstract interface for music catalogue and playback providers.
    """
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        pass

    @abstractmethod
    def search(self, query: str, entity_types: Optional[List[str]] = None, limit: int = 15) -> Dict[str, List[NormalizedItem]]:
        """Searches catalogue and returns results grouped by entity_type."""

    @abstractmethod
    def resolve_url(self, url: str) -> Optional[NormalizedItem]:
        """Resolves exact entity from URL without substitution."""

    @abstractmethod
    def get_capabilities(self, item: NormalizedItem) -> Dict[str, Any]:
        """Evaluates supported playback, embed, and download capabilities for an item."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Returns whether provider is currently enabled in settings."""
