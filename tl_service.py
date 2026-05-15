"""
tl_service.py
TL bot integration for OpenShorts.

Provides non-blocking notification helpers that gracefully degrade
when TL_ENABLED=false or credentials are missing.
"""

import os
import re
import time
import threading
import httpx
from typing import Optional, Any

# Load .env so TL_* vars are available even when app.py hasn't loaded dotenv yet
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("TL_BOT_TOKEN", "TOKEN_MOI_CUA_BAN").strip()
CHAT_ID = os.getenv("TL_CHAT_ID", "1986129893").strip()
ENABLED = os.getenv("TL_ENABLED", "true").lower() in ("1", "true", "yes")

# TL API base
_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Hard limit for a single TL message body (caption or text).
# API allows 4096 chars; we leave headroom for formatting.
_SINGLE_MSG_LIMIT = 3900


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters for safe TL rendering."""
    # Characters that need escaping in MarkdownV2: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escapees = r'_*[]()~`>#+\-=|{}.!'
    for ch in escapees:
        text = text.replace(ch, f"\\{ch}")
    return text


def _is_configured() -> bool:
    return bool(ENABLED and BOT_TOKEN and CHAT_ID)


def _api_url(endpoint: str) -> str:
    return f"{_API_BASE}/{endpoint}"


def _post(endpoint: str, data: Optional[dict] = None, files: Optional[dict] = None) -> Optional[dict]:
    """Make a POST request to the TL Bot API. Returns None on failure."""
    if not _is_configured():
        return None
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(_api_url(endpoint), data=data, files=files)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                print(f"[TL] API error: {result}")
                return None
            return result.get("result")
    except httpx.HTTPStatusError as e:
        print(f"[TL] HTTP {e.response.status_code}: {e.response.text}")
    except Exception as e:
        print(f"[TL] Request failed: {e}")
    return None


def _send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """
    Send a plain text message. Returns True on success.
    parse_mode: "Markdown", "MarkdownV2", or "HTML"
    """
    if not _is_configured():
        return False
    if not text or not text.strip():
        return False
    data = {"chat_id": CHAT_ID, "text": text[:_SINGLE_MSG_LIMIT]}
    if parse_mode:
        data["parse_mode"] = parse_mode
    return _post("sendMessage", data) is not None


def _send_split(text: str, prefix: str = "", parse_mode: str = "Markdown") -> bool:
    """
    Send a long text as one or more messages, splitting at line boundaries
    to keep messages readable.
    """
    if not _is_configured():
        return False
    if not text:
        return False

    # Split into lines, then pack into chunks under the limit
    lines = text.splitlines(keepends=True)
    chunks, current = [], ""

    def flush():
        if current:
            chunks.append(current.rstrip("\n"))
        return ""

    for line in lines:
        if len(current) + len(line) <= _SINGLE_MSG_LIMIT:
            current += line
        else:
            current = flush()
            if len(line) > _SINGLE_MSG_LIMIT:
                # Long line: truncate it
                chunks.append(line[:_SINGLE_MSG_LIMIT])
            else:
                current = line
    flush()

    if prefix:
        chunks.insert(0, prefix)

    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        ok = _send_message(chunk, parse_mode)
        if not ok:
            return False
        # Brief pause between messages to avoid rate limits
        time.sleep(0.2)
    return True


def _format_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    if seconds is None:
        return "n/a"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ─── Public API ────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    return _is_configured()


def send_message(text: str) -> bool:
    """Send a plain text message."""
    return _send_message(text)


def send_code_block(title: str, raw_text: str, job_id: str) -> bool:
    """
    Send a code block (triple-backtick JSON) with a title header.
    Used for raw Gemini responses.
    """
    if not _is_configured():
        return False
    header = f"*{title}*\n`Job ID: {job_id}`\n"
    # Wrap the response in triple backticks for a code block.
    # Escape any existing backticks first.
    escaped = raw_text.replace("```", "\\`\\`\\`")
    body = f"```\n{escaped}\n```"

    # TL sendMessage doesn't support code blocks — use sendDocument instead
    # so the content is displayed as a downloadable code file.
    try:
        import json as _json
        filename = f"gemini_response_{job_id[:8]}.json"
        with httpx.Client(timeout=30.0) as client:
            # Step 1: Send the header as a text message
            _send_message(header, parse_mode="Markdown")

            # Step 2: Send the code block as a text message via sendMessage
            # (MarkdownV2 with escaped code fences works for display)
            escaped_body = _escape_md(body)
            msg = _send_message(escaped_body, parse_mode="MarkdownV2")
            return msg is not None
    except Exception as e:
        print(f"[TL] send_code_block failed: {e}")
        # Fallback: send as-is
        return _send_split(header + body, parse_mode="Markdown")


def send_video(file_path: str, caption: str) -> bool:
    """
    Upload a local video file to TL with a caption.
    Caption is truncated to 1024 chars (TL's caption limit).
    """
    if not _is_configured():
        return False
    if not os.path.exists(file_path):
        print(f"[TL] send_video: file not found: {file_path}")
        return False

    try:
        with httpx.Client(timeout=120.0) as client:
            with open(file_path, "rb") as f:
                files = {"video": f}
                # TL caps captions at 1024 chars
                data = {
                    "chat_id": CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "Markdown",
                }
                resp = client.post(_api_url("sendVideo"), data=data, files=files)
                resp.raise_for_status()
                result = resp.json()
                if not result.get("ok"):
                    print(f"[TL] sendVideo error: {result}")
                    return False
                return True
    except httpx.HTTPStatusError as e:
        print(f"[TL] sendVideo HTTP {e.response.status_code}: {e.response.text}")
    except Exception as e:
        print(f"[TL] sendVideo failed: {e}")
    return False


def send_document(file_path: str, caption: str) -> bool:
    """Send a local file as a TL document (no size limits)."""
    if not _is_configured():
        return False
    if not os.path.exists(file_path):
        print(f"[TL] send_document: file not found: {file_path}")
        return False

    try:
        with httpx.Client(timeout=120.0) as client:
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {
                    "chat_id": CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "Markdown",
                }
                resp = client.post(_api_url("sendDocument"), data=data, files=files)
                resp.raise_for_status()
                result = resp.json()
                return result.get("ok", False)
    except Exception as e:
        print(f"[TL] send_document failed: {e}")
    return False


def notify_job_submitted(job_id: str, url: str, source: str, youtube_title: str = "") -> None:
    """
    Fired immediately when a job is created — from web or TL.
    Runs in a background thread so it never blocks the request.
    """
    def _bg():
        title_part = f"\n📌 YouTube Title: {youtube_title}" if youtube_title else ""
        text = (
            f"📥 *New job submitted*\n"
            f"`Job ID:` `{job_id}`\n"
            f"`Source:` `{source}`\n"
            f"`URL:` {url}{title_part}\n"
            f"`Status:` queued"
        )
        _send_message(text, parse_mode="Markdown")
        # Log locally
        print(f"[TL] Job submitted notification sent for {job_id} (source={source})")

    threading.Thread(target=_bg, daemon=True).start()


def notify_step(job_id: str, step_name: str, details: str = "") -> None:
    """Send a progress step notification. Runs async."""
    def _bg():
        detail_part = f"\n{details}" if details else ""
        text = (
            f"🔄 *Job Update*\n"
            f"`Job ID:` `{job_id}`\n"
            f"`Step:` {step_name}{detail_part}"
        )
        _send_message(text, parse_mode="Markdown")

    threading.Thread(target=_bg, daemon=True).start()


def notify_gemini_response(job_id: str, raw_response: str) -> None:
    """Send the raw Gemini/AI response in a code block. Runs async."""
    def _bg():
        header = (
            f"🤖 *AI Analysis Complete*\n"
            f"`Job ID:` `{job_id}`\n"
            f"\n"
        )
        escaped = raw_response.replace("```", "\\`\\`\\`")
        body = f"```\n{escaped}\n```"
        # Use split to send header separately from code block
        _send_message(header, parse_mode="Markdown")
        _send_message(_escape_md(body), parse_mode="MarkdownV2")
        print(f"[TL] Gemini response sent for {job_id}")

    threading.Thread(target=_bg, daemon=True).start()


def notify_clip_ready(
    job_id: str,
    clip: dict,
    file_path: str,
    source_url: str = "",
    source: str = "web",
    clip_index: int = 0,
) -> None:
    """
    Fired when a single clip file is ready.
    Sends metadata + the video file. Runs async.
    """
    def _bg():
        clip_title = clip.get("video_title_for_youtube_short", "Clip")
        caption_text = clip.get("video_description_for_tiktok") or clip.get("video_description_for_instagram", "")
        viral_hook = clip.get("viral_hook_text", "")
        start_t = clip.get("start")
        end_t = clip.get("end")
        video_url = clip.get("video_url", "")

        parts = [
            f"🎬 *Clip Ready*",
            f"`Job ID:` `{job_id}`",
            f"`Source:` `{source}`",
            f"",
        ]
        if source_url:
            parts.append(f"`YouTube:` {source_url}")
        if clip_title:
            parts.append(f"`Clip Title:` {clip_title}")
        if caption_text:
            parts.append(f"`Caption:` {caption_text[:300]}")
        if viral_hook:
            parts.append(f"`Viral Hook:` {viral_hook}")
        if start_t is not None and end_t is not None:
            parts.append(f"`Time:` {_format_ts(start_t)} → {_format_ts(end_t)}")
        if video_url:
            parts.append(f"`Video URL:` {video_url}")

        header_text = "\n".join(parts)

        # Send metadata first
        _send_message(header_text, parse_mode="Markdown")

        # Then send the video file
        if file_path and os.path.exists(file_path):
            video_caption = f"Clip {clip_index + 1}: {clip_title}"
            send_video(file_path, video_caption)
        else:
            _send_message(f"`Video file not found locally.`", parse_mode="Markdown")

        print(f"[TL] Clip ready notification sent for {job_id}/clip_{clip_index}")

    threading.Thread(target=_bg, daemon=True).start()


def notify_job_completed(job_id: str, clips: list, source: str = "web") -> None:
    """Fired when the entire job pipeline finishes. Runs async."""
    def _bg():
        count = len(clips) if clips else 0
        text = (
            f"✅ *Job Complete*\n"
            f"`Job ID:` `{job_id}`\n"
            f"`Source:` `{source}`\n"
            f"`Clips generated:` {count}"
        )
        _send_message(text, parse_mode="Markdown")
        print(f"[TL] Job completed notification sent for {job_id} ({count} clips)")

    threading.Thread(target=_bg, daemon=True).start()


def notify_job_failed(job_id: str, error: str, source: str = "web") -> None:
    """Fired when the job pipeline fails. Runs async."""
    def _bg():
        # Split long errors to avoid hitting the message limit
        _send_split(
            (
                f"❌ *Job Failed*\n"
                f"`Job ID:` `{job_id}`\n"
                f"`Source:` `{source}`\n"
                f"`Error:`\n"
            ),
            parse_mode="Markdown",
        )
        _send_split(error, parse_mode="Markdown")
        print(f"[TL] Job failed notification sent for {job_id}")

    threading.Thread(target=_bg, daemon=True).start()


# ─── Bot input polling ─────────────────────────────────────────────────────────

def _poll_updates(offset: int = 0) -> Optional[dict]:
    """Fetch new updates from the TL bot. Returns None on failure."""
    if not _is_configured():
        return None
    try:
        url = f"{_API_BASE}/getUpdates"
        params = {"timeout": 30, "offset": offset}
        with httpx.Client(timeout=35.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            result = resp.json()
            if not result.get("ok"):
                return None
            return result.get("result", [])
    except Exception as e:
        print(f"[TL] getUpdates failed: {e}")
    return None


def _send_reply(chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> bool:
    """Send a reply message to a specific chat."""
    if not _is_configured():
        return False
    data = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    return _post("sendMessage", data) is not None


_YOUTUBE_URL_RE = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[\w\-?=&#%+/:.]+",
    re.IGNORECASE,
)


def _extract_youtube_url(text: str) -> Optional[str]:
    match = _YOUTUBE_URL_RE.search(text)
    return match.group(0) if match else None


USAGE_INSTRUCTIONS = (
    "👋 *Welcome to OpenShorts Bot!*\n\n"
    "Send me a *YouTube URL* and I'll automatically:\n"
    "1. Download the video\n"
    "2. Transcribe & analyze it\n"
    "3. Cut the best viral moments\n"
    "4. Send you all clips with metadata\n\n"
    "*Supported formats:*\n"
    "• youtube.com/watch?v=...\n"
    "• youtu.be/...\n"
    "• youtube.com/shorts/...\n\n"
    "Just paste your URL and I'll get started!"
)


def start_bot(on_url_callback) -> None:
    """
    Run the TL bot in a blocking polling loop.
    `on_url_callback(url, chat_id, message_id)` is called for each new YouTube URL.
    This function blocks forever — call it in a dedicated thread.
    """
    if not _is_configured():
        print("[TL] Bot not started: not configured or disabled.")
        return

    print(f"[TL] Bot starting. Token: ...{BOT_TOKEN[-4:]}, Chat ID: {CHAT_ID}")

    # Set webhook to None first to ensure clean polling state
    _post("setWebhook", {"url": ""})

    offset = 0
    while True:
        updates = _poll_updates(offset)
        if updates is None:
            time.sleep(5)
            continue

        for update in updates:
            update_id = update.get("update_id", 0)
            offset = max(offset, update_id + 1)

            message = update.get("message", {})
            if not message:
                continue

            chat_id = message.get("chat", {}).get("id")
            if not chat_id:
                continue

            # Ignore messages not from our configured CHAT_ID (as integer)
            try:
                if int(CHAT_ID) != chat_id:
                    continue
            except ValueError:
                pass

            text = (message.get("text") or message.get("caption") or "").strip()
            message_id = message.get("message_id")

            # /start command
            if text.lower().startswith("/start"):
                _send_reply(chat_id, USAGE_INSTRUCTIONS, message_id)
                continue

            # Check for YouTube URL
            youtube_url = _extract_youtube_url(text)
            if youtube_url:
                # Normalize: ensure https://
                if not youtube_url.startswith("http"):
                    youtube_url = "https://" + youtube_url
                _send_reply(
                    chat_id,
                    f"🔍 Got it! Processing: {youtube_url}\nI'll notify you when clips are ready.",
                    message_id,
                )
                try:
                    on_url_callback(youtube_url, chat_id, message_id)
                except Exception as e:
                    print(f"[TL] Bot callback error: {e}")
                    _send_reply(chat_id, f"⚠️ Failed to start job: {e}", message_id)
            else:
                # Not a YouTube URL
                _send_reply(chat_id, USAGE_INSTRUCTIONS, message_id)
