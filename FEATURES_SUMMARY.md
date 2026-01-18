# Subtitle Cockpit - Features Documentation

## Overview

A comprehensive web-based subtitle processing application with support for OCR conversion, translation, subtitle search, and file management. The application runs in Docker and provides a modern web interface for managing subtitle workflows.

The application is intended to run on the same server as a Plex/Emby/other media player. The intended workflow is as follows:
1. Download high-quality video file (i.e. bluray quality), because usually they have the subtitles embedded. This step is not covered by the application.
2. (Button click) Upload the video file to the application
3. (Button click) Extract the hardcoded image-based subtitles from the video file 
4. (Button click) Convert the image-based subtitles to textual SRT format
5. (Button click) Translate the subtitles to the target language
6. Refresh the media player library to see the new subtitles

---

## Core Features

### 1. File Explorer

**Web-based file browser** for navigating and managing media files and subtitles.

**Capabilities:**
- Browse directory structure with breadcrumb navigation
- Multi-file selection with checkboxes
- Click entire file row to toggle selection
- Upload files via drag-and-drop or file picker
- Download files directly from browser
- Rename/move files and folders
- Delete files and folders
- Configurable root directory

**Visual Features:**
- Video files highlighted with yellow/amber background (#fff8e1)
- Distinct icons for different file types:
  - 🎬 Video files (.mkv, .mp4, .avi, .mov, .wmv, .flv, .webm, .m4v, .mpeg, .mpg)
  - 💬 Subtitle files (.srt, .sup, .sub, .idx, .ass, .ssa, .vtt)
  - 📁 Folders
  - 📄 Other files

**Filtering:**
- **All** - Show all files (default)
- **Videos** - Show only video files
- **Subtitles** - Show only subtitle files
- Folders always visible regardless of filter
- Configurable file type exclusions (e.g., `.xml, .nfo, .txt, .jpg, .png`)

---

### 2. Subtitle Extraction

**Extract embedded subtitles from video files** (MKV, MP4, etc.).

**Supported Tools:**
- **mkvextract** - For MKV files
- **ffmpeg** - For MP4 and other formats

**Features:**
- Cross-platform support (Windows and Linux)
- Automatic tool selection based on file format
- Extracts subtitle track for the language configured in settings
- Tries to extract textual format first, then hardsubs
- Preserves subtitle format (SRT, ASS, SUP, etc.)
- Processes via job queue
- Output files named: `<video>.<track_number>.<language>.<format>`

---

### 3. OCR Subtitle Conversion (SUP/SUB to SRT)

**Convert image-based subtitles to text-based SRT format** using Tesseract OCR.

**Supported Input Formats:**
- `.sup` - Blu-ray PGS/HDMV subtitles
- `.sub` - DVD VobSub subtitles (requires `.idx` companion file)

**Features:**
- Cross-platform support (Windows and Linux)
- Automatic Tesseract path detection
- Configurable OCR source language (3-letter code: eng, deu, fra, spa, etc.) in the settings (same as for extraction)
- Processes via job queue for background execution
- Image preprocessing matching SubtitleEdit quality

**Technical Details:**
- Parses PGS segments (PCS, PDS, ODS)
- RLE image decompression
- YCbCr to RGB color conversion (BT.709 standard)
- Image binarization for optimal OCR
- Tesseract PSM=6 (uniform text block), OEM=3 (default engine)

---

### 4. Subtitle Translation

**Translate SRT subtitle files** to different languages using multiple translation providers.

**Supported Providers:**
- **DeepL** - High-quality neural translation
- **Azure Translator** - High-quality neural translation
- **Google Translate** - Free translation (unstable)

**Features:**
- Batch translation with automatic rate limiting (to not overload the API providers)
- Multiple API key support per provider
- Automatic failover between API keys
- Automatic provider switching on error
- VPN support via Mullvad WireGuard for identity obfuscation
- Configurable wait time between requests
- HTML tag preservation in subtitles
- Context-aware translation for LLMs ("These are subtitles from a video file")

**Provider-Specific Settings:**
- **DeepL**: API key, VPN config (optional)
- **Azure**: API key, endpoint, region, VPN config (optional)
- **Google Translate**: No API key required, configurable wait time

**VPN Integration:**
- Per-API-key VPN configuration
- Automatic VPN start/stop per translation job
- IP verification after VPN connection
- Graceful degradation if VPN fails

---

### 5. Online Subtitle Search

**Search and download subtitles** from online databases.

**Supported Providers:**
- **OpenSubtitles** - Large subtitle database

**Features:**
- Automatic video file analysis (using guessit)
- Search by video filename
- Configurable maximum downloads per video
- Automatic subtitle placement next to video file
- Processes via job queue

**Configuration:**
- OpenSubtitles username
- OpenSubtitles password
- Maximum subtitle downloads (default: 1)

---

### 6. Job Queue System

**Background job processing** with SQLite-based queue and web UI.

**Job Types:**
- `extract` - Subtitle extraction (timeout: 5 minutes)
- `sup_to_srt` - OCR conversion (timeout: 10 minutes)
- `translate` - Translation (timeout: 30 minutes)
- `search_subtitles` - Online search (timeout: 5 minutes)

**Job States:**
- `pending` - Waiting to be processed
- `running` - Currently being processed
- `completed` - Finished successfully
- `failed` - Failed with error message
- `timeout` - Exceeded maximum runtime

**Features:**
- Configurable parallel job limit (1-10, default: 1)
- FIFO processing (oldest first)
- Real-time progress monitoring
- Job deletion for pending jobs
- Automatic cleanup (failed jobs after 1 hour)
- Background processor thread
- Per-job timeout enforcement

**Job Queue UI:**
- Accessible at `/jobs`
- Auto-refresh every 5 seconds
- Sections: Running Jobs, Pending Jobs, Failed Jobs
- Delete button for pending jobs
- Displays: Job ID, Type, File, Status, Timestamps, Error messages

---

### 7. Settings Management

**Centralized configuration** via web interface.

**General Settings:**
- Root directory for file explorer
- Excluded file types (comma-separated)
- Maximum parallel jobs (1-10)
- OCR source language (3-letter code)

**Translation Settings:**
- Provider selection (GoogleTranslate, DeepL, Azure)
- Target language
- Wait time per provider (milliseconds)
- Auto-switch provider on error
- Auto-change API key on error (per provider)

**Provider-Specific Settings:**
- **Azure**: Endpoint URL, Region
- **DeepL**: API keys with VPN configs
- **Azure**: API keys with VPN configs

**API Key Management:**
- Multiple keys per provider
- Active/inactive toggle
- VPN config assignment per key
- Last used timestamp tracking
- Retry days configuration

**Subtitle Search Settings:**
- OpenSubtitles username
- OpenSubtitles password
- Maximum subtitle downloads

**Settings Import/Export:**
- Export settings as JSON file
- Import settings from JSON file
- Validation on import

**VPN Configuration:**
- List available WireGuard configs from `/vpn-configs`
- Assign configs to specific API keys
- Shows which configs are assigned

---

## User Interface

### Main Page (`/`)
- File explorer with breadcrumb navigation
- Action buttons: SUP to SRT, Translate, Extract, Search Subtitles
- Filter buttons: All, Videos, Subtitles
- Upload/Download/Delete/Rename functionality
- Link to Job Queue and Settings

### Job Queue Page (`/jobs`)
- Real-time job status display
- Running, Pending, and Failed job sections
- Delete pending jobs
- Auto-refresh every 5 seconds
- Manual refresh button

### Settings Page (`/settings`)
- Tabbed or grouped settings interface
- Provider-specific settings visually indented
- Save/Load functionality
- Import/Export settings
- VPN config management

---

## Technical Architecture

### Backend
- **Framework**: Flask (Python)
- **Job Queue**: SQLite database (`jobs.db`)
- **OCR Engine**: Tesseract 5.5.0
- **Translation**: DeepL SDK, Azure SDK, deep-translator
- **Subtitle Parsing**: Custom parsers for SUP/SUB formats
- **Video Analysis**: guessit, subliminal
- **VPN**: WireGuard (Mullvad)

### Frontend
- **HTML/CSS/JavaScript**
- **Icons**: Font Awesome
- **Auto-refresh**: JavaScript intervals
- **AJAX**: Fetch API for async operations

### Deployment
- **Container**: Docker
- **Base Image**: python:3.12-slim
- **Dependencies**: FFmpeg, mkvtoolnix, Tesseract, WireGuard
- **Volumes**: Media directory, settings, VPN configs, source code (dev mode)
- **Privileges**: Privileged mode with NET_ADMIN and SYS_MODULE for VPN

### File Structure
```
windsurf-project/
├── src/                          # Python source files
│   ├── app.py                    # Flask application
│   ├── job_queue.py              # Job queue system
│   ├── ocr_subtitle_converter.py # OCR conversion
│   ├── translation_providers.py  # Translation logic
│   ├── google_translate_local.py # Google Translate
│   └── subtitle_search.py        # Subtitle search
├── templates/                    # HTML templates
│   ├── index.html               # Main page
│   ├── jobs.html                # Job queue page
│   └── settings.html            # Settings page
├── static/                       # CSS/JS/images
│   ├── css/style.css
│   └── js/script.js
├── Dockerfile                    # Container definition
├── requirements.txt              # Python dependencies
└── settings.json                 # Persistent settings
```

---

## Supported File Formats

### Video Files
.mkv, .mp4, .avi, .mov, .wmv, .flv, .webm, .m4v, .mpeg, .mpg

### Subtitle Files
.srt, .sup, .sub, .idx, .ass, .ssa, .vtt

### Image-Based Subtitles (OCR Input)
.sup (Blu-ray PGS), .sub (DVD VobSub)

### Text-Based Subtitles (Translation Input)
.srt

---

## Configuration Examples

### Exclude Unwanted Files
```
Settings → Excluded file types: .xml, .nfo, .txt, .jpg, .png, .json
```

### Configure Translation
```
Settings → Provider: DeepL
Settings → Target Language: th
Settings → DeepL API Keys: Add key with optional VPN config
Settings → Wait time: 1000ms
```

### Configure OCR
```
Settings → OCR source language: deu (for German)
```

### Configure Job Processing
```
Settings → Maximum parallel jobs: 4
```

### Configure Subtitle Search
```
Settings → OpenSubtitles Username: your_username
Settings → OpenSubtitles Password: your_password
Settings → Max subtitle downloads: 3
```

---

## Workflow Examples

### Extract Embedded Subtitles
1. Select video files (`.mkv`, `.mp4`)
2. Click "Extract" button
3. Jobs added to queue
4. Extracted subtitles appear next to video files

### Convert SUP to SRT
1. Navigate to folder with `.sup` files
2. Select one or more `.sup` files
3. Click "SUP to SRT" button
4. Jobs added to queue
5. Monitor progress in Job Queue page
6. Refresh file explorer to see `.srt` files

### Translate Subtitles
1. Select `.srt` files
2. Configure target language in Settings
3. Click "Translate" button
4. Jobs added to queue
5. Monitor progress in Job Queue page
6. Translated files appear as `<filename>.<lang>.srt`

### Search Online Subtitles
1. Select video files
2. Click "Search Subtitles" button
3. Jobs added to queue
4. Downloaded subtitles appear next to video files

---

## API Endpoints

### File Operations
- `GET /api/list` - List files in directory
- `POST /api/download` - Download file
- `POST /api/upload` - Upload file
- `POST /api/delete` - Delete files/folders
- `POST /api/rename` - Rename/move file/folder

### Job Operations
- `GET /api/jobs` - Get all jobs (pending, running, failed)
- `DELETE /api/jobs/<id>` - Delete pending job
- `POST /api/sup_to_srt` - Add OCR conversion jobs
- `POST /api/translate` - Add translation jobs
- `POST /api/extract_subtitles` - Add extraction jobs
- `POST /api/search_subtitles` - Add search jobs

### Settings Operations
- `GET /api/settings` - Get current settings
- `POST /api/settings` - Update settings
- `GET /api/settings/export` - Export settings as JSON
- `POST /api/settings/import` - Import settings from JSON
- `GET /api/vpn_configs` - List available VPN configs

---

## Environment Variables

- `DEFAULT_MEDIA_DIR` - Default root directory (default: `/media`)
- `FLASK_ENV` - Flask environment (production/development)
- `FLASK_DEBUG` - Enable Flask debug mode (0/1)
- `HOST_MEDIA_DIR` - Host path for media directory
- `MULLVAD_VPN_DIR` - Path to VPN config directory

---

## Security Considerations

- VPN support requires privileged container mode
- API keys stored in `settings.json` (should be protected)
- No authentication/authorization implemented
- Intended for local/trusted network use
- File operations have full access to mounted directories

---

## Performance Characteristics

### OCR Conversion
- Speed: ~1-2 seconds per subtitle image
- Memory: ~200MB per job
- CPU: Single-threaded per job

### Translation
- Speed: Depends on provider API
- Rate limiting: Configurable wait time
- Batch processing: Azure (50 texts/batch), DeepL (full file)

### Job Queue
- Concurrent jobs: Configurable (1-10)
- Database: SQLite (lightweight, no external dependencies)
- Cleanup: Automatic (7 days for completed, 1 hour for failed)

---

## Browser Compatibility

- Modern browsers with JavaScript enabled
- Tested on: Chrome, Firefox, Edge
- Requires: Fetch API, ES6 support
- Responsive design for desktop use

---

## Access URLs

- **Main Application**: `http://localhost:5000`
- **Job Queue**: `http://localhost:5000/jobs`
- **Settings**: `http://localhost:5000/settings`

---

## Summary

This application provides a complete subtitle processing workflow:

1. **File Management** - Browse, upload, download, organize files
2. **OCR Conversion** - Convert image subtitles to text (SUP/SUB → SRT)
3. **Translation** - Translate subtitles to different languages
4. **Extraction** - Extract embedded subtitles from videos
5. **Search** - Find and download subtitles online
6. **Job Queue** - Background processing with monitoring
7. **Settings** - Centralized configuration management

All operations are accessible through a modern web interface with real-time status updates and comprehensive error handling.
