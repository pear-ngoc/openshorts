from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class YouTubeVideoInfo:
    """Standardized YouTube video info returned by all downloader services."""
    video_id: str
    title: str
    duration: float  # seconds
    width: int
    height: int
    file_size_bytes: int
    thumbnail_url: str
    uploader: str
    upload_date: str  # YYYYMMDD
    description: str
    view_count: int
    like_count: Optional[int] = None
    availability: str = "public"  # public, unlisted, private

    def to_json(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "file_size_bytes": self.file_size_bytes,
            "thumbnail_url": self.thumbnail_url,
            "uploader": self.uploader,
            "upload_date": self.upload_date,
            "description": self.description,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "availability": self.availability,
        }


@dataclass
class YouTubeDownloadResult:
    """Result of a YouTube download operation."""
    file_path: str
    title: str
    video_info: YouTubeVideoInfo


class YouTubeServiceBase(ABC):
    """Abstract base class for YouTube downloader services."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this service is configured and available."""

    @abstractmethod
    def get_info(self, url: str) -> YouTubeVideoInfo:
        """Fetch video metadata without downloading."""

    @abstractmethod
    def download(self, url: str, output_dir: str) -> YouTubeDownloadResult:
        """Download the video and return path + info."""

    def _extract_video_id(self, url: str) -> str:
        """Extract video ID from a YouTube URL."""
        import re
        patterns = [
            r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'^([a-zA-Z0-9_-]{11})$',
        ]
        for pattern in patterns:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        raise ValueError(f"Could not extract video ID from: {url}")
