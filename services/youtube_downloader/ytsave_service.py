"""
Ytsave.to proxy service for YouTube downloads.

Requires:
  USE_YTSAVE_PROXY=true
  YTSAVE_PHPSESSID=<cookie from browser>

Flow:
  1. POST YouTube URL -> get mediaItems (multiple quality options)
  2. POST highest-quality mediaUrl -> get fileUrl + fileSize
  3. Download from fileUrl
"""
import os
import time
import httpx
from .base import YouTubeServiceBase, YouTubeVideoInfo, YouTubeDownloadResult
from ..utils import sanitize_filename


class YtsaveService(YouTubeServiceBase):
    name = "ytsave"

    def __init__(self):
        self.phpsessid = os.environ.get("YTSAVE_PHPSESSID", "")
        self.enabled = (
            os.environ.get("USE_YTSAVE_PROXY", "false").lower() in ("1", "true", "yes")
            and bool(self.phpsessid)
        )
        self._headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://ytsave.to",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://ytsave.to/vi2/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "sec-gpc": "1",
            "x-requested-with": "XMLHttpRequest",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
        }

    def is_available(self) -> bool:
        return self.enabled

    def get_info(self, url: str) -> YouTubeVideoInfo:
        """Fetch video metadata via ytsave (no download)."""
        video_id = self._extract_video_id(url)
        import urllib.parse
        url_encoded = urllib.parse.quote(url, safe="")

        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.post(
                "https://ytsave.to/proxy.php",
                headers=self._headers,
                cookies={"PHPSESSID": self.phpsessid},
                data=f"url={url_encoded}",
            )
            resp.raise_for_status()
            data = resp.json()

        api_data = data.get("api", {})
        if api_data.get("status") != "ok":
            raise RuntimeError(
                f"Ytsave get_info error: {api_data.get('message', data)}"
            )

        # Pick highest quality video item for file size
        media_items = api_data.get("mediaItems", [])
        video_items = [m for m in media_items if m.get("type") == "Video"]
        chosen = video_items[0] if video_items else {}

        duration_str = chosen.get("mediaDuration", "00:00:00")
        h, m, s = duration_str.split(":")
        duration_sec = int(h) * 3600 + int(m) * 60 + float(s)

        res = chosen.get("mediaRes", "0x0")
        if "x" in res:
            w, h_res = res.split("x")
        else:
            w, h_res = 0, 0

        return YouTubeVideoInfo(
            video_id=video_id,
            title=api_data.get("title", f"youtube_{video_id}"),
            duration=duration_sec,
            width=int(w),
            height=int(h_res),
            file_size_bytes=int(chosen.get("mediaFileSizeBytes", 0)),
            thumbnail_url=(
                f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            ),
            uploader=(
                api_data.get("userInfo", {}).get("name", "Unknown")
            ),
            upload_date="",
            description=api_data.get("description", ""),
            view_count=0,
        )

    def download(self, url: str, output_dir: str) -> YouTubeDownloadResult:
        """Download video via ytsave 3-step flow."""
        import urllib.parse

        video_id = self._extract_video_id(url)
        url_encoded = urllib.parse.quote(url, safe="")

        print(f"🔗 Ytsave downloading: {video_id}")

        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            # Step 1: submit YouTube URL -> get media items
            resp1 = client.post(
                "https://ytsave.to/proxy.php",
                headers=self._headers,
                cookies={"PHPSESSID": self.phpsessid},
                data=f"url={url_encoded}",
            )
            resp1.raise_for_status()
            data1 = resp1.json()

            api_data = data1.get("api", {})
            if api_data.get("status") != "ok":
                raise RuntimeError(
                    f"Ytsave step 1 error: {api_data.get('message', data1)}"
                )

            media_items = api_data.get("mediaItems", [])
            video_items = [m for m in media_items if m.get("type") == "Video"]
            if not video_items:
                raise RuntimeError("Ytsave returned no video media items")

            chosen = video_items[0]
            title = api_data.get("title", f"youtube_{video_id}")
            media_url = chosen["mediaUrl"]
            quality = chosen.get("mediaQuality", "unknown")
            file_size_bytes = int(chosen.get("mediaFileSizeBytes", 0))
            file_size_mb = file_size_bytes / (1024 * 1024)

            print(f"   Title: {title}")
            print(f"   Quality: {quality} ({file_size_mb:.1f} MB)")

            # Step 2: request download URL
            media_url_encoded = urllib.parse.quote(media_url, safe="")
            resp2 = client.post(
                "https://ytsave.to/proxy.php",
                headers=self._headers,
                cookies={"PHPSESSID": self.phpsessid},
                data=f"url={media_url_encoded}",
            )
            resp2.raise_for_status()
            data2 = resp2.json()

            api2 = data2.get("api", {})
            if api2.get("status") != "completed":
                raise RuntimeError(f"Ytsave step 2 error: {api2}")

            file_url = api2["fileUrl"]
            dl_size_mb = int(api2.get("fileSizeBytes", 0)) / (1024 * 1024)
            print(f"   Download ready: {dl_size_mb:.1f} MB")

            # Step 3: download
            sanitized = sanitize_filename(title)
            output_file = os.path.join(output_dir, f"{sanitized}.mp4")

            print(f"📥 Downloading from ytsave CDN ({dl_size_mb:.1f} MB)...")
            dl_resp = client.get(file_url, timeout=600.0, follow_redirects=True)
            dl_resp.raise_for_status()

            total_bytes = 0
            with open(output_file, "wb") as f:
                for chunk in dl_resp.iter_bytes(chunk_size=131072):
                    f.write(chunk)
                    total_bytes += len(chunk)

            downloaded_mb = total_bytes / (1024 * 1024)
            print(f"✅ Downloaded via ytsave: {downloaded_mb:.1f} MB -> {output_file}")

        video_info = self.get_info(url)
        video_info.file_size_bytes = total_bytes

        return YouTubeDownloadResult(
            file_path=output_file,
            title=sanitized,
            video_info=video_info,
        )
