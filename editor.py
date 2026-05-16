import os
import json
import re
import subprocess
import time
import base64
import cv2
from typing import Optional, Union
from openai import OpenAI as _OpenAI


def _normalize_openai_base_url(base_url: str) -> str:
    """Ensure an OpenAI-compatible base URL ends with /v1 for chat completions."""
    base_url = base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = base_url + "/v1"
    return base_url


def _is_openai_compatible(base_url: Optional[str]) -> bool:
    """Detect if we're using a self-hosted OpenAI-compatible endpoint."""
    if not base_url:
        return False
    base_url_lower = base_url.lower().strip()
    blocked = {"generativelanguage.googleapis.com", "googleapis.com"}
    return not any(b in base_url_lower for b in blocked)


class VideoEditor:
    def __init__(self, api_key, base_url=None, model_name=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name or "gemini-3-flash-preview"

        if _is_openai_compatible(base_url):
            self._provider = "openai_compatible"
            self._client = _OpenAI(api_key=api_key, base_url=_normalize_openai_base_url(base_url))
            self._video_file = None
        else:
            self._provider = "gemini"
            from google import genai as _genai
            from google.genai import types as _types
            self._genai = _genai
            self._types = _types
            self._client = _genai.Client(
                api_key=api_key,
                http_options=_types.HttpOptions(base_url=base_url) if base_url else None,
            )
            self._video_file = None

    def _extract_frames(self, video_path: str, max_frames: int = 8) -> list[str]:
        """Extract frames from video and return as base64 JPEG strings."""
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps == 0:
            fps = 30
        duration = total_frames / fps

        frame_indices = [int(i * total_frames / max_frames) for i in range(max_frames)]
        frames = []

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if ret:
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                b64 = base64.b64encode(buffer).decode('utf-8')
                frames.append(b64)

        cap.release()

        if not frames:
            raise ValueError(f"Could not extract frames from {video_path}")

        return frames

    def upload_video(self, video_path):
        """
        For Gemini: uploads video to Gemini File API.
        For Ollama: stores path and extracts frames on-demand.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        self._video_path = video_path

        if self._provider == "gemini":
            print(f"📤 Uploading {video_path} to Gemini...")
            try:
                file_upload = self._client.files.upload(file=video_path)
            except Exception as e:
                print(f"❌ Gemini Upload Error: {e}")
                raise e

            print("⏳ Waiting for video processing by Gemini...")
            while True:
                file_info = self._client.files.get(name=file_upload.name)
                if file_info.state == "ACTIVE":
                    print("✅ Video processed and ready.")
                    self._video_file = file_upload
                    return file_upload
                elif file_info.state == "FAILED":
                    raise Exception("Video processing failed by Gemini.")
                time.sleep(2)
        else:
            print(f"📤 Storing video path for Ollama/LM Studio: {video_path}")
            self._video_file = video_path
            return video_path

    def _call_llm(self, prompt: str, system_prompt: str = None) -> str:
        """Make an LLM call. Works with both Gemini and OpenAI-compatible."""
        if self._provider == "gemini":
            contents = [self._video_file, prompt]
            config = self._types.GenerateContentConfig(
                response_mime_type="application/json"
            )
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )
            return response.text
        else:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            video_path = getattr(self, '_video_path', None)
            if video_path and os.path.exists(video_path):
                frames = self._extract_frames(video_path, max_frames=8)
                vision_content = []
                for i, b64 in enumerate(frames):
                    time_pos = i * (30 / 8)
                    vision_content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        }
                    })
                vision_content.append({"type": "text", "text": prompt})
                messages.append({
                    "role": "user",
                    "content": vision_content
                })
            else:
                messages.append({"role": "user", "content": prompt})

            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.2,
            )
            return response.choices[0].message.content or ""

    def _clean_json_response(self, text: str) -> str:
        """Strip markdown fences and extract JSON from LLM response."""
        cleaned = text
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        start_idx = cleaned.find('{')
        end_idx = cleaned.rfind('}')
        if start_idx != -1 and end_idx != -1:
            cleaned = cleaned[start_idx:end_idx+1]
        return cleaned

    def get_ffmpeg_filter(self, video_file_obj, duration, fps=30, width=None, height=None, transcript=None):
        """Asks LLM for a raw FFmpeg filter string."""
        if width is None or height is None:
            width, height = 1080, 1920

        transcript_text = json.dumps(transcript) if transcript else "Not available."

        prompt = f"""
You are an expert FFmpeg video editor. Your task is to generate a complex video filter string to make a short video viral, BUT ONLY apply effects where they make sense contextually.

Video Duration: {duration} seconds.
Video FPS: {fps}
Video Resolution (MUST KEEP EXACT): {width}x{height}

TRANSCRIPT (Context of what is being said):
{transcript_text}

Goal: Enhance the video with dynamic zooms, cuts (simulated with punch-ins), and visual effects to increase retention, but DO NOT overdo it. Random effects are bad. Contextual effects are good.

Instructions:
1. ANALYZE THE VIDEO AND TRANSCRIPT: Understand the mood, the pacing, and the key moments.
2. APPLY EFFECTS ONLY WHEN RELEVANT:
   - Use "punch-in" zooms (zoompan) to emphasize key points, jokes, or dramatic moments in the speech.
   - slow zooms to face when the speaker is speaking
   - Use visual effects (contrast, saturation, sharpness) to highlight mood changes or specific segments.
   - If nothing significant is happening, keep it simple. It is BETTER to have no effect than a random/distracting one.
   - Avoid constant motion if the speaker is delivering a serious or steady message.
3. Create a single valid FFmpeg filter complex string (for the -vf flag).
4. Use filters like `zoompan`, `eq` (contrast), `hue` (saturation/bw), `unsharp`.
5. Pacing: Align effects with the rhythm of the speech (from transcript) or visual action.
6. CRITICAL SYNTAX RULES:
   - DO NOT use comparison operators like `<`, `>`, `<=`, `>=` anywhere. They frequently break FFmpeg expression parsing.
   - USE FFmpeg expression FUNCTIONS instead:
     - `between(x,a,b)`
     - `lt(x,y)`, `lte(x,y)`, `gt(x,y)`, `gte(x,y)`
     - `if(cond,then,else)`
   - Always wrap expression values in single quotes: `z='...'`, `x='...'`, `y='...'`, `enable='...'`.
   
   - FOR `zoompan`: 
     - Prefer `on` (output frame index) to avoid time-variable quirks.
     - Convert seconds to frames using FPS={fps}: `frame = seconds * {fps}`.
     - Use `between(on, startFrame, endFrame)` for segmenting and pacing.
     - Example:
      `zoompan=z='1.1*between(on,0,75)+1.3*between(on,76,150)+1.15*between(on,151,300)+1.2*gte(on,301)'`
     - ALWAYS set zoompan output size to EXACT `{width}x{height}` using `s={width}x{height}`.
     - ALWAYS set `fps={fps}` and `d=1`.
     - DO NOT use `scale`, `crop`, `pad` unless you keep EXACT `{width}x{height}` (no aspect ratio changes).
     
   - FOR `eq`, `hue`, `curves`, `unsharp` (Visual Effects): 
     - **DO NOT** use dynamic expressions for parameter values (e.g. `contrast='1+0.5*t'`).
     - **USE TIMELINE EDITING** via the `enable` option.
     - Create MULTIPLE filter instances for different time ranges.
     - **SYNTAX FOR ENABLE:**
      - **USE** `between(t,start,end)` for clarity and robustness.
      - **USE** single quotes around the enable expression.
      - **Example:** `eq=contrast=1.2:enable='between(t,0,3)'`
      - **Example:** `hue=s=0:enable='between(t,10,12)'`
     - This is much safer and robust than boolean multiplication.
    
Constraints:
- Output JSON with a single key: "filter_string".
- The value must be the RAW filter string ready to be passed to `-vf`.
- OUTPUT MUST KEEP EXACT RESOLUTION AND ASPECT RATIO: {width}x{height}.
- Do NOT output 1280x720 or 1080x1080 unless the input is exactly that.
- IMPORTANT: Do NOT include the `-vf` flag itself, just the filter content.
- IMPORTANT: Ensure syntax is correct for FFmpeg. 

Output JSON:
{{
    "filter_string": "..."
}}
"""

        system_prompt = "You are an expert FFmpeg video editor. Return only valid JSON."

        print(f"🤖 Asking LLM for FFmpeg filter (provider: {self._provider})...")
        response_text = self._call_llm(prompt, system_prompt)

        print(f"🔍 DEBUG: LLM Raw Response:\n{response_text}")

        try:
            cleaned = self._clean_json_response(response_text)
            print(f"🔍 DEBUG: Cleaned JSON Text:\n{cleaned}")
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"❌ Failed to parse JSON: {response_text}")
            return None

    def get_effects_config(self, video_file_obj, duration, fps=30, width=None, height=None, transcript=None):
        """Asks LLM for a structured EffectsConfig JSON for Remotion rendering."""
        if width is None or height is None:
            width, height = 1080, 1920

        transcript_text = json.dumps(transcript) if transcript else "Not available."

        prompt = f"""
You are an expert video editor analyzing a video and its transcript to generate dynamic visual effects for a Remotion-based renderer.

Video Duration: {duration} seconds.
Video FPS: {fps}
Video Resolution: {width}x{height}

TRANSCRIPT (Context of what is being said):
{transcript_text}

Your task is to produce a structured JSON describing time-based effect segments that cover the FULL video duration.

Each segment has these fields:
- "startSec" (number): Start time in seconds.
- "endSec" (number): End time in seconds.
- "zoom" (number): Zoom level. 1.0 = no zoom, max 1.5. Use subtle values like 1.05-1.2 for most cases.
- "zoomCenterX" (number): Horizontal focus point for zoom, 0.0 (left) to 1.0 (right). 0.5 = center.
- "zoomCenterY" (number): Vertical focus point for zoom, 0.0 (top) to 1.0 (bottom). 0.5 = center.
- "brightness" (number): Brightness multiplier. 1.0 = normal. Range 0.8-1.2.
- "contrast" (number): Contrast multiplier. 1.0 = normal. Range 0.8-1.3.
- "saturate" (number): Saturation multiplier. 1.0 = normal. Range 0.8-1.3.

Instructions:
1. ANALYZE the video content and transcript to understand mood, pacing, and key moments.
2. Apply CONTEXTUAL effects aligned with speech and action:
   - Use slow, subtle zooms toward the speaker's face during speaking moments.
   - Emphasize key moments, punchlines, or dramatic beats with slightly stronger zoom or contrast.
   - Keep transitions smooth — avoid jarring jumps between segments.
   - If nothing significant is happening, keep values at defaults (zoom 1.0, all multipliers 1.0).
3. Segments MUST cover the entire video duration from 0 to {duration} seconds with no gaps.
4. Prefer fewer, longer segments with gradual changes over many rapid short segments.
5. Output ONLY valid JSON, no explanations.

Output format:
{{
    "segments": [
        {{
            "startSec": 0,
            "endSec": 3.5,
            "zoom": 1.0,
            "zoomCenterX": 0.5,
            "zoomCenterY": 0.5,
            "brightness": 1.0,
            "contrast": 1.0,
            "saturate": 1.0
        }}
    ]
}}
"""

        system_prompt = "You are an expert video editor. Return only valid JSON."

        print(f"🤖 Asking LLM for Remotion effects config (provider: {self._provider})...")
        response_text = self._call_llm(prompt, system_prompt)

        print(f"🔍 DEBUG: LLM Raw Response:\n{response_text}")

        try:
            cleaned = self._clean_json_response(response_text)
            print(f"🔍 DEBUG: Cleaned JSON Text:\n{cleaned}")
            return json.loads(cleaned)
        except json.JSONDecodeError:
            print(f"❌ Failed to parse effects config JSON: {response_text}")
            return None

    @staticmethod
    def _split_filter_chain(filter_string: str) -> list[str]:
        """Split a -vf filter chain on commas, respecting single-quoted substrings."""
        parts: list[str] = []
        start = 0
        in_quote = False
        for i, ch in enumerate(filter_string):
            if ch == "'":
                in_quote = not in_quote
            elif ch == "," and not in_quote:
                parts.append(filter_string[start:i])
                start = i + 1
        parts.append(filter_string[start:])
        return parts

    @classmethod
    def _enforce_zoompan_output_size(cls, filter_string: str, width: int, height: int) -> str:
        """Force any zoompan filter to output the same geometry as the input clip."""
        parts = cls._split_filter_chain(filter_string)
        out_parts: list[str] = []
        for part in parts:
            if "zoompan=" in part:
                if re.search(r":s=\d+x\d+", part):
                    part = re.sub(r":s=\d+x\d+", f":s={width}x{height}", part)
                else:
                    part = f"{part}:s={width}x{height}"
            out_parts.append(part)
        return ",".join(out_parts)

    @staticmethod
    def _sanitize_filter_string(filter_string: str) -> str:
        """
        Best-effort sanitizer for LLM-generated FFmpeg expressions.
        Converts comparison operators (t<3, on>=75, etc.) into FFmpeg expr functions (lt(), gte(), ...),
        which are far more reliably parsed across FFmpeg builds.
        """
        s = filter_string

        patterns: list[tuple[re.Pattern[str], str]] = [
            (re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_]\w*)\s*>=\s*(-?\d+(?:\.\d+)?)"), r"gte(\1,\2)"),
            (re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_]\w*)\s*<=\s*(-?\d+(?:\.\d+)?)"), r"lte(\1,\2)"),
            (re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_]\w*)\s*>\s*(-?\d+(?:\.\d+)?)"), r"gt(\1,\2)"),
            (re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_]\w*)\s*<\s*(-?\d+(?:\.\d+)?)"), r"lt(\1,\2)"),
        ]
        for pat, repl in patterns:
            s = pat.sub(repl, s)

        return s

    def apply_edits(self, input_path, output_path, filter_data):
        """Executes FFmpeg with the generated filter."""

        if not filter_data or "filter_string" not in filter_data:
            print("⚠️ No filter string found. Copying original.")
            subprocess.run(['ffmpeg', '-y', '-i', input_path, '-c', 'copy', output_path])
            return

        filter_string = filter_data["filter_string"]

        try:
            probe_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', input_path]
            res_out = subprocess.check_output(probe_cmd, env={**os.environ, "LANG": "C.UTF-8"}).decode().strip()
            w, h = map(int, res_out.split('x'))
        except Exception as e:
            print(f"⚠️ Could not probe resolution: {e}")
            w, h = None, None

        sanitized = self._sanitize_filter_string(filter_string)
        if sanitized != filter_string:
            print("🧼 Sanitized AI Filter (converted comparisons to lt/lte/gt/gte functions)")
            print(f"🧼 Before: {filter_string}")
            print(f"🧼 After:  {sanitized}")
            filter_string = sanitized

        if w and h:
            enforced = self._enforce_zoompan_output_size(filter_string, w, h)
            if enforced != filter_string:
                print(f"📐 Enforced zoompan output size to {w}x{h}")
                filter_string = enforced

            if "setsar=" not in filter_string:
                filter_string = f"{filter_string},setsar=1"

        print(f"🎬 Executing AI Filter: {filter_string}")

        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vf', filter_string,
            '-c:v', 'libx264',
            '-preset', 'slow', '-crf', '14',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-c:a', 'copy',
            output_path
        ]

        env = os.environ.copy()
        env["LANG"] = "C.UTF-8"
        env["LC_ALL"] = "C.UTF-8"

        try:
            cmd_bytes = []
            for arg in cmd:
                if isinstance(arg, str):
                    cmd_bytes.append(arg.encode('utf-8'))
                else:
                    cmd_bytes.append(arg)

            subprocess.run(cmd_bytes, check=True, env=env)
        except subprocess.CalledProcessError as e:
            print(f"❌ FFmpeg failed: {e}")
            raise e

if __name__ == "__main__":
    pass
