"""
yt-dlp-based YouTube downloader service.
"""
import os
import time
import yt_dlp
from .base import YouTubeServiceBase, YouTubeVideoInfo, YouTubeDownloadResult
from ..utils import sanitize_filename


class YtdlpService(YouTubeServiceBase):
    name = "yt-dlp"

    def __init__(self):
        self.cookies_path = None
        cookies_env = os.environ.get("YOUTUBE_COOKIES", "")
        if cookies_env:
            self.cookies_path = "/tmp/youtube_cookies.txt"
            try:
                with open(self.cookies_path, "w") as f:
                    f.write(cookies_env)
            except Exception:
                self.cookies_path = None

        self._common_opts = {
            "quiet": False,
            "verbose": True,
            "no_warnings": False,
            "cookiefile": self.cookies_path,
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "nocheckcertificate": True,
            "cachedir": False,
            "extractor_args": {
                "youtube": {
                    "player_client": ["web", "mweb", "ios", "android"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        }

    def is_available(self) -> bool:
        return True  # Always available (yt-dlp is installed)

    def _to_video_info(self, info: dict) -> YouTubeVideoInfo:
        return YouTubeVideoInfo(
            video_id=info.get("id", ""),
            title=info.get("title", "youtube_video"),
            duration=float(info.get("duration", 0) or 0),
            width=int(info.get("width", 0) or 0),
            height=int(info.get("height", 0) or 0),
            file_size_bytes=int(info.get("filesize", 0) or 0),
            thumbnail_url=info.get("thumbnail", ""),
            uploader=info.get("uploader", ""),
            upload_date=info.get("upload_date", ""),
            description=info.get("description", ""),
            view_count=int(info.get("view_count", 0) or 0),
            like_count=int(info.get("like_count", 0) or 0) if info.get("like_count") else None,
            availability=(
                "unlisted" if info.get("availability") == "unlisted"
                else "private" if info.get("availability") == "private"
                else "public"
            ),
        )

    def get_info(self, url: str) -> YouTubeVideoInfo:
        """Extract video metadata without downloading."""
        opts = {**self._common_opts}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return self._to_video_info(info)

    def download(self, url: str, output_dir: str) -> YouTubeDownloadResult:
        """Download the video using yt-dlp."""
        video_id = self._extract_video_id(url)
        sanitized_title = f"yt_{video_id}"

        output_template = os.path.join(output_dir, f"{sanitized_title}.%(ext)s")
        expected_file = os.path.join(output_dir, f"{sanitized_title}.mp4")
        if os.path.exists(expected_file):
            os.remove(expected_file)

        opts = {
            **self._common_opts,
            "format": (
                "bestvideo[height>=2160]+bestaudio/"
                "bestvideo[height>=1440]+bestaudio/"
                "bestvideo[height>=1080]+bestaudio/"
                "bestvideo+bestaudio/best"
            ),
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "overwrites": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            video_info = self._to_video_info(info)
            sanitized_title = sanitize_filename(info.get("title", f"yt_{video_id}"))
            ydl.download([url])

        downloaded_file = os.path.join(output_dir, f"{sanitized_title}.mp4")
        if not os.path.exists(downloaded_file):
            for f in os.listdir(output_dir):
                if f.startswith(sanitized_title) and f.endswith(".mp4"):
                    downloaded_file = os.path.join(output_dir, f)
                    break

        return YouTubeDownloadResult(
            file_path=downloaded_file,
            title=sanitized_title,
            video_info=video_info,
        )

