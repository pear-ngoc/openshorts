# OpenShorts - Hướng Dẫn Cài Đặt Docker

## Tổng Quan

OpenShorts là ứng dụng AI chuyển đổi video dọc từ YouTube hoặc video local thành các clip ngắn viral cho TikTok, Instagram Reels, YouTube Shorts.

## Các Dịch Vụ

Dự án bao gồm 3 container chính:

| Container | Image | Cổng | Mô Tả |
|-----------|-------|------|-------|
| `backend` | tuna99/openshorts-backend-dev | 8000 | Xử lý video, AI, transcription |
| `frontend` | tuna99/openshorts-frontend | 5175:5173 | Giao diện dashboard React |
| `renderer` | tuna99/openshorts-renderer | 3100 | Tạo video bằng Remotion |

## Cách Sử Dụng

### 1. Khởi Động Toàn Bộ Hệ Thống

```bash
docker compose up --build
```

Truy cập:
- **Dashboard:** http://localhost:5175
- **Backend API:** http://localhost:8000
- **Renderer:** http://localhost:3100

### 2. Khởi Động Riêng Từng Dịch Vụ

```bash
# Chỉ backend
docker compose up --build backend

# Backend + frontend (không cần renderer)
docker compose up --build backend frontend

# Chỉ frontend
docker compose up --build frontend
```

### 3. Dừng Dịch Vụ

```bash
# Dừng và xoá containers
docker compose down

# Dừng, xoá containers và volumes
docker compose down -v

# Dừng và xoá images
docker compose down --rmi local
```

### 4. Xem Logs

```bash
# Tất cả services
docker compose logs -f

# Chỉ backend
docker compose logs -f backend

# Chỉ frontend
docker compose logs -f frontend

# Chỉ renderer
docker compose logs -f renderer
```

## Cấu Hình Môi Trường

Tạo file `.env` trong thư mục gốc của dự án:

```bash
# --- Transcription ---
# Provider: "local" (faster-whisper) hoặc "groq"
TRANSCRIPTION_PROVIDER=local
GROQ_API_KEY=your_groq_api_key
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo
GROQ_TRANSCRIPTION_BASE_URL=https://api.groq.com/openai/v1
TRANSCRIPTION_CHUNK_SECONDS=600

# --- LLM / AI ---
# Provider: "gemini" hoặc provider tương thích OpenAI format
LLM_PROVIDER=gemini
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=
LLM_MODEL=gemini-2.5-flash

# Gemini (tuỳ chọn - dùng thay cho LLM)
GEMINI_API_KEY=your_gemini_api_key
GEMINI_BASE_URL=
GEMINI_MODEL=gemini-2.5-flash

# --- YouTube Download Proxy (ytsave.to) ---
# Bật khi yt-dlp bị chặn (IP ban / bot check).
USE_YTSAVE_PROXY=false
YTSAVE_PHPSESSID=
```

### Biến Môi Trường Chi Tiết

#### Transcription

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `TRANSCRIPTION_PROVIDER` | `local` | `local` dùng faster-whisper, `groq` dùng API |
| `GROQ_API_KEY` | - | API key cho Groq transcription |
| `GROQ_TRANSCRIPTION_MODEL` | `whisper-large-v3-turbo` | Model Whisper trên Groq |
| `GROQ_TRANSCRIPTION_BASE_URL` | `https://api.groq.com/openai/v1` | Base URL Groq API |
| `TRANSCRIPTION_CHUNK_SECONDS` | `600` | Chia video thành chunks (giây) khi transcription |

#### LLM / AI

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `LLM_PROVIDER` | `gemini` | Provider AI: `gemini` hoặc `openai` |
| `LLM_API_KEY` | - | API key cho LLM |
| `LLM_BASE_URL` | - | Base URL tuỳ chỉnh (nếu dùng proxy) |
| `LLM_MODEL` | `gemini-2.5-flash` | Model LLM |
| `GEMINI_API_KEY` | - | API key riêng cho Gemini |
| `GEMINI_BASE_URL` | - | Base URL tuỳ chỉnh cho Gemini |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model Gemini |

#### YouTube Download Proxy (ytsave.to)

Khi yt-dlp tải YouTube bị lỗi do IP bị chặn/rate-limit, bạn có thể bật proxy
`ytsave.to` (đã tích hợp sẵn trong backend). Khi bật, hệ thống sẽ thử ytsave
trước; nếu lỗi sẽ tự động fallback về yt-dlp.

**Cách lấy cookie `PHPSESSID`:**
1. Mở `https://ytsave.to/` (hoặc `https://ytsave.to/vi2/`) trên trình duyệt
2. Mở DevTools → Application/Storage → Cookies → `ytsave.to`
3. Copy giá trị `PHPSESSID`

**Cấu hình trong `.env`:**
```bash
USE_YTSAVE_PROXY=true
YTSAVE_PHPSESSID=gia_tri_cookie
```

Sau đó restart lại docker: `docker compose down && docker compose up --build`

## Thư Mục Dữ Liệu

Các thư mục được mount từ host vào container:

```
/
├── uploads/    # Video upload từ người dùng
├── output/     # Video đã xử lý
├── outputs/    # Output phụ
├── temp/       # File tạm trong quá trình xử lý
├── clips/      # Các clip đã cắt
└── .env        # Biến môi trường
```

Backend container mount thêm:
- `./:/app` - Source code (dev mode)
- `backend_models` - Volume chứa YOLO models
- `./uploads:/app/uploads`
- `./output:/app/output`
- `./outputs:/app/outputs`
- `./temp:/app/temp`
- `./clips:/app/clips`

## API Endpoints

| Method | Route | Mô Tả |
|--------|-------|-------|
| POST | `/api/process` | Xử lý video |
| GET | `/api/status/{job_id}` | Kiểm tra trạng thái job |
| POST | `/api/edit` | Áp dụng effect video AI |
| POST | `/api/subtitle` | Tạo và áp dụng phụ đề |
| POST | `/api/hook` | Thêm text hook overlay |
| POST | `/api/translate` | Voice dubbing qua ElevenLabs |
| GET | `/api/translate/languages` | Danh sách ngôn ngữ hỗ trợ |
| POST | `/api/social/post` | Đăng lên mạng xã hội |

## Khắc Phục Sự Cố

### Container không khởi động được

1. Kiểm tra logs:
```bash
docker compose logs backend
```

2. Kiểm tra file `.env` có tồn tại không
3. Kiểm tra port đã được sử dụng chưa:
```bash
lsof -i :8000
lsof -i :5175
lsof -i :3100
```

### Lỗi Model AI

- Đảm bảo `GEMINI_API_KEY` hoặc `LLM_API_KEY` đúng trong `.env`
- Kiểm tra quota/limit của API

### Lỗi Permission

Nếu gặp lỗi quyền truy cập thư mục:
```bash
chmod -R 777 uploads output outputs temp clips
```

### Build lại từ đầu

```bash
docker compose down --rmi local
docker compose build --no-cache
docker compose up
```

## Lệnh Hữu Ích

```bash
# Liệt kê containers đang chạy
docker compose ps

# Restart một service
docker compose restart backend

# Rebuild không cache
docker compose build --no-cache backend

# Truy cập shell trong container
docker exec -it tuna99/openshorts-backend /bin/bash
```
