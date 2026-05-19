"""
YouTube downloader service manager.

Provides a unified interface for downloading YouTube videos via
configurable backend services (ytsave, yt-dlp, etc.).

Usage:
    from services.youtube_downloader import get_video_info, download_youtube

    # Get info only
    info = get_video_info("https://www.youtube.com/watch?v=VIDEO_ID")
    print(info.to_json())

    # Download
    result = download_youtube("https://www.youtube.com/watch?v=VIDEO_ID", "./output")
    print(result.file_path)
"""
import os
from .base import YouTubeServiceBase, YouTubeVideoInfo, YouTubeDownloadResult
from .manager import YouTubeDownloaderManager

# Default manager instance
_manager: YouTubeDownloaderManager | None = None


def _get_manager() -> YouTubeDownloaderManager:
    global _manager
    if _manager is None:
        _manager = YouTubeDownloaderManager()
    return _manager


def get_video_info(url: str) -> YouTubeVideoInfo:
    """
    Fetch YouTube video metadata without downloading.
    Routes to the configured primary service based on env vars.

    Returns YouTubeVideoInfo with fields: video_id, title, duration, width,
    height, file_size_bytes, thumbnail_url, uploader, upload_date,
    description, view_count, like_count, availability.
    """
    return _get_manager().get_info(url)


def download_youtube(url: str, output_dir: str) -> YouTubeDownloadResult:
    """
    Download a YouTube video to output_dir.

    Returns YouTubeDownloadResult with: file_path, title, video_info.

    Routes to the primary configured service; falls back through the
    service chain if the primary is unavailable.
    """
    return _get_manager().download(url, output_dir)
