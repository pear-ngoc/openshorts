import time
import math
import cv2
import scenedetect
import subprocess
import argparse
import re
import sys
from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from ultralytics import YOLO
import torch
import os
import numpy as np
from tqdm import tqdm
import yt_dlp
import mediapipe as mp
# import whisper (replaced by faster_whisper inside function)
from google import genai
from google.genai import types
from openai import OpenAI as _OpenAI
from dotenv import load_dotenv
import json
import httpx

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module='google.protobuf')

# Load environment variables FIRST so module-level env reads pick up .env values
load_dotenv()

# ─── FFmpeg encoder detection ────────────────────────────────────────────────
def _detect_nvenc():
    """
    Check two things before enabling hardware encoding:
    1. FFmpeg was compiled with h264_nvenc support.
    2. The CUDA driver (libcuda.so.1) is actually loadable at runtime.
       Even when FFmpeg includes the encoder, it fails at execution if
       no GPU/driver is present (common in containers without a GPU).
    """
    try:
        encoders = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
        ).decode(errors="ignore")
        has_encoder = "h264_nvenc" in encoders
    except Exception:
        has_encoder = False

    if not has_encoder:
        return False

    # Confirm libcuda.so.1 is loadable — avoids "Cannot load libcuda.so.1" at encode time.
    try:
        subprocess.check_output(
            ["ldconfig", "-p"],
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore")
        cuda_libs = subprocess.check_output(
            ["ldconfig", "-p"],
            stderr=subprocess.DEVNULL,
        ).decode(errors="ignore")
        has_cuda = "libcuda.so" in cuda_libs
    except Exception:
        has_cuda = False

    return has_cuda

HAS_NVENC = _detect_nvenc()
print("NVENC available:", HAS_NVENC)

# Chế độ test: chỉ render clip đầu tiên rồi dừng (set IS_TEST_MODE=true trong .env)
IS_TEST_MODE = os.getenv("IS_TEST_MODE", "false").lower() in ("1", "true", "yes")
if IS_TEST_MODE:
    print("⚠️  TEST MODE: chỉ render clip đầu tiên")


def _encoder_args():
    """
    Args cho video encoder chất lượng cao.
    NVENC ưu tiên vì tốc độ + chất lượng; CPU fallback dùng veryslow CRF 18.
    """
    if HAS_NVENC:
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-cq", "18",
            "-rc:v", "vbr",
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryslow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.2",
    ]


def run_cmd(cmd, quiet=False, allow_fail=False):
    """Chạy lệnh FFmpeg, log ra console, raise RuntimeError nếu thất bại."""
    if not quiet:
        print(" ".join(map(str, cmd)))
    try:
        return subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.PIPE if quiet else None,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return e
        stderr = e.stderr[-3000:] if isinstance(e.stderr, str) else e.stderr
        raise RuntimeError(
            f"Command failed:\n{' '.join(map(str, cmd))}\n\n{stderr}"
        ) from e


def cut_clip_30fps(input_video, output_video, start, duration):
    """
    Cắt clip: output seeking (-ss sau -i) cho frame-accurate + re-encode qua
    libx264 baseline để fix partial-file corruption từ input seeking.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-ss", str(start),
        "-t", str(duration),
        "-r", "30",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg cut failed (exit {result.returncode}):\n"
            f"cmd: {' '.join(cmd)}\n\n"
            f"{result.stderr[-5000:]}"
        )
    if not os.path.exists(output_video):
        raise RuntimeError(
            f"FFmpeg returned success but output file was not created: {output_video}\n"
            f"cmd: {' '.join(cmd)}\n\n"
            f"{result.stderr[-5000:]}"
        )
    size = os.path.getsize(output_video)
    if size == 0:
        raise RuntimeError(
            f"FFmpeg output file is 0 bytes: {output_video}\n"
            f"cmd: {' '.join(cmd)}\n\n"
            f"{result.stderr[-5000:]}"
        )

# Notification — gracefully absent if module not available
try:
    import notification_service
except ImportError:
    notification_service = None

# ─── Transcription Provider Config ───────────────────────────────────────
TRANSCRIPTION_PROVIDER = os.getenv("TRANSCRIPTION_PROVIDER", "local").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")
GROQ_BASE_URL = os.getenv("GROQ_TRANSCRIPTION_BASE_URL", "https://api.groq.com/openai/v1")
TRANSCRIPTION_CHUNK_SECONDS = int(os.getenv("TRANSCRIPTION_CHUNK_SECONDS", "600"))
TRANSCRIPTION_STRICT = os.getenv("TRANSCRIPTION_STRICT", "false").lower() in ("1", "true", "yes")

# ─── LLM Provider Config ───────────────────────────────────────────────────
def get_llm_config():
    """
    Returns (provider, api_key, base_url, model) from environment.
    Supports both new LLM_* vars and legacy GEMINI_* vars for backward compat.
    """
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("GEMINI_BASE_URL") or ""
    model = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
    return provider, api_key, base_url, model


def _mask_key(key):
    """Mask API key for safe logging."""
    if not key:
        return "(not set)"
    if len(key) <= 8:
        return key[:3] + "..."
    return key[:4] + "..." + key[-4:]


def _build_gemini_client(api_key, base_url=None):
    """Build a genai.Client, optionally with a custom base URL."""
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["http_options"] = types.HttpOptions(base_url=base_url)
    return genai.Client(**kwargs)


def _is_gemini_quota_error(exception: Exception) -> bool:
    """Detect if an exception is a Gemini quota/rate-limit error."""
    msg = str(exception).lower()
    return any(tag in msg for tag in ['429', 'quota', 'rate limit', 'rate_limit',
                                       'resourceexhausted', 'internal error', 'service unavailable'])

# --- Constants ---
ASPECT_RATIO = 9 / 16
MAX_OUTPUT_WIDTH = 1080
MAX_OUTPUT_HEIGHT = 1920
MIN_CLIP_DURATION_SECONDS = 15.0

def _clamp_clip_range(start, end, video_duration, min_duration=MIN_CLIP_DURATION_SECONDS):
    """
    Normalize LLM clip timestamps to prevent ultra-short or invalid cuts.

    Returns (safe_start, safe_end, note) where note is a short reason for any adjustment.
    """
    note = None

    try:
        safe_start = float(start)
        safe_end = float(end)
    except (TypeError, ValueError):
        safe_start = 0.0
        safe_end = 0.0
        note = "invalid_timestamp_type"

    if video_duration <= 0:
        return 0.0, 0.0, "invalid_video_duration"

    safe_start = max(0.0, min(safe_start, float(video_duration)))
    safe_end = max(0.0, min(safe_end, float(video_duration)))

    if safe_end <= safe_start:
        safe_end = min(float(video_duration), safe_start + min_duration)
        note = note or "end_before_or_equal_start"

    current_duration = safe_end - safe_start
    if current_duration < min_duration:
        expanded_end = min(float(video_duration), safe_start + min_duration)
        if expanded_end - safe_start >= min_duration:
            safe_end = expanded_end
            note = note or f"clip_too_short_expanded_to_{min_duration:.1f}s"
        else:
            safe_start = max(0.0, float(video_duration) - min_duration)
            safe_end = float(video_duration)
            note = note or f"clip_too_short_shifted_to_tail_{min_duration:.1f}s"

    if safe_end <= safe_start:
        safe_start = 0.0
        safe_end = min(float(video_duration), max(min_duration, 1.0))
        note = note or "fallback_reset"

    return round(safe_start, 3), round(safe_end, 3), note

def _make_transcript_fallback_clips(transcript_result: dict, video_duration: float) -> list:
    """
    When Gemini fails (quota/exhausted), extract clips (60-120s) from
    the first speaking segments in the transcript.
    """
    segments = transcript_result.get('segments', [])
    clips = []
    clip_duration_min = 60
    clip_duration_max = 120

    # Collect speaking segments (skip segments with very short/no text)
    speaking_parts = []
    for seg in segments:
        text = seg.get('text', '').strip()
        if len(text) < 5:
            continue
        seg_start = seg.get('start', 0)
        seg_end = seg.get('end', 0)
        duration = seg_end - seg_start
        if duration < 5:
            continue
        speaking_parts.append({'start': seg_start, 'end': seg_end, 'text': text})

    if not speaking_parts:
        return []

    # Try to build clips of 30-60s from consecutive speaking segments
    current_clip_start = speaking_parts[0]['start']
    current_clip_end = speaking_parts[0]['end']

    for seg in speaking_parts[1:]:
        seg_start = seg['start']
        seg_end = seg['end']
        gap = seg_start - current_clip_end

        if gap <= 5.0 and (current_clip_end - current_clip_start) < clip_duration_max:
            # Extend current clip
            current_clip_end = seg_end
        else:
            # Close current clip if it meets minimum
            clip_len = current_clip_end - current_clip_start
            if clip_len >= clip_duration_min:
                clips.append({
                    'start': current_clip_start,
                    'end': current_clip_end,
                    'video_title_for_youtube_short': 'Fallback Clip',
                    'video_description_for_tiktok': 'Generated from transcript (Gemini unavailable)',
                    'video_description_for_instagram': 'Generated from transcript (Gemini unavailable)',
                    'viral_hook_text': '',
                    'fallback_reason': 'gemini_failed'
                })
            # Start new clip
            current_clip_start = seg_start
            current_clip_end = seg_end

    # Don't forget the last clip
    clip_len = current_clip_end - current_clip_start
    if clip_len >= clip_duration_min and len(clips) < 15:
        clips.append({
            'start': current_clip_start,
            'end': current_clip_end,
            'video_title_for_youtube_short': 'Fallback Clip',
            'video_description_for_tiktok': 'Generated from transcript (Gemini unavailable)',
            'video_description_for_instagram': 'Generated from transcript (Gemini unavailable)',
            'viral_hook_text': '',
            'fallback_reason': 'gemini_failed'
        })

    print(f"   📋 Created {len(clips)} fallback clips from transcript speech segments.")
    return clips[:15]  # Cap at 15 clips like Gemini would

GEMINI_PROMPT_TEMPLATE = """
You are a senior short-form video editor. Read the ENTIRE transcript and word-level timestamps to choose the 3–15 MOST VIRAL moments for TikTok/IG Reels/YouTube Shorts. Each clip must be between 60 and 120 seconds long.

⚠️ FFMPEG TIME CONTRACT — STRICT REQUIREMENTS:
- Return timestamps in ABSOLUTE SECONDS from the start of the video (usable in: ffmpeg -ss <start> -to <end> -i <input> ...).
- Only NUMBERS with decimal point, up to 3 decimals (examples: 0, 1.250, 17.350).
- Ensure 0 ≤ start < end ≤ VIDEO_DURATION_SECONDS.
- Each clip between 60 and 120 s (inclusive).
- Prefer starting 0.2–0.4 s BEFORE the hook and ending 0.2–0.4 s AFTER the payoff.
- Use silence moments for natural cuts; never cut in the middle of a word or phrase.
- STRICTLY FORBIDDEN to use time formats other than absolute seconds.

VIDEO_DURATION_SECONDS: {video_duration}

TRANSCRIPT_TEXT (raw):
{transcript_text}

WORDS_JSON (array of {{w, s, e}} where s/e are seconds):
{words_json}

STRICT EXCLUSIONS:
- No generic intros/outros or purely sponsorship segments unless they contain the hook.
- No clips < 60 s or > 120 s.

OUTPUT — RETURN ONLY VALID JSON (no markdown, no comments). Order clips by predicted performance (best to worst). In the descriptions, ALWAYS include a CTA like "Follow me and comment X and I'll send you the workflow" (especially if discussing an n8n workflow):
{{
  "shorts": [
    {{
      "start": <number in seconds, e.g., 12.340>,
      "end": <number in seconds, e.g., 37.900>,
      "video_description_for_tiktok": "<description for TikTok oriented to get views>",
      "video_description_for_instagram": "<description for Instagram oriented to get views>",
      "video_title_for_youtube_short": "<title for YouTube Short oriented to get views 100 chars max>",
      "viral_hook_text": "<SHORT punchy text overlay (max 10 words). MUST BE IN THE SAME LANGUAGE AS THE VIDEO TRANSCRIPT. Examples: 'POV: You realized...', 'Did you know?', 'Stop doing this!'>"
    }}
  ]
}}
"""

# Load the YOLO model once (Keep for backup or scene analysis if needed)
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "/app/models/yolov8n.pt")
model = YOLO(YOLO_MODEL_PATH)

# --- MediaPipe Setup ---
# Use standard Face Detection (BlazeFace) for speed
mp_face_detection = mp.solutions.face_detection
face_detection = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)

class SmoothedCameraman:
    """
    Handles smooth camera movement.
    Simplified Logic: "Heavy Tripod"
    Only moves if the subject leaves the center safe zone.
    Moves slowly and linearly.
    """
    def __init__(self, output_width, output_height, video_width, video_height):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        
        # Initial State
        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2
        
        # Calculate crop dimensions (based on source resolution)
        # crop always matches source height, width is constrained to 9:16
        self.crop_height = video_height
        self.crop_width = int(self.crop_height * ASPECT_RATIO)
        if self.crop_width > video_width:
             self.crop_width = video_width
             self.crop_height = int(self.crop_width / ASPECT_RATIO)
             
        # Safe Zone: 20% of the video width
        # As long as the target is within this zone relative to current center, DO NOT MOVE.
        self.safe_zone_radius = self.crop_width * 0.25

    def update_target(self, face_box):
        """
        Updates the target center based on detected face/person.
        """
        if face_box:
            x, y, w, h = face_box
            self.target_center_x = x + w / 2
    
    def get_crop_box(self, force_snap=False):
        """
        Returns the (x1, y1, x2, y2) for the current frame.
        """
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x
            
            # SIMPLIFIED LOGIC:
            # 1. Is the target outside the safe zone?
            if abs(diff) > self.safe_zone_radius:
                # 2. If yes, move towards it slowly (Linear Speed)
                # Determine direction
                direction = 1 if diff > 0 else -1
                
                # Speed: 2 pixels per frame (Slow pan)
                # If the distance is HUGE (scene change or fast movement), speed up slightly
                if abs(diff) > self.crop_width * 0.5:
                    speed = 15.0 # Fast re-frame
                else:
                    speed = 3.0  # Slow, steady pan
                
                self.current_center_x += direction * speed
                
                # Check if we overshot (prevent oscillation)
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x
            
            # If inside safe zone, DO NOTHING (Stationary Camera)
                
        # Clamp center
        half_crop = self.crop_width / 2
        
        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop
            
        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)
        
        x1 = max(0, x1)
        x2 = min(self.video_width, x2)
        
        y1 = 0
        y2 = self.video_height
        
        return x1, y1, x2, y2

class SpeakerTracker:
    """
    Tracks speakers over time to prevent rapid switching and handle temporary obstructions.
    """
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}  # {id: score}
        self.last_seen = {}       # {id: frame_number}
        self.locked_counter = 0   # How long we've been locked on current speaker
        
        # Hyperparameters
        self.stabilization_threshold = stabilization_frames # Frames needed to confirm a new speaker
        self.switch_cooldown = cooldown_frames              # Minimum frames before switching again
        self.last_switch_frame = -1000
        
        # ID tracking
        self.next_id = 0
        self.known_faces = [] # [{'id': 0, 'center': x, 'last_frame': 123}]

    def get_target(self, face_candidates, frame_number, width):
        """
        Decides which face to focus on.
        face_candidates: list of {'box': [x,y,w,h], 'score': float}
        """
        current_candidates = []
        
        # 1. Match faces to known IDs (simple distance tracking)
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2
            
            best_match_id = -1
            min_dist = width * 0.15 # Reduced matching radius to avoid jumping in groups
            
            # Try to match with known faces seen recently
            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30: # Forgot faces older than 1s (was 2s)
                    continue
                    
                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']
            
            # If no match, assign new ID
            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1
            
            # Update known face
            self.known_faces = [kf for kf in self.known_faces if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x, 'last_frame': frame_number})
            
            current_candidates.append({
                'id': best_match_id,
                'box': face['box'],
                'score': face['score']
            })

        # 2. Update Scores with decay
        for pid in list(self.speaker_scores.keys()):
             self.speaker_scores[pid] *= 0.85 # Faster decay (was 0.9)
             if self.speaker_scores[pid] < 0.1:
                 del self.speaker_scores[pid]

        # Add new scores
        for cand in current_candidates:
            pid = cand['id']
            # Score is purely based on size (proximity) now that we don't have mouth
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        # 3. Determine Best Speaker
        if not current_candidates:
            # If no one found, maintain last active speaker if cooldown allows
            # to avoid black screen or jump to 0,0
            return None 
            
        best_candidate = None
        max_score = -1
        
        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)
            
            # Hysteresis: HUGE Bonus for current active speaker
            if pid == self.active_speaker_id:
                total_score *= 3.0 # Sticky factor
                
            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        # 4. Decide Switch
        if best_candidate:
            target_id = best_candidate['id']
            
            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']
            
            # New person
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates if c['id'] == self.active_speaker_id), None)
                if old_cand:
                    return old_cand['box']
            
            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']
            
        return None

def detect_face_candidates(frame):
    """
    Returns list of all detected faces using lightweight FaceDetection.
    """
    height, width, _ = frame.shape
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_detection.process(rgb_frame)
    
    candidates = []
    
    if not results.detections:
        return []
        
    for detection in results.detections:
        bboxC = detection.location_data.relative_bounding_box
        x = int(bboxC.xmin * width)
        y = int(bboxC.ymin * height)
        w = int(bboxC.width * width)
        h = int(bboxC.height * height)
        
        candidates.append({
            'box': [x, y, w, h],
            'score': w * h # Area as score
        })
            
    return candidates

def detect_person_yolo(frame):
    """
    Fallback: Detect largest person using YOLO when face detection fails.
    Returns [x, y, w, h] of the person's 'upper body' approximation.
    """
    # Use the globally loaded model
    results = model(frame, verbose=False, classes=[0]) # class 0 is person
    
    if not results:
        return None
        
    best_box = None
    max_area = 0
    
    for result in results:
        boxes = result.boxes
        for box in boxes:
            x1, y1, x2, y2 = [int(i) for i in box.xyxy[0]]
            w = x2 - x1
            h = y2 - y1
            area = w * h
            
            if area > max_area:
                max_area = area
                # Focus on the top 40% of the person (head/chest) for framing
                # This approximates where the face is if we can't detect it directly
                face_h = int(h * 0.4)
                best_box = [x1, y1, w, face_h]
                
    return best_box

def create_general_frame(frame, output_width, output_height):
    """
    Creates a 'General Shot' frame: 
    - Background: Blurred zoom of original
    - Foreground: Original video scaled to fit width, centered vertically.
    """
    orig_h, orig_w = frame.shape[:2]
    
    # 1. Background (Fill Height)
    # Crop center to aspect ratio
    bg_scale = output_height / orig_h
    bg_w = int(orig_w * bg_scale)
    bg_resized = cv2.resize(frame, (bg_w, output_height),
                            interpolation=cv2.INTER_LANCZOS4)
    
    # Crop center of background
    start_x = (bg_w - output_width) // 2
    if start_x < 0: start_x = 0
    background = bg_resized[:, start_x:start_x+output_width]
    if background.shape[1] != output_width:
        background = cv2.resize(background, (output_width, output_height),
                                interpolation=cv2.INTER_LANCZOS4)
        
    # Blur background
    background = cv2.GaussianBlur(background, (51, 51), 0)
    
    # 2. Foreground (Fit Width)
    scale = output_width / orig_w
    fg_h = int(orig_h * scale)
    foreground = cv2.resize(frame, (output_width, fg_h),
                            interpolation=cv2.INTER_LANCZOS4)
    foreground = unsharp_mask(foreground)
    # 3. Overlay
    y_offset = (output_height - fg_h) // 2
    
    # Clone background to avoid modifying it
    final_frame = background.copy()
    final_frame[y_offset:y_offset+fg_h, :] = foreground
    return final_frame

def unsharp_mask(frame, amount=1.5, threshold=0):
    """
    Unsharp mask mạnh hơn Laplacian kernel cũ.
    Bù độ mờ từ downscale/crop để giữ nét chi tiết.
    amount=1.5–2.0 là mạnh, threshold=0 áp dụng cho mọi edge difference.
    """
    blurred = cv2.GaussianBlur(frame, (0, 0), 3)
    lowcontrast = cv2.addWeighted(frame, 1.0, blurred, -1.0, 0)
    return cv2.addWeighted(frame, 1.0, lowcontrast, amount, 0)

def analyze_scenes_strategy(video_path, scenes):
    """
    Analyzes each scene to determine if it should be TRACK (Single person) or GENERAL (Group/Wide).
    Returns list of strategies corresponding to scenes.
    """
    cap = cv2.VideoCapture(video_path)
    strategies = []
    
    if not cap.isOpened():
        return ['TRACK'] * len(scenes)
        
    for start, end in tqdm(scenes, desc="   Analyzing Scenes"):
        # Sample 3 frames (start, middle, end)
        frames_to_check = [
            start.get_frames() + 5,
            int((start.get_frames() + end.get_frames()) / 2),
            end.get_frames() - 5
        ]
        
        face_counts = []
        for f_idx in frames_to_check:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret: continue
            
            # Detect faces
            candidates = detect_face_candidates(frame)
            face_counts.append(len(candidates))
            
        # Decision Logic
        if not face_counts:
            avg_faces = 0
        else:
            avg_faces = sum(face_counts) / len(face_counts)
            
        # Strategy:
        # 0 faces -> GENERAL (Landscape/B-roll)
        # 1 face -> TRACK
        # > 1.2 faces -> GENERAL (Group)
        
        if avg_faces > 2.5:
            strategies.append('GENERAL')
        else:
            strategies.append('TRACK')
            
    cap.release()
    return strategies

def detect_scenes(video_path):
    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector())
    scene_manager.detect_scenes(video=video)
    scene_list = scene_manager.get_scene_list()
    fps = video.frame_rate
    return scene_list, fps

def get_video_resolution(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video file {video_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return width, height


def sanitize_filename(filename):
    """Remove invalid characters from filename."""
    filename = re.sub(r'[<>:"/\\|?*#]', '', filename)
    filename = filename.replace(' ', '_')
    return filename[:100]


def download_youtube_video(url, output_dir="."):
    """
    Downloads a YouTube video. Routes through the youtube_downloader service
    which manages multiple backends (ytsave, yt-dlp, etc.).
    Returns (file_path, title) for backward compatibility.
    """
    step_start_time = time.time()

    from services.youtube_downloader import download_youtube
    result = download_youtube(url, output_dir)

    downloaded_file = result.file_path
    sanitized_title = result.title

    step_end_time = time.time()
    print(f"✅ Video downloaded in {step_end_time - step_start_time:.2f}s: {downloaded_file}")

    try:
        w, h = get_video_resolution(downloaded_file)
        print(f"📐 Downloaded/input resolution: {w}x{h}")
    except Exception as e:
        print(f"⚠️ Could not read downloaded video resolution: {e}")

    return downloaded_file, sanitized_title

def process_video_to_vertical(input_video, final_output_video, temp_video_output_path=None):
    """
    Core logic to convert horizontal video to vertical using scene detection and Active Speaker Tracking (MediaPipe).
    """
    script_start_time = time.time()

    # Define temporary file paths based on the output name.
    # Use temp_ prefix so uploaders skip these intermediates.
    out_dir = os.path.dirname(final_output_video) or "."
    out_stem = os.path.splitext(os.path.basename(final_output_video))[0]
    _temp_video_output = temp_video_output_path or os.path.join(out_dir, f"temp_{out_stem}.mp4")
    temp_audio_output = os.path.join(out_dir, f"temp_{out_stem}.aac")

    # Clean up previous temp files if they exist (don't delete the input!)
    if _temp_video_output != input_video and os.path.exists(_temp_video_output):
        os.remove(_temp_video_output)
    if os.path.exists(temp_audio_output):
        os.remove(temp_audio_output)
    if os.path.exists(final_output_video):
        os.remove(final_output_video)

    print(f"🎬 Processing clip: {input_video}")
    if not os.path.exists(input_video):
        raise FileNotFoundError(f"Video file does not exist: {input_video}")
    file_size = os.path.getsize(input_video)
    print(f"   File exists: {file_size / (1024*1024):.1f} MB")
    print("   Step 1: Detecting scenes...")
    try:
        scenes, fps = detect_scenes(input_video)
    except OSError as e:
        if "not found" in str(e).lower() or "not exist" in str(e).lower():
            raise FileNotFoundError(f"scenedetect cannot read video (file may be corrupt): {input_video} ({file_size} bytes)") from e
        raise
    
    if not scenes:
        print("   ❌ No scenes were detected. Using full video as one scene.")
        # If scene detection fails or finds nothing, treat whole video as one scene
        cap = cv2.VideoCapture(input_video)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        from scenedetect import FrameTimecode
        scenes = [(FrameTimecode(0, fps), FrameTimecode(total_frames, fps))]

    print(f"   ✅ Found {len(scenes)} scenes.")

    print("\n   🧠 Step 2: Preparing Active Tracking...")
    original_width, original_height = get_video_resolution(input_video)
    print(f"   📐 Input resolution: {original_width}x{original_height}")
    
    # Target the largest possible 9:16 output, capped at MAX values.
    # If source >= MAX, use source height as-is (no unnecessary downscale).
    # If source < MAX, upscale to MAX using high-quality LANCZOS4.
    # OUTPUT_HEIGHT = min(original_height, MAX_OUTPUT_HEIGHT)
    # OUTPUT_WIDTH = min(int(OUTPUT_HEIGHT * ASPECT_RATIO), MAX_OUTPUT_WIDTH)
    # if OUTPUT_WIDTH % 2 != 0:
    #     OUTPUT_WIDTH += 1
    OUTPUT_WIDTH = 1080
    OUTPUT_HEIGHT = 1920

    # Track whether we need to upscale (for logging)
    need_upscale = OUTPUT_HEIGHT > original_height
    if need_upscale:
        print(f"   ℹ️  Source {original_width}x{original_height} — will upscale to {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")
    else:
        print(f"   ℹ️  Output resolution: {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")

    # Initialize Cameraman
    cameraman = SmoothedCameraman(OUTPUT_WIDTH, OUTPUT_HEIGHT, original_width, original_height)
    
    # --- New Strategy: Per-Scene Analysis ---
    print("\n   🤖 Step 3: Analyzing Scenes for Strategy (Single vs Group)...")
    scene_strategies = analyze_scenes_strategy(input_video, scenes)
    # scene_strategies is a list of 'TRACK' or 'General' corresponding to scenes
    
    print("\n   ✂️ Step 4: Processing video frames...")
    encoder_label = "h264_nvenc/p4/CQ18/VBR" if HAS_NVENC else "libx264/veryslow/CRF18"
    print(f"   🎯 Encoding: {encoder_label}/yuv420p/faststart")

    # command = [
    #     'ffmpeg', '-y', '-f', 'rawvideo', '-vcodec', 'rawvideo',
    #     '-s', f'{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}', '-pix_fmt', 'bgr24',
    #     '-r', str(fps), '-i', '-', '-c:v', 'libx264',
    #     '-preset', 'slow', '-crf', '14',
    #     '-pix_fmt', 'yuv420p',
    #     '-movflags', '+faststart',
    #     '-an', _temp_video_output
    # ]
    # IMPORTANT: ffmpeg prints periodic progress/stats to stderr by default.
    # We pipe raw frames to ffmpeg via stdin; if stderr is too chatty and not
    # drained concurrently, the OS pipe buffer can fill and block the encode.
    # Keep stderr minimal to avoid apparent "stuck at 0%" hangs.
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostats', '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}',
        '-pix_fmt', 'bgr24',
        '-r', str(fps),
        '-i', '-',
        *_encoder_args(),
        '-movflags', '+faststart',
        '-an',
        _temp_video_output
    ]

    ffmpeg_process = None
    cap = None
    try:
        ffmpeg_process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        cap = cv2.VideoCapture(input_video)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        frame_number = 0
        current_scene_index = 0

        # Pre-calculate scene boundaries
        scene_boundaries = []
        for s_start, s_end in scenes:
            # SceneDetect FrameTimecode: get_frames() is deprecated
            scene_boundaries.append((s_start.frame_num, s_end.frame_num))

        # Global tracker for single-person shots
        speaker_tracker = SpeakerTracker(cooldown_frames=30)

        with tqdm(total=total_frames, desc="   Processing", file=sys.stdout) as pbar:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                # Update Scene Index
                if current_scene_index < len(scene_boundaries):
                    start_f, end_f = scene_boundaries[current_scene_index]
                    if frame_number >= end_f and current_scene_index < len(scene_boundaries) - 1:
                        current_scene_index += 1

                # Determine Strategy for current frame based on scene
                current_strategy = scene_strategies[current_scene_index] if current_scene_index < len(scene_strategies) else 'TRACK'

                # Apply Strategy
                if current_strategy == 'GENERAL':
                    # "Plano General" -> Blur Background + Fit Width
                    output_frame = create_general_frame(frame, OUTPUT_WIDTH, OUTPUT_HEIGHT)

                    # Debug log first GENERAL frame
                    if frame_number == 0:
                        print(f"   📐 GENERAL frame: source={frame.shape[1]}x{frame.shape[0]} composite={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")

                    # Reset cameraman/tracker so they don't drift while inactive
                    cameraman.current_center_x = original_width / 2
                    cameraman.target_center_x = original_width / 2

                else:
                    # "Single Speaker" -> Track & Crop

                    # Detect every 2nd frame for performance
                    if frame_number % 2 == 0:
                        candidates = detect_face_candidates(frame)
                        target_box = speaker_tracker.get_target(candidates, frame_number, original_width)
                        if target_box:
                            cameraman.update_target(target_box)
                        else:
                            person_box = detect_person_yolo(frame)
                            if person_box:
                                cameraman.update_target(person_box)

                    # Snap camera on scene change to avoid panning from previous scene position
                    is_scene_start = (frame_number == scene_boundaries[current_scene_index][0])

                    x1, y1, x2, y2 = cameraman.get_crop_box(force_snap=is_scene_start)

                    # Crop
                    if y2 > y1 and x2 > x1:
                        cropped = frame[y1:y2, x1:x2]
                        output_frame = cv2.resize(
                            cropped,
                            (OUTPUT_WIDTH, OUTPUT_HEIGHT),
                            interpolation=cv2.INTER_LANCZOS4
                        )
                        output_frame = unsharp_mask(output_frame)
                        if frame_number == 0:
                            print(f"   📐 TRACK frame: source={frame.shape[1]}x{frame.shape[0]} crop={x2-x1}x{y2-y1} output={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")
                    else:
                        output_frame = cv2.resize(
                            frame,
                            (OUTPUT_WIDTH, OUTPUT_HEIGHT),
                            interpolation=cv2.INTER_LANCZOS4
                        )
                        output_frame = unsharp_mask(output_frame)
                        if frame_number == 0:
                            print(f"   📐 FALLBACK frame: source={frame.shape[1]}x{frame.shape[0]} output={OUTPUT_WIDTH}x{OUTPUT_HEIGHT}")

                ffmpeg_process.stdin.write(output_frame.tobytes())
                frame_number += 1
                pbar.update(1)

        ffmpeg_process.stdin.close()
        stderr_output = ffmpeg_process.stderr.read().decode()
        ffmpeg_process.wait()
        cap.release()

        if ffmpeg_process.returncode != 0:
            print("\n   ❌ FFmpeg frame processing failed.")
            print("   Stderr:", stderr_output)
            return False

        if not os.path.exists(_temp_video_output):
            print(f"\n   ❌ FFmpeg output file was not created: {_temp_video_output}")
            print("   Stderr:", stderr_output)
            return False

        # Verify output frame count matches input
        cap_check = cv2.VideoCapture(_temp_video_output)
        output_frame_count = int(cap_check.get(cv2.CAP_PROP_FRAME_COUNT))
        cap_check.release()
        if output_frame_count == 0:
            print(f"\n   ❌ FFmpeg output has 0 frames. Stderr: {stderr_output[-2000:]}")
            return False
        if output_frame_count < total_frames * 0.9:
            print(f"   ⚠️  Output frames ({output_frame_count}) < 90% of input frames ({total_frames}) — input may be corrupted")

        # Verify output resolution
        out_w, out_h = get_video_resolution(_temp_video_output)
        print(f"   ✅ Intermediate video: {out_w}x{out_h}")
        if out_w != OUTPUT_WIDTH or out_h != OUTPUT_HEIGHT:
            print(f"   ⚠️  Output resolution mismatch! Expected {OUTPUT_WIDTH}x{OUTPUT_HEIGHT}, got {out_w}x{out_h}")

        print("\n   🔊 Step 5: Extracting audio...")
        audio_extract_command = [
            'ffmpeg', '-y', '-i', input_video, '-vn',
            '-acodec', 'aac', '-b:a', '192k',
            temp_audio_output
        ]
        try:
            subprocess.run(audio_extract_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            pass

        if os.path.exists(temp_audio_output):
            merge_command = [
                'ffmpeg', '-y', '-i', _temp_video_output, '-i', temp_audio_output,
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart',
                final_output_video
            ]
        else:
            merge_command = [
                'ffmpeg', '-y', '-i', _temp_video_output,
                '-c:v', 'copy',
                '-movflags', '+faststart',
                final_output_video
            ]

        try:
            subprocess.run(merge_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            return False

        # Verify final output
        final_cap = cv2.VideoCapture(final_output_video)
        final_frame_count = int(final_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        final_fps = final_cap.get(cv2.CAP_PROP_FPS)
        final_cap.release()
        final_w, final_h = get_video_resolution(final_output_video)
        final_duration = final_frame_count / final_fps if final_fps > 0 else 0
        print(f"   ✅ Final output: {final_w}x{final_h}, {final_frame_count} frames, {final_duration:.1f}s @ {final_fps}fps")
        if final_duration < 5:
            print(f"   ❌ Final video is suspiciously short ({final_duration:.1f}s). The input clip may be corrupted.")

        return True

    finally:
        # Always attempt to clean up temp intermediates (even on failure/interrupt)
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        try:
            if ffmpeg_process is not None and ffmpeg_process.stdin and not ffmpeg_process.stdin.closed:
                ffmpeg_process.stdin.close()
        except Exception:
            pass
        try:
            if os.path.exists(_temp_video_output):
                os.remove(_temp_video_output)
        except Exception:
            pass
        try:
            if os.path.exists(temp_audio_output):
                os.remove(temp_audio_output)
        except Exception:
            pass

def extract_audio_for_transcription(video_path: str, output_audio_path: str) -> bool:
    """Extract audio from video for transcription (16kHz mono MP3)."""
    import subprocess
    cmd = [
        'ffmpeg', '-y', '-i', video_path,
        '-vn', '-ac', '1', '-ar', '16000',
        '-b:a', '64k', output_audio_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ⚠️ Audio extraction failed: {e.stderr.decode()}")
        return False


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    import subprocess
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        audio_path
    ]
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        return float(result)
    except Exception:
        return 0.0


def transcribe_with_groq(audio_path: str) -> list[dict]:
    """Transcribe audio using Groq Whisper API with chunking support."""
    import time
    from openai import OpenAI

    chunk_duration = TRANSCRIPTION_CHUNK_SECONDS
    total_duration = _get_audio_duration(audio_path)
    model_name = GROQ_MODEL

    print(f"🎙️  Transcribing with Groq Whisper...")
    print(f"   model: {model_name}")
    print(f"   audio duration: {total_duration:.1f}s")

    if total_duration == 0:
        raise RuntimeError("Could not determine audio duration")

    # Single chunk: no splitting needed
    if total_duration <= chunk_duration:
        return _transcribe_groq_chunk(audio_path, chunk_offset=0.0, chunk_index=0)

    # Multi-chunk: split audio into parts
    chunk_files = []
    num_chunks = int(math.ceil(total_duration / chunk_duration))
    print(f"   audio > {chunk_duration}s → splitting into {num_chunks} chunks")

    for i in range(num_chunks):
        start_sec = i * chunk_duration
        chunk_path = audio_path.replace('.mp3', f'.chunk_{i:03d}.mp3')
        cmd = [
            'ffmpeg', '-y', '-i', audio_path,
            '-ss', str(start_sec),
            '-t', str(chunk_duration),
            '-vn', '-ac', '1', '-ar', '16000', '-b:a', '64k',
            chunk_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        chunk_files.append((chunk_path, start_sec))
        print(f"   chunk {i+1}/{num_chunks}: {start_sec:.1f}s - {start_sec + chunk_duration:.1f}s")

    all_segments = []
    start_time = time.time()

    for idx, (chunk_path, offset) in enumerate(chunk_files):
        segments = _transcribe_groq_chunk(chunk_path, chunk_offset=offset, chunk_index=idx)
        all_segments.extend(segments)
        # Cleanup chunk file
        try:
            os.remove(chunk_path)
        except OSError:
            pass

    elapsed = time.time() - start_time
    print(f"   Groq transcription done in {elapsed:.1f}s — {len(all_segments)} segments")
    return all_segments


def _transcribe_groq_chunk(audio_path: str, chunk_offset: float, chunk_index: int) -> list[dict]:
    """Transcribe a single audio chunk via Groq Whisper API."""
    import time
    from openai import OpenAI

    start_time = time.time()
    client = OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL)

    with open(audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model=GROQ_MODEL,
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    elapsed = time.time() - start_time
    print(f"   [{chunk_index}] Groq chunk transcribed in {elapsed:.1f}s")

    # Handle both OpenAI response object and raw dict
    data = response
    if hasattr(response, 'model_dump'):
        data = response.model_dump()
    elif hasattr(response, 'dict'):
        data = response.dict()

    segments = []
    raw_segments = data.get('segments', [])
    for seg in raw_segments:
        start = seg.get('start', 0) + chunk_offset
        end = seg.get('end', 0) + chunk_offset
        text = seg.get('text', '').strip()
        segments.append({'start': start, 'end': end, 'text': text})

    return segments


def transcribe_with_local_whisper(audio_path: str) -> list[dict]:
    """Transcribe audio using Faster-Whisper (local CPU)."""
    from faster_whisper import WhisperModel

    print("🎙️  Transcribing with Faster-Whisper (CPU Optimized)...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(audio_path, word_timestamps=True)

    print(f"   Detected language '{info.language}' with probability {info.language_probability:.2f}")

    result = []
    for seg in segments:
        print(f"   [{seg.start:.2f}s -> {seg.end:.2f}s] {seg.text}")
        seg_dict = {
            'text': seg.text,
            'start': seg.start,
            'end': seg.end,
            'words': []
        }
        if seg.words:
            for word in seg.words:
                seg_dict['words'].append({
                    'word': word.word,
                    'start': word.start,
                    'end': word.end,
                    'probability': word.probability
                })
        result.append(seg_dict)

    return result


def transcribe_audio(audio_path: str) -> list[dict]:
    """
    Unified transcription entry point.
    Selects provider based on TRANSCRIPTION_PROVIDER env var and falls back on error.

    Returns list of dicts with keys: start, end, text  (and optionally 'words').
    """
    provider = TRANSCRIPTION_PROVIDER
    groq_key = os.getenv("GROQ_API_KEY", "")

    if provider == "groq":
        if not groq_key:
            print("⚠️  TRANSCRIPTION_PROVIDER=groq but GROQ_API_KEY is not set. Falling back to local Faster-Whisper.")
        else:
            try:
                return transcribe_with_groq(audio_path)
            except Exception as e:
                print(f"❌ Groq transcription failed: {e}")
                if TRANSCRIPTION_STRICT:
                    raise RuntimeError(f"Groq transcription failed in strict mode: {e}") from e
                print("🔄 Falling back to local Faster-Whisper...")

    # Default: local Faster-Whisper
    return transcribe_with_local_whisper(audio_path)


def transcribe_video(video_path: str) -> dict:
    """
    Full transcription pipeline for a video file.
    Returns dict: {text, segments, language}
    - segments: list of {start, end, text, words?} — compatible with Gemini and downstream.
    """
    import math

    audio_path = video_path + ".transcribe.mp3"

    if not extract_audio_for_transcription(video_path, audio_path):
        # Fallback: try transcribing the video directly (some formats work)
        audio_path = video_path

    try:
        transcript_segments = transcribe_audio(audio_path)
    finally:
        # Cleanup temp audio unless it was the original video
        if audio_path != video_path and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except OSError:
                pass

    full_text = " ".join(seg['text'] for seg in transcript_segments)

    # Detect language from first segment's word timings (or default)
    language = "en"
    for seg in transcript_segments:
        if seg.get('words'):
            language = "en"
            break

    return {
        'text': full_text,
        'segments': transcript_segments,
        'language': language,
    }

def _analyze_with_gemini_native(prompt, api_key, base_url, model_name):
    """Call Gemini using the native google-genai SDK."""
    client = _build_gemini_client(api_key, base_url)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt
    )

    # Cost calculation for Gemini
    cost_analysis = None
    try:
        usage = response.usage_metadata
        if usage:
            input_price_per_million = 0.10
            output_price_per_million = 0.40
            prompt_tokens = usage.prompt_token_count
            output_tokens = usage.candidates_token_count
            input_cost = (prompt_tokens / 1_000_000) * input_price_per_million
            output_cost = (output_tokens / 1_000_000) * output_price_per_million
            total_cost = input_cost + output_cost
            cost_analysis = {
                "input_tokens": prompt_tokens,
                "output_tokens": output_tokens,
                "input_cost": input_cost,
                "output_cost": output_cost,
                "total_cost": total_cost,
                "model": model_name,
                "provider": "gemini"
            }
            print(f"💰 Token Usage ({model_name}):")
            print(f"   - Input Tokens: {prompt_tokens} (${input_cost:.6f})")
            print(f"   - Output Tokens: {output_tokens} (${output_cost:.6f})")
            print(f"   - Total Estimated Cost: ${total_cost:.6f}")
    except Exception as e:
        print(f"⚠️ Could not calculate cost: {e}")

    text = response.text
    return text, cost_analysis


def _analyze_with_openai_compatible(prompt, api_key, base_url, model_name):
    """Call LLM via OpenAI-compatible /v1/chat/completions endpoint."""
    if not base_url:
        raise ValueError("LLM_PROVIDER=openai_compatible requires LLM_BASE_URL to be set.")

    client = _OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "You are an expert short-form video editor. Return only valid JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    text = response.choices[0].message.content or ""
    return text, None  # No cost tracking for OpenAI-compatible (would need provider-specific parsing)


def _parse_llm_response(text, cost_analysis=None):
    """Strip markdown code fences and parse JSON from LLM response."""
    cleaned = text
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        result = json.loads(cleaned)
        if cost_analysis:
            result["cost_analysis"] = cost_analysis
        return result
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse LLM response as JSON: {e}")
        print(f"   Raw response (first 2000 chars): {text[:2000]}")
        raise


def get_viral_clips(transcript_result, video_duration):
    """
    Unified LLM entry point — dispatches to the configured provider
    (gemini or openai_compatible) based on LLM_PROVIDER env var.
    """
    provider, api_key, base_url, model_name = get_llm_config()

    if not api_key:
        print("❌ Error: Missing LLM_API_KEY / GEMINI_API_KEY.")
        return None

    # Normalise provider name
    is_gemini = provider in ("gemini", "gemini_native", "")
    is_openai = provider in ("openai", "openai_compatible", "openai-compatible")

    if is_gemini:
        print(f"🤖  Analyzing with Gemini Native LLM...")
        print(f"   provider: gemini")
        print(f"   model: {model_name}")
        print(f"   api_key: {_mask_key(api_key)}")
        if base_url:
            print(f"   base_url: {base_url}")
    elif is_openai:
        print(f"🤖  Analyzing with OpenAI-compatible LLM...")
        print(f"   provider: openai_compatible")
        print(f"   model: {model_name}")
        print(f"   api_key: {_mask_key(api_key)}")
        print(f"   base_url: {base_url}")
    else:
        print(f"⚠️  Unknown LLM_PROVIDER='{provider}', defaulting to gemini.")
        is_gemini = True

    # Extract words
    words = []
    for segment in transcript_result['segments']:
        for word in segment.get('words', []):
            words.append({
                'w': word['word'],
                's': word['start'],
                'e': word['end']
            })

    prompt = GEMINI_PROMPT_TEMPLATE.format(
        video_duration=video_duration,
        transcript_text=json.dumps(transcript_result['text']),
        words_json=json.dumps(words)
    )

    try:
        if is_gemini:
            text, cost_analysis = _analyze_with_gemini_native(prompt, api_key, base_url, model_name)
        else:
            text, cost_analysis = _analyze_with_openai_compatible(prompt, api_key, base_url, model_name)

        return _parse_llm_response(text, cost_analysis)

    except Exception as e:
        if _is_gemini_quota_error(e):
            print(f"❌ LLM quota exhausted (429/Rate-Limit): {e}")
            print("⚠️  LLM identify clips failed — returning quota_exhausted flag.")
            print("   The job will fall back to short segments from transcript instead of the whole video.")
            return {"shorts": [], "gemini_quota_exhausted": True}
        print(f"❌ LLM Error (provider={provider}): {e}")
        return None

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="AutoCrop-Vertical with Viral Clip Detection.")
    
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('-i', '--input', type=str, help="Path to the input video file.")
    input_group.add_argument('-u', '--url', type=str, help="YouTube URL to download and process.")
    
    parser.add_argument('-o', '--output', type=str, help="Output directory or file (if processing whole video).")
    parser.add_argument('--keep-original', action='store_true', help="Keep the downloaded YouTube video.")
    parser.add_argument('--skip-analysis', action='store_true', help="Skip AI analysis and convert the whole video.")
    
    args = parser.parse_args()

    script_start_time = time.time()
    
    def _ensure_dir(path: str) -> str:
        """Create directory if missing and return the same path."""
        if path:
            os.makedirs(path, exist_ok=True)
        return path
    
    # 1. Get Input Video
    if args.url:
        # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
        # For whole-video runs (--skip-analysis), --output can be a file path.
        if args.output and not args.skip_analysis:
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default "."
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or "."
            else:
                output_dir = "."
        
        input_video, video_title = download_youtube_video(args.url, output_dir)
    else:
        input_video = args.input
        video_title = os.path.splitext(os.path.basename(input_video))[0]
        
        if args.output and not args.skip_analysis:
            # For multi-clip runs, treat --output as an OUTPUT DIRECTORY (create it if needed).
            output_dir = _ensure_dir(args.output)
        else:
            # If output is a directory, use it; if it's a filename, use its directory; else default to input dir.
            if args.output and os.path.isdir(args.output):
                output_dir = args.output
            elif args.output and not os.path.isdir(args.output):
                output_dir = os.path.dirname(args.output) or os.path.dirname(input_video)
            else:
                output_dir = os.path.dirname(input_video)

    if not os.path.exists(input_video):
        print(f"❌ Input file not found: {input_video}")
        exit(1)

    # 2. Decision: Analyze clips or process whole?
    if args.skip_analysis:
        print("⏩ Skipping analysis, processing entire video...")
        output_file = args.output if args.output else os.path.join(output_dir, f"{video_title}_vertical.mp4")
        success = process_video_to_vertical(input_video, output_file)
        if success:
            # Write a minimal metadata so app.py can find and serve the single clip.
            job_id_from_dir = os.path.basename(output_dir) if output_dir != "." else video_title
            metadata_basename = f"{job_id_from_dir}_metadata"
            metadata_file = os.path.join(output_dir, f"{metadata_basename}.json")
            metadata = {
                "shorts": [{
                    "start": 0,
                    "end": 0,
                    "video_title_for_youtube_short": video_title or "Full Video",
                    "video_description_for_tiktok": "Full video converted to vertical.",
                    "video_description_for_instagram": "Full video converted to vertical.",
                    "viral_hook_text": ""
                }],
                "transcript": {"text": "", "segments": []}
            }
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            print(f"   ✅ Saved metadata to {metadata_file}")
    else:
        # 3. Transcribe
        job_id_from_dir = os.path.basename(output_dir)
        if notification_service:
            notification_service.notify_step(os.path.basename(output_dir), "Transcription started", f"Processing: {input_video}")
        transcript = transcribe_video(input_video)
        if notification_service:
            notification_service.notify_step(os.path.basename(output_dir), "Transcription completed", f"{len(transcript.get('segments', []))} segments")
            notification_service.notify_transcription_result(os.path.basename(output_dir), transcript)

        # Persist transcript as a standalone artifact (for S3/MinIO upload)
        try:
            transcript_file = os.path.join(output_dir, f"{job_id_from_dir}_transcript.json")
            with open(transcript_file, 'w') as f:
                json.dump(transcript, f, indent=2)
        except Exception:
            pass

        # Get duration
        cap = cv2.VideoCapture(input_video)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps
        cap.release()

        # 4. Gemini Analysis
        if notification_service:
            notification_service.notify_step(os.path.basename(output_dir), "AI Analysis started", "Identifying viral moments...")
        clips_data = get_viral_clips(transcript, duration)

        # Persist LLM response as a standalone artifact (for S3/MinIO upload)
        try:
            response_file = os.path.join(output_dir, f"{job_id_from_dir}_gemini_response.json")
            payload = clips_data if isinstance(clips_data, dict) else {"shorts": [], "llm_failed": True}
            with open(response_file, 'w') as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

        if clips_data and clips_data.get('gemini_quota_exhausted'):
            print("⚠️  Gemini quota exhausted. Creating fallback clips from transcript...")
            if notification_service:
                notification_service.notify_step(os.path.basename(output_dir), "Gemini quota exhausted", "Falling back to transcript segments")
            fallback_clips = _make_transcript_fallback_clips(transcript, duration)
            if not fallback_clips:
                print("❌ Gemini quota exhausted AND no transcript speech segments found. Cannot create clips.")
                total_time = time.time() - script_start_time
                print(f"\n⏱️  Total execution time: {total_time:.2f}s")
                sys.exit(1)
            clips_data['shorts'] = fallback_clips
            clips_data['fallback_reason'] = 'gemini_failed'

        if not clips_data or 'shorts' not in clips_data or not clips_data['shorts']:
            if clips_data and clips_data.get('gemini_quota_exhausted'):
                print("❌ Gemini quota exhausted — skipping whole-video fallback to avoid 900s output.")
                total_time = time.time() - script_start_time
                print(f"\n⏱️  Total execution time: {total_time:.2f}s")
                sys.exit(1)
            print("❌ Failed to identify clips. Converting whole video as fallback.")
            # Use a stable filename so the job has a predictable artifact.
            output_filename = f"{job_id_from_dir}_clip_1.mp4"
            output_file = os.path.join(output_dir, output_filename)
            success = process_video_to_vertical(input_video, output_file)

            # Persist minimal metadata even in fallback mode
            try:
                metadata_basename = f"{job_id_from_dir}_metadata"
                metadata_file = os.path.join(output_dir, f"{metadata_basename}.json")
                metadata = {
                    "shorts": [{
                        "start": 0,
                        "end": duration,
                        "video_title_for_youtube_short": video_title or "Full Video",
                        "video_description_for_tiktok": "Fallback: whole video converted to vertical.",
                        "video_description_for_instagram": "Fallback: whole video converted to vertical.",
                        "viral_hook_text": "",
                        "filename": output_filename,
                        "path": output_file,
                        "video_url": f"/videos/{job_id_from_dir}/{output_filename}",
                        "fallback_reason": "llm_failed"
                    }],
                    "transcript": transcript,
                }
                # Include the (possibly failed) LLM response shape for debugging
                if isinstance(clips_data, dict):
                    metadata.update({
                        "llm": {
                            "provider": get_llm_config()[0],
                            "response": clips_data,
                        }
                    })
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                print(f"   ✅ Saved metadata to {metadata_file}")
            except Exception:
                pass
        else:
            print(f"🔥 Found {len(clips_data['shorts'])} viral clips!")
            if notification_service:
                notification_service.notify_analysis_result(os.path.basename(output_dir), clips_data)

            # 5. Process each clip
            if notification_service:
                notification_service.notify_step(job_id_from_dir, "Cutting clips", f"Processing {len(clips_data['shorts'])} clips...")
            for i, clip in enumerate(clips_data['shorts']):
                start = clip.get('start', 0)
                end = clip.get('end', 0)
                safe_start, safe_end, clip_fix_note = _clamp_clip_range(start, end, duration)
                if clip_fix_note:
                    print(
                        f"   ⚠️ Clip {i+1} timestamp adjusted ({clip_fix_note}): "
                        f"{start}s-{end}s -> {safe_start}s-{safe_end}s"
                    )
                    clip['timestamp_fix_note'] = clip_fix_note
                clip['start'] = safe_start
                clip['end'] = safe_end
                start = safe_start
                end = safe_end
                print(f"\n🎬 Processing Clip {i+1}: {start}s - {end}s")
                print(f"   Title: {clip.get('video_title_for_youtube_short', 'No Title')}")
                if not os.path.exists(input_video):
                    raise FileNotFoundError(f"Input video does not exist: {input_video}")
                input_size = os.path.getsize(input_video) / (1024*1024)
                print(f"   Input: {input_video} ({input_size:.1f} MB)")
                if notification_service:
                    notification_service.notify_step(job_id_from_dir, f"Cutting clip {i+1}", f"{start}s - {end}s")

                # Cut clip — use job_id as prefix so the filename is stable and discoverable.
                clip_filename = f"{job_id_from_dir}_clip_{i+1}.mp4"
                clip_temp_path = os.path.join(output_dir, f"temp_{clip_filename}")
                clip_final_path = os.path.join(output_dir, clip_filename)

                # Cut + re-encode: output seeking for accuracy, libx264 baseline to repair partial-file corruption
                cut_start_time = time.time()
                print(f"   ✂️  Cutting clip (FFmpeg, 30fps, libx264/fast/CRF18/baseline/repair)...")
                clip_duration = max(0.1, end - start)
                cut_clip_30fps(input_video, clip_temp_path, start, clip_duration)
                cut_end_time = time.time()

                if not os.path.exists(clip_temp_path):
                    raise FileNotFoundError(
                        f"FFmpeg cut completed but output file is missing: {clip_temp_path}"
                        f" — input={input_video}, start={start}, duration={clip_duration}"
                    )
                output_size_mb = os.path.getsize(clip_temp_path) / (1024 * 1024)
                if output_size_mb < 0.1:
                    raise RuntimeError(
                        f"FFmpeg output file is suspiciously small ({output_size_mb:.1f} MB): {clip_temp_path}"
                    )

                print(f"   ✅ Cut done in {cut_end_time - cut_start_time:.2f}s ({output_size_mb:.1f} MB): {clip_temp_path}")

                # Process vertical
                success = process_video_to_vertical(clip_temp_path, clip_final_path)

                if success:
                    print(f"   ✅ Clip {i+1} ready: {clip_final_path}")
                    # Update shorts[i] with file location so app.py / frontend can build the URL.
                    clips_data['shorts'][i]['filename'] = clip_filename
                    clips_data['shorts'][i]['path'] = clip_final_path
                    clips_data['shorts'][i]['video_url'] = f"/videos/{job_id_from_dir}/{clip_filename}"
                    # Notify about the ready clip
                    if notification_service:
                        source = os.getenv("NOTIFY_JOB_SOURCE", "unknown")
                        notification_service.notify_clip_ready(
                            job_id=job_id_from_dir,
                            clip=clips_data['shorts'][i],
                            file_path=clip_final_path,
                            source_url="",
                            source=source,
                            clip_index=i,
                        )

                # Clean up temp cut
                if os.path.exists(clip_temp_path):
                    os.remove(clip_temp_path)

                # Test mode: chỉ render clip đầu tiên rồi dừng
                if IS_TEST_MODE:
                    print("   ⚠️  TEST MODE: dừng sau clip đầu tiên")
                    break

            # Save metadata AFTER all clips are processed so it reflects actual results.
            # Named with job_id prefix so app.py's rescue pattern can find it.
            metadata_basename = f"{job_id_from_dir}_metadata"
            metadata_file = os.path.join(output_dir, f"{metadata_basename}.json")
            clips_data['transcript'] = transcript
            with open(metadata_file, 'w') as f:
                json.dump(clips_data, f, indent=2)
            print(f"   ✅ Saved metadata to {metadata_file}")

    # Clean up original if requested
    if args.url and not args.keep_original and os.path.exists(input_video):
        os.remove(input_video)
        print(f"🗑️  Cleaned up downloaded video.")

    total_time = time.time() - script_start_time
    print(f"\n⏱️  Total execution time: {total_time:.2f}s")
