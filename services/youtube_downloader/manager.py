"""
YouTube downloader service manager.

Routes YouTube URLs to the appropriate downloader service based on
configuration. Supports adding new services without changing call sites.

Service priority (first available wins):
  1. YtsaveService  - when USE_YTSAVE_PROXY=true and YTSAVE_PHPSESSID is set
  2. YtdlpService  - always available (fallback)
"""
from .base import YouTubeServiceBase, YouTubeVideoInfo, YouTubeDownloadResult
from .ytsave_service import YtsaveService
from .ytdlp_service import YtdlpService


class YouTubeDownloaderManager:
    """
    Manages YouTube downloader services with automatic routing and fallback.

    Env vars that control routing:
      USE_YTSAVE_PROXY  - if "true"/"1"/"yes", use ytsave as primary
      YTSAVE_PHPSESSID  - cookie for ytsave authentication
    """

    def __init__(self):
        self._services: list[YouTubeServiceBase] = []
        self._init_services()

    def _init_services(self):
        """Initialize services in priority order."""
        self._services.append(YtsaveService())
        self._services.append(YtdlpService())

    def get_primary_service(self) -> YouTubeServiceBase | None:
        """Return the first available (configured) service."""
        for svc in self._services:
            if svc.is_available():
                return svc
        return None

    def get_info(self, url: str) -> YouTubeVideoInfo:
        """
        Fetch video metadata. Tries primary service first, then falls back.
        Raises if all services fail.
        """
        errors = {}
        for svc in self._services:
            if not svc.is_available():
                continue
            try:
                return svc.get_info(url)
            except Exception as e:
                errors[svc.name] = str(e)
                print(f"⚠️ {svc.name} get_info failed: {e}")

        raise RuntimeError(
            f"All YouTube services failed for get_info. Errors: {errors}"
        )

    def download(self, url: str, output_dir: str) -> YouTubeDownloadResult:
        """
        Download a YouTube video. Tries primary service first, then falls back.
        Raises if all services fail.
        """
        errors = {}
        for svc in self._services:
            if not svc.is_available():
                continue
            try:
                return svc.download(url, output_dir)
            except Exception as e:
                errors[svc.name] = str(e)
                print(f"⚠️ {svc.name} download failed: {e}")

        raise RuntimeError(
            f"All YouTube services failed for download. Errors: {errors}"
        )

    def list_services(self) -> list[str]:
        """List all registered service names."""
        return [svc.name for svc in self._services]

    def service_status(self) -> dict:
        """Return availability status of all services."""
        return {
            svc.name: svc.is_available() for svc in self._services
        }
