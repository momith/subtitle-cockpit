import json
import logging
import math
import os
import shutil
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import pysubs2
from babelfish import Language

try:
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:
    rapidfuzz_fuzz = None


VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
    '.mpeg', '.mpg', '.ts', '.m2ts'
}

ISO639_B_TO_T = {
    'alb': 'sqi',
    'arm': 'hye',
    'baq': 'eus',
    'bur': 'mya',
    'chi': 'zho',
    'cze': 'ces',
    'dut': 'nld',
    'fre': 'fra',
    'ger': 'deu',
    'gre': 'ell',
    'ice': 'isl',
    'mac': 'mkd',
    'mao': 'mri',
    'may': 'msa',
    'per': 'fas',
    'rum': 'ron',
    'slo': 'slk',
    'tib': 'bod',
    'wel': 'cym',
}

logger = logging.getLogger(__name__)

_HEAVY_EMBEDDING_MODEL = None
_HEAVY_EMBEDDING_MODEL_LOAD_FAILED = False
_HEAVY_EMBEDDING_MODEL_NAME = None


class SubtitleSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioStreamInfo:
    index: int
    codec_name: str
    language_alpha3: Optional[str]
    language_raw: Optional[str]


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float
    audio_streams: List[AudioStreamInfo]


@dataclass(frozen=True)
class AudioSampleWindow:
    name: str
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class AnchorMatch:
    window_name: str
    transcript_text: str
    transcript_seconds: float
    subtitle_index: int
    subtitle_seconds: float
    similarity: float


@dataclass(frozen=True)
class SyncPlan:
    subtitle_language_alpha2: str
    subtitle_language_alpha3: str
    audio_stream_index: int
    sample_minutes: int
    sample_windows: List[AudioSampleWindow]
    start_anchor: AnchorMatch
    end_anchor: AnchorMatch
    offset_seconds: float
    scale: float


@dataclass(frozen=True)
class PiecewiseSegment:
    start_subtitle_seconds: float
    end_subtitle_seconds: float
    offset_seconds: float
    scale: float


@dataclass(frozen=True)
class SyncResult:
    plan: SyncPlan
    output_path: str


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class NormalizedCue:
    index: int
    start_seconds: float
    end_seconds: float
    raw_text: str
    normalized_text: str


@dataclass(frozen=True)
class SlidingCueWindow:
    start_index: int
    end_index: int
    start_seconds: float
    end_seconds: float
    normalized_text: str


@dataclass(frozen=True)
class LoadedSubtitles:
    subs: pysubs2.SSAFile
    encoding: str


@dataclass(frozen=True)
class AnchorCandidate:
    normalized_text: str
    anchor_seconds: float


@dataclass(frozen=True)
class WhisperTranscriptionConfig:
    model_name: str = 'tiny'
    device: str = 'cpu'
    compute_type: str = 'int8'
    cpu_threads: int = 1
    num_workers: int = 1
    beam_size: int = 1
    best_of: int = 1
    patience: float = 1.0
    temperature: float = 0.0
    condition_on_previous_text: bool = False
    vad_filter: bool = True
    word_timestamps: bool = False


@dataclass(frozen=True)
class SyncMatchingConfig:
    anchor_min_similarity: float = 0.38
    anchor_max_window_size: int = 5
    anchor_max_candidates_from_edges: int = 8
    anchor_max_phrase_segments: int = 3
    anchor_min_text_length: int = 18
    max_scale_delta: float = 0.08
    max_end_error_seconds: float = 1.0


@dataclass(frozen=True)
class HeavySyncConfig:
    embedding_model_name: str = 'intfloat/multilingual-e5-small'
    max_cue_gap_seconds: float = 3.5
    max_transcript_gap_seconds: float = 3.5
    step_segments: int = 1


def default_whisper_transcription_config() -> WhisperTranscriptionConfig:
    return WhisperTranscriptionConfig(
        model_name=os.environ.get('SUBTITLE_SYNC_WHISPER_MODEL', 'tiny'),
        device=os.environ.get('SUBTITLE_SYNC_WHISPER_DEVICE', 'cpu'),
        compute_type=os.environ.get('SUBTITLE_SYNC_WHISPER_COMPUTE_TYPE', 'int8'),
        cpu_threads=max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_CPU_THREADS', os.cpu_count() or 1)), 1),
        num_workers=max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_NUM_WORKERS', '1')), 1),
        beam_size=max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_BEAM_SIZE', '3')), 1),
        best_of=max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_BEST_OF', '3')), 1),
        patience=max(float(os.environ.get('SUBTITLE_SYNC_WHISPER_PATIENCE', '1.0')), 0.0),
        temperature=max(float(os.environ.get('SUBTITLE_SYNC_WHISPER_TEMPERATURE', '0.0')), 0.0),
        condition_on_previous_text=os.environ.get('SUBTITLE_SYNC_WHISPER_CONDITION_ON_PREVIOUS', '0') == '1',
        vad_filter=os.environ.get('SUBTITLE_SYNC_WHISPER_VAD_FILTER', '1') != '0',
        word_timestamps=os.environ.get('SUBTITLE_SYNC_WHISPER_WORD_TIMESTAMPS', '1') != '0',
    )


def default_sync_matching_config() -> SyncMatchingConfig:
    return SyncMatchingConfig()


def default_heavy_sync_config() -> HeavySyncConfig:
    return HeavySyncConfig(
        embedding_model_name=str(os.environ.get('SUBTITLE_SYNC_HEAVY_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small') or 'intfloat/multilingual-e5-small'),
        max_cue_gap_seconds=max(float(os.environ.get('SUBTITLE_SYNC_HEAVY_MAX_CUE_GAP_SECONDS', '3.5')), 0.0),
        max_transcript_gap_seconds=max(float(os.environ.get('SUBTITLE_SYNC_HEAVY_MAX_TRANSCRIPT_GAP_SECONDS', '3.5')), 0.0),
        step_segments=max(int(os.environ.get('SUBTITLE_SYNC_HEAVY_STEP_SEGMENTS', '1')), 1),
    )


def normalize_text(value: str) -> str:
    text = (value or '').lower()
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\{[^}]+\}', ' ', text)
    text = re.sub(r'[^\w\s]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'_+', ' ', text)
    text = re.sub(r'\s+', ' ', text, flags=re.UNICODE).strip()
    return text


def _timems_to_seconds(value: int) -> float:
    return float(value) / 1000.0


def _seconds_to_timems(value: float) -> int:
    return int(round(max(value, 0.0) * 1000.0))


def load_subtitles(subtitle_path: str) -> LoadedSubtitles:
    encodings = ['utf-8', 'utf-8-sig', 'cp1252', 'latin-1']
    last_error = None
    for encoding in encodings:
        try:
            subs = pysubs2.load(subtitle_path, encoding=encoding)
            return LoadedSubtitles(subs=subs, encoding=encoding)
        except Exception as exc:
            last_error = exc
    raise SubtitleSyncError(f'Could not read subtitle file {subtitle_path}: {last_error}')


def normalize_language_code(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    normalized = str(code).strip().lower().replace('_', '-').split('-')[0]
    if len(normalized) == 2:
        return Language.fromcode(normalized, 'alpha2').alpha3
    if len(normalized) == 3:
        normalized = ISO639_B_TO_T.get(normalized, normalized)
        try:
            return Language.fromcode(normalized, 'alpha3t').alpha3
        except Exception:
            try:
                return Language.fromcode(normalized, 'alpha3b').alpha3
            except Exception:
                return normalized
    return None


def parse_subtitle_language(subtitle_path: str) -> tuple[str, str]:
    base_name = Path(subtitle_path).name
    stem = Path(base_name).stem
    match = re.search(r'\.([a-zA-Z]{2,3})$', stem)
    if not match:
        raise SubtitleSyncError(
            f'Could not determine subtitle language from filename: {base_name}. Expected suffix like .de.srt'
        )

    code = match.group(1).lower()
    try:
        normalized_alpha3 = normalize_language_code(code)
        if not normalized_alpha3:
            raise ValueError(code)
        language = Language.fromcode(normalized_alpha3, 'alpha3t')
    except Exception as exc:
        raise SubtitleSyncError(f'Unsupported subtitle language code in filename: {code}') from exc

    return language.alpha2, language.alpha3


def build_sample_windows(duration_seconds: float, sample_minutes: int) -> List[AudioSampleWindow]:
    if duration_seconds <= 0:
        raise SubtitleSyncError('Video duration must be greater than zero')
    if sample_minutes < 1:
        raise SubtitleSyncError('sync sample minutes must be at least 1')

    sample_seconds = float(sample_minutes) * 60.0
    head_duration = min(sample_seconds, duration_seconds)
    tail_start = max(duration_seconds - sample_seconds, 0.0)
    tail_duration = max(duration_seconds - tail_start, 0.0)

    windows = [
        AudioSampleWindow(name='start', start_seconds=0.0, duration_seconds=head_duration),
        AudioSampleWindow(name='end', start_seconds=tail_start, duration_seconds=tail_duration),
    ]
    return windows


def build_full_transcription_windows(duration_seconds: float, chunk_minutes: int) -> List[AudioSampleWindow]:
    if duration_seconds <= 0:
        raise SubtitleSyncError('Video duration must be greater than zero')
    if chunk_minutes < 1:
        raise SubtitleSyncError('chunk_minutes must be at least 1')

    chunk_seconds = float(chunk_minutes) * 60.0
    windows: List[AudioSampleWindow] = []
    cursor = 0.0
    index = 1
    while cursor < duration_seconds:
        duration = min(chunk_seconds, duration_seconds - cursor)
        windows.append(
            AudioSampleWindow(
                name=f'full_{index:03d}',
                start_seconds=cursor,
                duration_seconds=duration,
            )
        )
        cursor += duration
        index += 1
    return windows


def load_normalized_cues(subtitle_path: str) -> List[NormalizedCue]:
    loaded = load_subtitles(subtitle_path)
    subs = loaded.subs
    cues: List[NormalizedCue] = []
    for index, line in enumerate(subs, start=1):
        text = line.plaintext.strip()
        normalized = normalize_text(text)
        if not normalized:
            continue
        cues.append(
            NormalizedCue(
                index=index,
                start_seconds=_timems_to_seconds(line.start),
                end_seconds=_timems_to_seconds(line.end),
                raw_text=text,
                normalized_text=normalized,
            )
        )
    if not cues:
        raise SubtitleSyncError(f'No usable subtitle cues found in {subtitle_path}')
    return cues


def build_sliding_windows(
    cues: List[NormalizedCue],
    max_window_size: int = 8,
    max_gap_seconds: float | None = None,
) -> List[SlidingCueWindow]:
    windows: List[SlidingCueWindow] = []
    for start in range(len(cues)):
        parts: List[str] = []
        for end in range(start, min(len(cues), start + max_window_size)):
            if max_gap_seconds is not None and end > start:
                gap_seconds = cues[end].start_seconds - cues[end - 1].end_seconds
                if gap_seconds > max_gap_seconds:
                    break
            parts.append(cues[end].normalized_text)
            text = normalize_text(' '.join(parts))
            if not text:
                continue
            windows.append(
                SlidingCueWindow(
                    start_index=start,
                    end_index=end,
                    start_seconds=cues[start].start_seconds,
                    end_seconds=cues[end].end_seconds,
                    normalized_text=text,
                )
            )
    return windows


def build_anchor_candidates(
    segments: Iterable[TranscriptSegment],
    from_end: bool,
    max_candidates_from_edges: int = 2,
    max_phrase_segments: int = 4,
    min_text_length: int = 12,
) -> List[AnchorCandidate]:
    ordered = list(segments)
    if from_end:
        ordered = list(reversed(ordered))

    candidates: List[AnchorCandidate] = []
    for start in range(min(len(ordered), max_candidates_from_edges)):
        parts: List[str] = []
        anchor_seconds = ordered[start].end_seconds if from_end else ordered[start].start_seconds
        for end in range(start, min(len(ordered), start + max_phrase_segments)):
            parts.append(ordered[end].text)
            normalized = normalize_text(' '.join(parts))
            if len(normalized) < min_text_length:
                continue
            candidates.append(AnchorCandidate(normalized_text=normalized, anchor_seconds=anchor_seconds))

    if not candidates:
        raise SubtitleSyncError('Transcription did not contain a usable sentence for anchor matching')
    return candidates


def _similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


def _collapse_whitespace(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').replace('\\N', ' ').replace('\n', ' ')).strip()


def find_anchor_match(
    cues: List[NormalizedCue],
    windows: List[SlidingCueWindow],
    candidates: List[AnchorCandidate],
    window_name: str,
    search_from_end: bool,
    min_similarity: float = 0.5,
) -> AnchorMatch:
    candidate_windows = windows if not search_from_end else list(reversed(windows))
    midpoint = len(cues) // 2
    best: Optional[tuple[float, SlidingCueWindow, AnchorCandidate]] = None

    for candidate in candidates:
        for window in candidate_windows:
            if not search_from_end and window.start_index > midpoint:
                break
            if search_from_end and window.end_index < midpoint:
                break

            score = _similarity(candidate.normalized_text, window.normalized_text)
            if best is None or score > best[0]:
                best = (score, window, candidate)

    if best is None or best[0] < min_similarity:
        raise SubtitleSyncError(
            f'Could not find a reliable {window_name} anchor in subtitle file (best similarity: {0.0 if best is None else best[0]:.3f})'
        )

    score, best_window, best_candidate = best
    subtitle_seconds = best_window.start_seconds if not search_from_end else best_window.end_seconds
    subtitle_index = best_window.start_index if not search_from_end else best_window.end_index

    return AnchorMatch(
        window_name=window_name,
        transcript_text=best_candidate.normalized_text,
        transcript_seconds=best_candidate.anchor_seconds,
        subtitle_index=cues[subtitle_index].index,
        subtitle_seconds=subtitle_seconds,
        similarity=score,
    )


def calculate_linear_transform(
    start_anchor: AnchorMatch,
    end_anchor: AnchorMatch,
    max_scale_delta: float = 0.08,
    max_end_error_seconds: float = 1.0,
) -> tuple[float, float]:
    subtitle_delta = end_anchor.subtitle_seconds - start_anchor.subtitle_seconds
    transcript_delta = end_anchor.transcript_seconds - start_anchor.transcript_seconds

    if subtitle_delta <= 0 or transcript_delta <= 0:
        raise SubtitleSyncError('Invalid anchors: timestamps must move forward for start and end anchors')

    scale = transcript_delta / subtitle_delta
    if not math.isfinite(scale) or scale <= 0:
        raise SubtitleSyncError('Computed invalid subtitle scale factor')

    # Reject implausible drift corrections. A few percent covers timing/framerate issues.
    if abs(scale - 1.0) > max_scale_delta:
        raise SubtitleSyncError(f'Anchor drift is too large for a reliable linear sync (scale={scale:.5f})')

    offset = start_anchor.transcript_seconds - (start_anchor.subtitle_seconds * scale)

    predicted_end = end_anchor.subtitle_seconds * scale + offset
    end_error = abs(predicted_end - end_anchor.transcript_seconds)
    if end_error > max_end_error_seconds:
        raise SubtitleSyncError(f'Anchors are inconsistent (end error {end_error:.3f}s)')

    return offset, scale


def apply_transform_to_subtitles(subtitle_path: str, output_path: str, offset_seconds: float, scale: float) -> None:
    logger.info(
        'subtitle_sync: apply transform start subtitle=%s output=%s offset=%.3fs scale=%.6f',
        subtitle_path,
        output_path,
        offset_seconds,
        scale,
    )
    started_at = time.perf_counter()
    loaded = load_subtitles(subtitle_path)
    subs = loaded.subs
    logger.info(
        'subtitle_sync: loaded subtitle file for write lines=%s encoding=%s in %.2fs',
        len(subs),
        loaded.encoding,
        time.perf_counter() - started_at,
    )

    transform_started_at = time.perf_counter()
    for line in subs:
        start_seconds = _timems_to_seconds(line.start)
        end_seconds = _timems_to_seconds(line.end)
        new_start = start_seconds * scale + offset_seconds
        new_end = end_seconds * scale + offset_seconds
        line.start = _seconds_to_timems(new_start)
        line.end = max(line.start, _seconds_to_timems(new_end))

    logger.info(
        'subtitle_sync: transformed subtitle timestamps lines=%s in %.2fs',
        len(subs),
        time.perf_counter() - transform_started_at,
    )

    output_dir = os.path.dirname(output_path) or '.'
    logger.info(
        'subtitle_sync: saving subtitles output=%s output_dir_exists=%s',
        output_path,
        os.path.isdir(output_dir),
    )
    save_started_at = time.perf_counter()
    subs.save(output_path, encoding=loaded.encoding)
    logger.info(
        'subtitle_sync: save completed output=%s exists=%s size=%s in %.2fs',
        output_path,
        os.path.exists(output_path),
        os.path.getsize(output_path) if os.path.exists(output_path) else None,
        time.perf_counter() - save_started_at,
    )


def build_output_path(subtitle_path: str) -> str:
    source = Path(subtitle_path)
    base = source.with_suffix('')
    logger.info('subtitle_sync: build output path start subtitle=%s', subtitle_path)
    candidate = base.parent / f'{base.name}.synced.srt'
    logger.info('subtitle_sync: resolved output path=%s', candidate)
    return str(candidate)


def probe_video_metadata(video_path: str) -> VideoMetadata:
    if not shutil.which('ffprobe'):
        raise SubtitleSyncError('ffprobe is not installed or not found in PATH')
    cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-show_streams', '-show_format', video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SubtitleSyncError(f'ffprobe failed: {(result.stderr or result.stdout or "unknown error").strip()}')

    payload = json.loads(result.stdout or '{}')
    format_data = payload.get('format') or {}
    streams = payload.get('streams') or []
    try:
        duration_seconds = float(format_data.get('duration') or 0.0)
    except Exception as exc:
        raise SubtitleSyncError('Could not determine video duration') from exc

    audio_streams: List[AudioStreamInfo] = []
    for stream in streams:
        if stream.get('codec_type') != 'audio':
            continue
        tags = stream.get('tags') or {}
        raw_lang = str(tags.get('language') or '').strip() or None
        alpha3 = normalize_language_code(raw_lang)
        audio_streams.append(
            AudioStreamInfo(
                index=int(stream.get('index')),
                codec_name=str(stream.get('codec_name') or ''),
                language_alpha3=alpha3,
                language_raw=raw_lang,
            )
        )

    return VideoMetadata(duration_seconds=duration_seconds, audio_streams=audio_streams)


def select_audio_stream(metadata: VideoMetadata, subtitle_language_alpha3: str) -> AudioStreamInfo:
    for stream in metadata.audio_streams:
        if stream.language_alpha3 and stream.language_alpha3.lower() == subtitle_language_alpha3.lower():
            return stream
    raise SubtitleSyncError(
        f'No audio stream found for subtitle language {subtitle_language_alpha3}'
    )


def extract_audio_window(video_path: str, stream_index: int, window: AudioSampleWindow, temp_dir: str) -> str:
    if not shutil.which('ffmpeg'):
        raise SubtitleSyncError('ffmpeg is not installed or not found in PATH')
    output_path = os.path.join(temp_dir, f'{window.name}.wav')
    started_at = time.perf_counter()
    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-ss', f'{window.start_seconds:.3f}',
        '-t', f'{window.duration_seconds:.3f}',
        '-i', video_path,
        '-map', f'0:{stream_index}',
        '-ac', '1',
        '-ar', '16000',
        '-vn',
        '-c:a', 'pcm_s16le',
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SubtitleSyncError(f'ffmpeg audio extraction failed: {(result.stderr or result.stdout or "unknown error").strip()}')
    logger.info(
        'subtitle_sync: extracted %s window audio in %.2fs (stream=%s start=%.3fs duration=%.3fs)',
        window.name,
        time.perf_counter() - started_at,
        stream_index,
        window.start_seconds,
        window.duration_seconds,
    )
    return output_path


@lru_cache(maxsize=4)
def _get_whisper_model(model_name: str, device: str, compute_type: str, cpu_threads: int, num_workers: int):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SubtitleSyncError('faster-whisper is not installed') from exc

    started_at = time.perf_counter()
    model = WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        num_workers=num_workers,
    )
    logger.info(
        'subtitle_sync: initialized whisper model name=%s device=%s compute_type=%s cpu_threads=%s num_workers=%s in %.2fs',
        model_name,
        device,
        compute_type,
        cpu_threads,
        num_workers,
        time.perf_counter() - started_at,
    )
    return model


def transcribe_audio(
    audio_path: str,
    language_alpha2: str,
    config: Optional[WhisperTranscriptionConfig] = None,
) -> List[TranscriptSegment]:
    config = config or default_whisper_transcription_config()
    model = _get_whisper_model(
        config.model_name,
        config.device,
        config.compute_type,
        max(config.cpu_threads, 1),
        max(config.num_workers, 1),
    )
    started_at = time.perf_counter()
    logger.info(
        'subtitle_sync: starting transcription audio=%s model=%s language=%s beam_size=%s best_of=%s patience=%s temperature=%s condition_on_previous_text=%s vad_filter=%s',
        audio_path,
        config.model_name,
        language_alpha2,
        config.beam_size,
        config.best_of,
        config.patience,
        config.temperature,
        config.condition_on_previous_text,
        config.vad_filter,
    )
    segments, _info = model.transcribe(
        audio_path,
        language=language_alpha2,
        vad_filter=config.vad_filter,
        beam_size=max(config.beam_size, 1),
        best_of=max(config.best_of, 1),
        patience=max(config.patience, 0.0),
        condition_on_previous_text=config.condition_on_previous_text,
        temperature=max(config.temperature, 0.0),
        word_timestamps=config.word_timestamps,
    )
    result: List[TranscriptSegment] = []
    for segment in segments:
        text = str(getattr(segment, 'text', '') or '').strip()
        if not text:
            continue
        result.append(
            TranscriptSegment(
                text=text,
                start_seconds=float(getattr(segment, 'start', 0.0)),
                end_seconds=float(getattr(segment, 'end', 0.0)),
            )
        )
    if not result:
        raise SubtitleSyncError(f'No speech segments produced for {audio_path}')
    logger.info(
        'subtitle_sync: completed transcription audio=%s segments=%s in %.2fs',
        audio_path,
        len(result),
        time.perf_counter() - started_at,
    )
    return result


def plan_sync(
    video_path: str,
    subtitle_path: str,
    sample_minutes: int,
    transcription_config: Optional[WhisperTranscriptionConfig] = None,
    matching_config: Optional[SyncMatchingConfig] = None,
    metadata_provider: Callable[[str], VideoMetadata] = probe_video_metadata,
    audio_extractor: Callable[[str, int, AudioSampleWindow, str], str] = extract_audio_window,
    transcriber: Callable[[str, str, Optional[WhisperTranscriptionConfig]], List[TranscriptSegment]] = transcribe_audio,
) -> SyncPlan:
    transcription_config = transcription_config or default_whisper_transcription_config()
    matching_config = matching_config or default_sync_matching_config()
    sync_started_at = time.perf_counter()
    logger.info('subtitle_sync: plan start video=%s subtitle=%s sample_minutes=%s', video_path, subtitle_path, sample_minutes)

    started_at = time.perf_counter()
    subtitle_language_alpha2, subtitle_language_alpha3 = parse_subtitle_language(subtitle_path)
    logger.info(
        'subtitle_sync: parsed subtitle language alpha2=%s alpha3=%s in %.2fs',
        subtitle_language_alpha2,
        subtitle_language_alpha3,
        time.perf_counter() - started_at,
    )

    started_at = time.perf_counter()
    metadata = metadata_provider(video_path)
    audio_stream = select_audio_stream(metadata, subtitle_language_alpha3)
    sample_windows = build_sample_windows(metadata.duration_seconds, sample_minutes)
    logger.info(
        'subtitle_sync: probed video metadata duration=%.3fs audio_stream_index=%s in %.2fs',
        metadata.duration_seconds,
        audio_stream.index,
        time.perf_counter() - started_at,
    )

    started_at = time.perf_counter()
    cues = load_normalized_cues(subtitle_path)
    cue_windows = build_sliding_windows(cues, max_window_size=matching_config.anchor_max_window_size)
    logger.info(
        'subtitle_sync: loaded subtitle cues count=%s sliding_windows=%s in %.2fs',
        len(cues),
        len(cue_windows),
        time.perf_counter() - started_at,
    )

    transcripts = {}
    with tempfile.TemporaryDirectory(prefix='subtitle-sync-') as temp_dir:
        for window in sample_windows:
            audio_path = audio_extractor(video_path, audio_stream.index, window, temp_dir)
            segments = transcriber(audio_path, subtitle_language_alpha2, transcription_config)
            transcripts[window.name] = [
                TranscriptSegment(
                    text=segment.text,
                    start_seconds=segment.start_seconds + window.start_seconds,
                    end_seconds=segment.end_seconds + window.start_seconds,
                )
                for segment in segments
            ]
            logger.info(
                'subtitle_sync: mapped %s transcript to absolute timeline segments=%s',
                window.name,
                len(transcripts[window.name]),
            )

    started_at = time.perf_counter()
    start_candidates = build_anchor_candidates(
        transcripts['start'],
        from_end=False,
        max_candidates_from_edges=matching_config.anchor_max_candidates_from_edges,
        max_phrase_segments=matching_config.anchor_max_phrase_segments,
        min_text_length=matching_config.anchor_min_text_length,
    )
    end_candidates = build_anchor_candidates(
        transcripts['end'],
        from_end=True,
        max_candidates_from_edges=matching_config.anchor_max_candidates_from_edges,
        max_phrase_segments=matching_config.anchor_max_phrase_segments,
        min_text_length=matching_config.anchor_min_text_length,
    )
    logger.info(
        'subtitle_sync: built anchor candidates start=%s end=%s in %.2fs',
        len(start_candidates),
        len(end_candidates),
        time.perf_counter() - started_at,
    )

    started_at = time.perf_counter()
    start_anchor = find_anchor_match(
        cues=cues,
        windows=cue_windows,
        candidates=start_candidates,
        window_name='start',
        search_from_end=False,
        min_similarity=matching_config.anchor_min_similarity,
    )
    end_anchor = find_anchor_match(
        cues=cues,
        windows=cue_windows,
        candidates=end_candidates,
        window_name='end',
        search_from_end=True,
        min_similarity=matching_config.anchor_min_similarity,
    )
    logger.info(
        'subtitle_sync: matched anchors start_index=%s end_index=%s start_similarity=%.3f end_similarity=%.3f in %.2fs',
        start_anchor.subtitle_index,
        end_anchor.subtitle_index,
        start_anchor.similarity,
        end_anchor.similarity,
        time.perf_counter() - started_at,
    )

    started_at = time.perf_counter()
    offset_seconds, scale = calculate_linear_transform(
        start_anchor,
        end_anchor,
        max_scale_delta=matching_config.max_scale_delta,
        max_end_error_seconds=matching_config.max_end_error_seconds,
    )
    logger.info(
        'subtitle_sync: computed linear transform offset=%.3fs scale=%.6f in %.2fs total=%.2fs',
        offset_seconds,
        scale,
        time.perf_counter() - started_at,
        time.perf_counter() - sync_started_at,
    )

    return SyncPlan(
        subtitle_language_alpha2=subtitle_language_alpha2,
        subtitle_language_alpha3=subtitle_language_alpha3,
        audio_stream_index=audio_stream.index,
        sample_minutes=sample_minutes,
        sample_windows=sample_windows,
        start_anchor=start_anchor,
        end_anchor=end_anchor,
        offset_seconds=offset_seconds,
        scale=scale,
    )


@dataclass(frozen=True)
class HeavyTranscriptPhrase:
    phrase_index: int
    transcript_seconds: float
    raw_text: str
    normalized_text: str


@dataclass(frozen=True)
class HeavyMatchCandidate:
    phrase_index: int
    transcript_seconds: float
    subtitle_index: int
    subtitle_end_index: int
    subtitle_seconds: float
    similarity: float
    score: float
    source: str = 'local'
    llm_similarity: float | None = None


@dataclass(frozen=True)
class VoiceActivitySegment:
    start_seconds: float
    end_seconds: float
    text: str
    no_speech_prob: float | None


def build_full_transcript_segments(
    video_path: str,
    stream_index: int,
    duration_seconds: float,
    language_alpha2: str,
    transcription_config: WhisperTranscriptionConfig,
    chunk_minutes: int = 6,
) -> tuple[List[TranscriptSegment], List[VoiceActivitySegment]]:
    chunk_seconds = max(float(chunk_minutes) * 60.0, 60.0)
    segments: List[TranscriptSegment] = []
    voice_segments: List[VoiceActivitySegment] = []
    model = _get_whisper_model(
        transcription_config.model_name,
        transcription_config.device,
        transcription_config.compute_type,
        max(transcription_config.cpu_threads, 1),
        max(transcription_config.num_workers, 1),
    )

    with tempfile.TemporaryDirectory(prefix='subtitle-sync-heavy-transcript-') as temp_dir:
        chunk_index = 0
        chunk_start = 0.0
        while chunk_start < duration_seconds:
            chunk_duration = min(chunk_seconds, duration_seconds - chunk_start)
            window = AudioSampleWindow(
                name=f'full_{chunk_index}',
                start_seconds=chunk_start,
                duration_seconds=chunk_duration,
            )
            audio_path = extract_audio_window(video_path, stream_index, window, temp_dir)
            whisper_segments, _info = model.transcribe(
                audio_path,
                language=language_alpha2,
                vad_filter=transcription_config.vad_filter,
                beam_size=max(transcription_config.beam_size, 1),
                best_of=max(transcription_config.best_of, 1),
                patience=max(transcription_config.patience, 0.0),
                condition_on_previous_text=transcription_config.condition_on_previous_text,
                temperature=max(transcription_config.temperature, 0.0),
                word_timestamps=transcription_config.word_timestamps,
            )
            for whisper_segment in whisper_segments:
                text = str(getattr(whisper_segment, 'text', '') or '').strip()
                if not text:
                    continue

                words = list(getattr(whisper_segment, 'words', []) or [])
                timed_words = [
                    word for word in words
                    if getattr(word, 'start', None) is not None and getattr(word, 'end', None) is not None
                ]
                relative_start = float(getattr(whisper_segment, 'start', 0.0))
                relative_end = float(getattr(whisper_segment, 'end', 0.0))
                if timed_words:
                    relative_start = float(getattr(timed_words[0], 'start', relative_start))
                    relative_end = float(getattr(timed_words[-1], 'end', relative_end))

                absolute_start = relative_start + chunk_start
                absolute_end = relative_end + chunk_start
                no_speech_prob = getattr(whisper_segment, 'no_speech_prob', None)
                if no_speech_prob is not None:
                    no_speech_prob = float(no_speech_prob)

                segments.append(
                    TranscriptSegment(
                        text=text,
                        start_seconds=absolute_start,
                        end_seconds=absolute_end,
                    )
                )
                voice_segments.append(
                    VoiceActivitySegment(
                        start_seconds=absolute_start,
                        end_seconds=absolute_end,
                        text=text,
                        no_speech_prob=no_speech_prob,
                    )
                )

            chunk_start += chunk_duration
            chunk_index += 1

    if not segments:
        raise SubtitleSyncError('Heavy path did not produce any transcript segments')
    return segments, voice_segments


def build_heavy_transcript_phrases(
    transcript_segments: List[TranscriptSegment],
    step_segments: int = 1,
    max_phrase_segments: int = 3,
    min_text_length: int = 18,
    max_gap_seconds: float | None = None,
) -> List[HeavyTranscriptPhrase]:
    phrases: List[HeavyTranscriptPhrase] = []
    for start_index in range(0, len(transcript_segments), max(step_segments, 1)):
        parts: List[str] = []
        for end_index in range(start_index, min(len(transcript_segments), start_index + max_phrase_segments)):
            if end_index > start_index and max_gap_seconds is not None:
                gap_seconds = transcript_segments[end_index].start_seconds - transcript_segments[end_index - 1].end_seconds
                if gap_seconds > max_gap_seconds:
                    break
            parts.append(transcript_segments[end_index].text)
            raw_text = _collapse_whitespace(' '.join(parts))
            normalized = normalize_text(raw_text)
            if len(normalized) < min_text_length:
                continue
            phrases.append(
                HeavyTranscriptPhrase(
                    phrase_index=len(phrases),
                    transcript_seconds=transcript_segments[start_index].start_seconds,
                    raw_text=raw_text,
                    normalized_text=normalized,
                )
            )
            break

    if len(phrases) < 3:
        raise SubtitleSyncError('Heavy path could not build enough transcript phrases for alignment')
    return phrases


def compute_heavy_text_similarity(
    phrase_text: str,
    subtitle_text: str,
    heavy_config: Optional[HeavySyncConfig] = None,
) -> float:
    phrase_tokens = [token for token in phrase_text.split() if token]
    subtitle_tokens = [token for token in subtitle_text.split() if token]
    if not phrase_tokens or not subtitle_tokens:
        return 0.0

    heavy_config = heavy_config or default_heavy_sync_config()
    embedding_similarity = _compute_embedding_similarity(
        phrase_text,
        subtitle_text,
        heavy_config.embedding_model_name,
    )
    fuzzy_similarity = _compute_fuzzy_similarity(phrase_text, subtitle_text)
    phonetic_similarity = _compute_phonetic_similarity(phrase_tokens, subtitle_tokens)
    return max(0.0, min((0.45 * embedding_similarity) + (0.20 * fuzzy_similarity) + (0.35 * phonetic_similarity), 1.0))


def _get_heavy_embedding_model(model_name: str):
    global _HEAVY_EMBEDDING_MODEL, _HEAVY_EMBEDDING_MODEL_LOAD_FAILED, _HEAVY_EMBEDDING_MODEL_NAME

    if _HEAVY_EMBEDDING_MODEL is not None and _HEAVY_EMBEDDING_MODEL_NAME == model_name:
        return _HEAVY_EMBEDDING_MODEL
    if _HEAVY_EMBEDDING_MODEL_NAME != model_name:
        _HEAVY_EMBEDDING_MODEL = None
        _HEAVY_EMBEDDING_MODEL_LOAD_FAILED = False
        _HEAVY_EMBEDDING_MODEL_NAME = model_name
    if _HEAVY_EMBEDDING_MODEL_LOAD_FAILED:
        return None

    try:
        from sentence_transformers import SentenceTransformer

        _HEAVY_EMBEDDING_MODEL = SentenceTransformer(model_name)
    except Exception:
        _HEAVY_EMBEDDING_MODEL_LOAD_FAILED = True
        return None
    return _HEAVY_EMBEDDING_MODEL


@lru_cache(maxsize=8192)
def _encode_heavy_text(model_name: str, role: str, text: str) -> tuple[float, ...] | None:
    model = _get_heavy_embedding_model(model_name)
    if model is None:
        return None

    vector = model.encode(
        f'{role}: {text}',
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return tuple(float(value) for value in vector)


def _compute_embedding_similarity(phrase_text: str, subtitle_text: str, model_name: str) -> float:
    phrase_vector = _encode_heavy_text(model_name, 'query', phrase_text)
    subtitle_vector = _encode_heavy_text(model_name, 'passage', subtitle_text)
    if phrase_vector is None or subtitle_vector is None:
        return _similarity(phrase_text, subtitle_text)

    return max(0.0, min(sum(left * right for left, right in zip(phrase_vector, subtitle_vector)), 1.0))


def _compute_fuzzy_similarity(phrase_text: str, subtitle_text: str) -> float:
    if rapidfuzz_fuzz is None:
        return _similarity(phrase_text, subtitle_text)

    ratio = rapidfuzz_fuzz.ratio(phrase_text, subtitle_text) / 100.0
    partial_ratio = rapidfuzz_fuzz.partial_ratio(phrase_text, subtitle_text) / 100.0
    token_set_ratio = rapidfuzz_fuzz.token_set_ratio(phrase_text, subtitle_text) / 100.0
    return max(ratio, (token_set_ratio * 0.65) + (partial_ratio * 0.35))


def _compute_phonetic_similarity(phrase_tokens: List[str], subtitle_tokens: List[str]) -> float:
    phrase_codes = [code for code in (_cologne_phonetics(token) for token in phrase_tokens) if code]
    subtitle_codes = [code for code in (_cologne_phonetics(token) for token in subtitle_tokens) if code]
    if not phrase_codes or not subtitle_codes:
        return 0.0

    phrase_code_text = ' '.join(phrase_codes)
    subtitle_code_text = ' '.join(subtitle_codes)
    sequence_ratio = _similarity(phrase_code_text, subtitle_code_text)
    phrase_set = set(phrase_codes)
    subtitle_set = set(subtitle_codes)
    overlap_ratio = len(phrase_set & subtitle_set) / max(len(phrase_set), len(subtitle_set), 1)
    return max(sequence_ratio, overlap_ratio)


def _cologne_phonetics(text: str) -> str:
    normalized = (
        text.upper()
        .replace('Ä', 'A')
        .replace('Ö', 'O')
        .replace('Ü', 'U')
        .replace('ß', 'SS')
    )
    letters = [char for char in normalized if 'A' <= char <= 'Z']
    if not letters:
        return ''

    codes: List[str] = []
    previous_code = ''
    for index, char in enumerate(letters):
        previous_char = letters[index - 1] if index > 0 else ''
        next_char = letters[index + 1] if index + 1 < len(letters) else ''
        code = _cologne_code_for_char(char, previous_char, next_char, index == 0)
        if not code:
            continue
        for digit in code:
            if digit == previous_code:
                continue
            codes.append(digit)
            previous_code = digit

    if not codes:
        return ''
    head = codes[0]
    tail = [digit for digit in codes[1:] if digit != '0']
    return ''.join([head] + tail)


def _cologne_code_for_char(char: str, previous_char: str, next_char: str, is_start: bool) -> str:
    if char in 'AEIJOUY':
        return '0'
    if char == 'H':
        return ''
    if char == 'B':
        return '1'
    if char == 'P':
        return '3' if next_char == 'H' else '1'
    if char in 'DT':
        return '8' if next_char in 'CSZ' else '2'
    if char in 'FVW':
        return '3'
    if char in 'GKQ':
        return '4'
    if char == 'X':
        return '8' if previous_char in 'CKQ' else '48'
    if char == 'L':
        return '5'
    if char in 'MN':
        return '6'
    if char == 'R':
        return '7'
    if char in 'SZ':
        return '8'
    if char == 'C':
        if is_start:
            return '4' if next_char in 'AHKLOQRUX' else '8'
        if previous_char in 'SZ' or next_char in 'AHKOQUX':
            return '4'
        return '8'
    return ''


def build_heavy_match_candidates(
    phrase: HeavyTranscriptPhrase,
    cue_windows: List[SlidingCueWindow],
    cue_count: int,
    phrase_count: int,
    matching_config: SyncMatchingConfig,
    search_radius: int,
    heavy_config: Optional[HeavySyncConfig] = None,
) -> List[HeavyMatchCandidate]:
    cue_denominator = max(cue_count - 1, 1)
    phrase_denominator = max(phrase_count - 1, 1)
    expected_index = int(round((phrase.phrase_index / phrase_denominator) * cue_denominator))
    lower_bound = max(0, expected_index - search_radius)
    upper_bound = min(cue_count - 1, expected_index + search_radius)

    candidates: List[HeavyMatchCandidate] = []
    for window in cue_windows:
        if window.start_index < lower_bound or window.end_index > upper_bound:
            continue
        similarity = compute_heavy_text_similarity(
            phrase.normalized_text,
            window.normalized_text,
            heavy_config=heavy_config,
        )
        if similarity < matching_config.anchor_min_similarity:
            continue
        positional_penalty = abs(window.start_index - expected_index) / max(search_radius, 1)
        score = similarity - (positional_penalty * 0.18)
        candidates.append(
            HeavyMatchCandidate(
                phrase_index=phrase.phrase_index,
                transcript_seconds=phrase.transcript_seconds,
                subtitle_index=window.start_index,
                subtitle_end_index=window.end_index,
                subtitle_seconds=window.start_seconds,
                similarity=similarity,
                score=score,
                source='local',
            )
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            candidate.similarity,
            candidate.subtitle_end_index,
            -abs(candidate.subtitle_index - expected_index),
        ),
        reverse=True,
    )
    return candidates[:max(matching_config.anchor_max_candidates_from_edges, 1)]


def _heavy_phrase_token_count(phrase: HeavyTranscriptPhrase) -> int:
    return len([token for token in phrase.normalized_text.split() if token])


def _should_locally_rerank_heavy_phrase(
    phrase: HeavyTranscriptPhrase,
    candidates: List[HeavyMatchCandidate],
    strong_similarity: float,
) -> bool:
    token_count = _heavy_phrase_token_count(phrase)
    if token_count <= 3:
        return True
    if len(phrase.normalized_text) <= 24:
        return True
    if not candidates:
        return False
    return candidates[0].similarity < strong_similarity


def rerank_heavy_candidates_locally(
    phrases: List[HeavyTranscriptPhrase],
    cue_count: int,
    candidate_lists: List[List[HeavyMatchCandidate]],
    local_radius: int = 18,
    proximity_weight: float = 0.18,
    strong_similarity: float = 0.72,
) -> List[List[HeavyMatchCandidate]]:
    if not phrases or not candidate_lists:
        return candidate_lists

    try:
        initial_alignment = select_monotonic_heavy_alignment(phrases, cue_count, candidate_lists)
    except SubtitleSyncError:
        return candidate_lists

    strong_matches = {
        match.phrase_index: match
        for match in initial_alignment
        if match.similarity >= strong_similarity
    }
    if not strong_matches:
        return candidate_lists

    reranked_lists: List[List[HeavyMatchCandidate]] = []
    reranked_phrase_count = 0
    for phrase, candidates in zip(phrases, candidate_lists):
        if not candidates or not _should_locally_rerank_heavy_phrase(phrase, candidates, strong_similarity):
            reranked_lists.append(candidates)
            continue

        neighbor_matches = []
        previous_match = strong_matches.get(phrase.phrase_index - 1)
        next_match = strong_matches.get(phrase.phrase_index + 1)
        if previous_match is not None:
            neighbor_matches.append(previous_match)
        if next_match is not None:
            neighbor_matches.append(next_match)
        if not neighbor_matches:
            reranked_lists.append(candidates)
            continue

        boosted_candidates: List[HeavyMatchCandidate] = []
        for candidate in candidates:
            best_local_bonus = 0.0
            for neighbor_match in neighbor_matches:
                distance = abs(candidate.subtitle_index - neighbor_match.subtitle_index)
                if distance > local_radius:
                    continue
                proximity_bonus = (1.0 - (distance / max(local_radius, 1))) * proximity_weight
                if proximity_bonus > best_local_bonus:
                    best_local_bonus = proximity_bonus
            boosted_candidates.append(
                HeavyMatchCandidate(
                    phrase_index=candidate.phrase_index,
                    transcript_seconds=candidate.transcript_seconds,
                    subtitle_index=candidate.subtitle_index,
                    subtitle_end_index=candidate.subtitle_end_index,
                    subtitle_seconds=candidate.subtitle_seconds,
                    similarity=candidate.similarity,
                    score=candidate.score + best_local_bonus,
                )
            )

        boosted_candidates.sort(
            key=lambda candidate: (
                candidate.score,
                candidate.similarity,
                candidate.subtitle_end_index,
                candidate.subtitle_index,
            ),
            reverse=True,
        )
        reranked_lists.append(boosted_candidates)
        reranked_phrase_count += 1

    if reranked_phrase_count > 0:
        logger.info(
            'subtitle_sync: locally reranked heavy candidates phrases=%s strong_matches=%s radius=%s weight=%.2f threshold=%.2f',
            reranked_phrase_count,
            len(strong_matches),
            local_radius,
            proximity_weight,
            strong_similarity,
        )

    return reranked_lists


def _heavy_transition_penalty(
    previous: HeavyMatchCandidate,
    current: HeavyMatchCandidate,
    cue_count: int,
    phrase_count: int,
) -> float:
    subtitle_delta = current.subtitle_seconds - previous.subtitle_seconds
    transcript_delta = current.transcript_seconds - previous.transcript_seconds
    if subtitle_delta <= 0.0 or transcript_delta <= 0.0:
        return 1e6

    scale = transcript_delta / subtitle_delta
    phrase_gap = max(current.phrase_index - previous.phrase_index - 1, 0)
    cue_gap = max(current.subtitle_index - previous.subtitle_end_index - 1, 0)
    cue_gap_ratio = cue_gap / max(cue_count - 1, 1)
    phrase_gap_ratio = phrase_gap / max(phrase_count - 1, 1)
    strong_transition = _is_strong_heavy_match(previous) and _is_strong_heavy_match(current)
    offset_recovery = phrase_gap <= 1 and _is_offset_recovery_candidate(current)
    gap_delta = abs(cue_gap_ratio - phrase_gap_ratio)
    gap_tolerance = 0.08 if offset_recovery else (0.05 if strong_transition else 0.03)
    gap_penalty = max(0.0, gap_delta - gap_tolerance) * (0.55 if offset_recovery else (0.85 if strong_transition else 1.40))

    compression_threshold = 0.20 if offset_recovery else (0.35 if strong_transition else 0.45)
    expansion_threshold = 2.40 if offset_recovery else (2.10 if strong_transition else 1.90)
    compression_penalty = max(0.0, compression_threshold - scale) * (0.45 if offset_recovery else (1.40 if strong_transition else 2.40))
    expansion_penalty = max(0.0, scale - expansion_threshold) * (0.45 if offset_recovery else (0.80 if strong_transition else 1.10))

    jump_allowance = max(phrase_gap * 9, 18 if offset_recovery else (14 if strong_transition else 10))
    jump_penalty = max(0.0, cue_gap - jump_allowance) * (0.004 if offset_recovery else (0.008 if strong_transition else 0.015))
    return gap_penalty + compression_penalty + expansion_penalty + jump_penalty


def _heavy_skip_penalty(skipped_phrase_count: int) -> float:
    if skipped_phrase_count <= 0:
        return 0.0
    return skipped_phrase_count * 0.22


def _is_strong_heavy_match(candidate: HeavyMatchCandidate) -> bool:
    if candidate.llm_similarity is not None:
        return candidate.llm_similarity >= 0.74
    return candidate.similarity >= 0.74


def _is_offset_recovery_candidate(candidate: HeavyMatchCandidate) -> bool:
    local_similarity = candidate.llm_similarity if candidate.llm_similarity is not None else candidate.similarity
    return local_similarity >= 0.69 and candidate.score >= 0.68


def _heavy_anchor_bonus(candidate: HeavyMatchCandidate) -> float:
    local_similarity = candidate.llm_similarity if candidate.llm_similarity is not None else candidate.similarity
    if local_similarity < 0.68:
        return 0.0
    return min((local_similarity - 0.68) * 0.9, 0.08)


def _heavy_boundary_penalty(candidate: HeavyMatchCandidate, cue_count: int, phrase_count: int) -> float:
    cue_ratio = candidate.subtitle_index / max(cue_count - 1, 1)
    phrase_ratio = candidate.phrase_index / max(phrase_count - 1, 1)
    divergence = abs(cue_ratio - phrase_ratio)
    if divergence <= 0.12:
        return 0.0
    return (divergence - 0.12) * 0.35


def select_monotonic_heavy_alignment(
    phrases: List[HeavyTranscriptPhrase],
    cue_count: int,
    candidate_lists: List[List[HeavyMatchCandidate]],
) -> List[HeavyMatchCandidate]:
    flattened = [candidate for candidates in candidate_lists for candidate in candidates]
    if not flattened:
        raise SubtitleSyncError('Heavy path alignment did not produce enough monotonic matches')

    flattened.sort(
        key=lambda candidate: (
            candidate.phrase_index,
            candidate.subtitle_index,
            candidate.subtitle_end_index,
            candidate.score,
        )
    )
    phrase_count = len(phrases)
    best_scores: List[float] = []
    match_counts: List[int] = []
    previous_indices: List[int] = []

    for index, candidate in enumerate(flattened):
        best_score = (
            candidate.score
            + _heavy_anchor_bonus(candidate)
            - _heavy_skip_penalty(candidate.phrase_index)
            - _heavy_boundary_penalty(candidate, cue_count, phrase_count)
        )
        best_previous = -1
        best_count = 1
        for previous_index in range(index):
            previous_candidate = flattened[previous_index]
            if previous_candidate.phrase_index >= candidate.phrase_index:
                continue
            if previous_candidate.subtitle_end_index >= candidate.subtitle_index:
                continue

            skipped_phrases = candidate.phrase_index - previous_candidate.phrase_index - 1
            transition_penalty = _heavy_transition_penalty(previous_candidate, candidate, cue_count, phrase_count)
            chain_score = (
                best_scores[previous_index]
                + candidate.score
                + _heavy_anchor_bonus(candidate)
                - transition_penalty
                - _heavy_skip_penalty(skipped_phrases)
            )
            chain_count = match_counts[previous_index] + 1
            if chain_score > best_score or (
                abs(chain_score - best_score) < 1e-9 and chain_count > best_count
            ):
                best_score = chain_score
                best_previous = previous_index
                best_count = chain_count

        best_scores.append(best_score)
        match_counts.append(best_count)
        previous_indices.append(best_previous)

    def terminal_score(candidate_index: int) -> float:
        trailing_skips = phrase_count - flattened[candidate_index].phrase_index - 1
        return (
            best_scores[candidate_index]
            - _heavy_skip_penalty(trailing_skips)
            - _heavy_boundary_penalty(flattened[candidate_index], cue_count, phrase_count)
        )

    best_terminal_index = max(
        range(len(flattened)),
        key=lambda index: (terminal_score(index), match_counts[index]),
    )
    selected: List[HeavyMatchCandidate] = []
    cursor = best_terminal_index
    while cursor >= 0:
        selected.append(flattened[cursor])
        cursor = previous_indices[cursor]
    selected.reverse()

    unique_selected: List[HeavyMatchCandidate] = []
    last_phrase_index = -1
    last_subtitle_index = -1
    last_subtitle_end_index = -1
    for candidate in selected:
        if candidate.phrase_index <= last_phrase_index:
            continue
        if candidate.subtitle_index <= last_subtitle_index and candidate.subtitle_end_index <= last_subtitle_end_index:
            continue
        if candidate.subtitle_index <= last_subtitle_end_index:
            continue
        unique_selected.append(candidate)
        last_phrase_index = candidate.phrase_index
        last_subtitle_index = candidate.subtitle_index
        last_subtitle_end_index = candidate.subtitle_end_index

    if len(unique_selected) < 4:
        raise SubtitleSyncError('Heavy path alignment did not produce enough monotonic matches')
    return unique_selected


def _pair_scale_is_stable(
    left_anchor: AnchorMatch,
    right_anchor: AnchorMatch,
    min_anchor_scale: float,
    max_anchor_scale: float,
) -> bool:
    subtitle_delta = right_anchor.subtitle_seconds - left_anchor.subtitle_seconds
    transcript_delta = right_anchor.transcript_seconds - left_anchor.transcript_seconds
    if subtitle_delta <= 0.0 or transcript_delta <= 0.0:
        return False
    scale = transcript_delta / subtitle_delta
    return min_anchor_scale <= scale <= max_anchor_scale


def prune_unstable_heavy_anchors(
    anchors: List[AnchorMatch],
    min_anchor_scale: float = 0.35,
    max_anchor_scale: float = 2.50,
    min_anchor_count: int = 4,
) -> List[AnchorMatch]:
    ordered = sorted(anchors, key=lambda anchor: anchor.subtitle_seconds)
    if len(ordered) <= min_anchor_count:
        return ordered

    changed = True
    while changed and len(ordered) > min_anchor_count:
        changed = False
        for index in range(len(ordered) - 1):
            left_anchor = ordered[index]
            right_anchor = ordered[index + 1]
            if _pair_scale_is_stable(left_anchor, right_anchor, min_anchor_scale, max_anchor_scale):
                continue

            candidate_indexes: List[int] = []
            if 0 < index < len(ordered) - 1:
                candidate_indexes.append(index)
            if 0 < index + 1 < len(ordered) - 1:
                candidate_indexes.append(index + 1)
            if not candidate_indexes:
                return ordered

            best_choice = None
            best_key = None
            for candidate_index in candidate_indexes:
                trial = ordered[:candidate_index] + ordered[candidate_index + 1:]
                locally_stable = True
                trial_start = max(candidate_index - 1, 0)
                trial_stop = min(candidate_index + 1, len(trial) - 1)
                for trial_index in range(trial_start, trial_stop):
                    if not _pair_scale_is_stable(trial[trial_index], trial[trial_index + 1], min_anchor_scale, max_anchor_scale):
                        locally_stable = False
                        break

                similarity = ordered[candidate_index].similarity
                is_edge_adjacent = candidate_index in (1, len(ordered) - 2)
                choice_key = (
                    locally_stable,
                    not is_edge_adjacent,
                    -similarity,
                )
                if best_key is None or choice_key > best_key:
                    best_key = choice_key
                    best_choice = candidate_index

            if best_choice is None:
                return ordered

            ordered = ordered[:best_choice] + ordered[best_choice + 1:]
            changed = True
            break

    return ordered


def _matches_to_anchor_matches(matches: List[HeavyMatchCandidate]) -> List[AnchorMatch]:
    anchors: List[AnchorMatch] = []
    seen_subtitle_ranges = set()
    for index, match in enumerate(matches, start=1):
        subtitle_range = (match.subtitle_index, match.subtitle_end_index)
        if subtitle_range in seen_subtitle_ranges:
            continue
        seen_subtitle_ranges.add(subtitle_range)
        anchors.append(
            AnchorMatch(
                window_name=f'heavy_{index}',
                transcript_text='',
                transcript_seconds=match.transcript_seconds,
                subtitle_index=match.subtitle_index + 1,
                subtitle_seconds=match.subtitle_seconds,
                similarity=match.similarity,
            )
        )
    return anchors


def thin_heavy_alignment_to_anchors(
    matches: List[HeavyMatchCandidate],
    cue_windows: Optional[List[SlidingCueWindow]] = None,
    target_anchor_count: int = 10,
    preserve_similarity: float = 0.80,
    preserve_initial_count: int = 8,
) -> List[AnchorMatch]:
    del cue_windows
    if len(matches) < 2:
        raise SubtitleSyncError('Heavy path needs at least two matches to create anchors')

    ordered = sorted(matches, key=lambda match: match.subtitle_index)
    preserved_matches: List[HeavyMatchCandidate] = []
    preserved_pairs = set()

    for index, match in enumerate(ordered):
        should_preserve = index < preserve_initial_count or match.similarity >= preserve_similarity
        if not should_preserve:
            continue
        pair = (match.phrase_index, match.subtitle_index, match.subtitle_end_index)
        if pair in preserved_pairs:
            continue
        preserved_matches.append(match)
        preserved_pairs.add(pair)

    if len(ordered) <= target_anchor_count:
        chosen_matches = ordered
    else:
        stride = max(len(ordered) // max(target_anchor_count - 1, 1), 1)
        chosen_matches = [ordered[0]]
        chosen_matches.extend(ordered[index] for index in range(stride, len(ordered) - 1, stride))
        chosen_matches.append(ordered[-1])

    merged_matches: List[HeavyMatchCandidate] = []
    seen_pairs = set()
    for match in sorted(preserved_matches + chosen_matches, key=lambda item: item.subtitle_index):
        pair = (match.phrase_index, match.subtitle_index, match.subtitle_end_index)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        merged_matches.append(match)

    anchors = prune_unstable_heavy_anchors(_matches_to_anchor_matches(merged_matches))
    try:
        validate_heavy_anchor_stability(anchors)
    except SubtitleSyncError:
        anchors = prune_unstable_heavy_anchors(_matches_to_anchor_matches(ordered))

    if len(anchors) < 4:
        raise SubtitleSyncError('Heavy path did not retain enough anchors after thinning')
    return anchors


def validate_heavy_anchor_stability(
    anchors: List[AnchorMatch],
    min_anchor_scale: float = 0.35,
    max_anchor_scale: float = 2.50,
) -> None:
    ordered = sorted(anchors, key=lambda anchor: anchor.subtitle_seconds)
    if len(ordered) < 2:
        raise SubtitleSyncError('Heavy path needs at least two anchors for stability validation')

    for left_anchor, right_anchor in zip(ordered, ordered[1:]):
        subtitle_delta = right_anchor.subtitle_seconds - left_anchor.subtitle_seconds
        transcript_delta = right_anchor.transcript_seconds - left_anchor.transcript_seconds
        if subtitle_delta <= 0.0 or transcript_delta <= 0.0:
            raise SubtitleSyncError('Heavy path anchors are not strictly increasing during stability validation')

        scale = transcript_delta / subtitle_delta
        if scale < min_anchor_scale or scale > max_anchor_scale:
            raise SubtitleSyncError(
                'Heavy path produced an unstable anchor segment '
                f'between subtitle cues {left_anchor.subtitle_index} and {right_anchor.subtitle_index} '
                f'(scale={scale:.3f})'
            )


def build_piecewise_segments(anchors: List[AnchorMatch]) -> List[PiecewiseSegment]:
    ordered = sorted(anchors, key=lambda anchor: anchor.subtitle_seconds)
    if len(ordered) < 2:
        raise SubtitleSyncError('At least two anchors are required for piecewise sync')

    segments = []
    for left_anchor, right_anchor in zip(ordered, ordered[1:]):
        subtitle_delta = right_anchor.subtitle_seconds - left_anchor.subtitle_seconds
        transcript_delta = right_anchor.transcript_seconds - left_anchor.transcript_seconds
        if subtitle_delta <= 0 or transcript_delta <= 0:
            raise SubtitleSyncError('Heavy anchors are not strictly increasing')
        scale = transcript_delta / subtitle_delta
        offset = left_anchor.transcript_seconds - (left_anchor.subtitle_seconds * scale)
        segments.append(
            PiecewiseSegment(
                start_subtitle_seconds=left_anchor.subtitle_seconds,
                end_subtitle_seconds=right_anchor.subtitle_seconds,
                offset_seconds=offset,
                scale=scale,
            )
        )
    return segments


def build_global_baseline(anchors: List[AnchorMatch]) -> PiecewiseSegment:
    ordered = sorted(anchors, key=lambda anchor: anchor.subtitle_seconds)
    if len(ordered) < 2:
        raise SubtitleSyncError('At least two anchors are required for a global baseline')

    left_anchor = ordered[0]
    right_anchor = ordered[-1]
    subtitle_delta = right_anchor.subtitle_seconds - left_anchor.subtitle_seconds
    transcript_delta = right_anchor.transcript_seconds - left_anchor.transcript_seconds
    if subtitle_delta <= 0 or transcript_delta <= 0:
        raise SubtitleSyncError('Global baseline anchors are not strictly increasing')

    scale = transcript_delta / subtitle_delta
    offset = left_anchor.transcript_seconds - (left_anchor.subtitle_seconds * scale)
    return PiecewiseSegment(
        start_subtitle_seconds=left_anchor.subtitle_seconds,
        end_subtitle_seconds=right_anchor.subtitle_seconds,
        offset_seconds=offset,
        scale=scale,
    )


def transform_seconds(
    seconds: float,
    segments: List[PiecewiseSegment],
    baseline: PiecewiseSegment,
    enforce_baseline_guard: bool = False,
) -> float:
    chosen_segment = segments[0]
    for segment in segments:
        if seconds >= segment.start_subtitle_seconds:
            chosen_segment = segment
        if seconds <= segment.end_subtitle_seconds:
            break

    candidate = seconds * chosen_segment.scale + chosen_segment.offset_seconds
    if not enforce_baseline_guard:
        return candidate
    baseline_value = seconds * baseline.scale + baseline.offset_seconds
    if candidate < 0.0 or abs(chosen_segment.scale - baseline.scale) > 0.25:
        return baseline_value
    return candidate


def apply_piecewise_transform(
    subtitle_path: str,
    output_path: str,
    anchors: List[AnchorMatch],
    enforce_baseline_guard: bool = False,
) -> List[PiecewiseSegment]:
    loaded = load_subtitles(subtitle_path)
    subs = loaded.subs
    segments = build_piecewise_segments(anchors)
    baseline = build_global_baseline(anchors)

    for line in subs:
        start_seconds = _timems_to_seconds(line.start)
        end_seconds = _timems_to_seconds(line.end)
        new_start = transform_seconds(
            start_seconds,
            segments,
            baseline,
            enforce_baseline_guard=enforce_baseline_guard,
        )
        new_end = transform_seconds(
            end_seconds,
            segments,
            baseline,
            enforce_baseline_guard=enforce_baseline_guard,
        )
        line.start = _seconds_to_timems(new_start)
        line.end = max(line.start, _seconds_to_timems(new_end))

    subs.save(output_path, encoding=loaded.encoding)
    return segments


def heavy_plan_sync(
    video_path: str,
    subtitle_path: str,
    chunk_minutes: int,
    transcription_config: Optional[WhisperTranscriptionConfig] = None,
    matching_config: Optional[SyncMatchingConfig] = None,
    heavy_config: Optional[HeavySyncConfig] = None,
) -> tuple[SyncPlan, List[AnchorMatch]]:
    transcription_config = transcription_config or default_whisper_transcription_config()
    matching_config = matching_config or default_sync_matching_config()
    heavy_config = heavy_config or default_heavy_sync_config()

    subtitle_language_alpha2, subtitle_language_alpha3 = parse_subtitle_language(subtitle_path)
    metadata = probe_video_metadata(video_path)
    audio_stream = select_audio_stream(metadata, subtitle_language_alpha3)
    cues = load_normalized_cues(subtitle_path)
    cue_windows = build_sliding_windows(
        cues,
        max_window_size=matching_config.anchor_max_window_size,
        max_gap_seconds=heavy_config.max_cue_gap_seconds,
    )
    transcript_segments, _voice_segments = build_full_transcript_segments(
        video_path=video_path,
        stream_index=audio_stream.index,
        duration_seconds=metadata.duration_seconds,
        language_alpha2=subtitle_language_alpha2,
        transcription_config=transcription_config,
        chunk_minutes=chunk_minutes,
    )
    phrases = build_heavy_transcript_phrases(
        transcript_segments,
        step_segments=heavy_config.step_segments,
        max_phrase_segments=matching_config.anchor_max_phrase_segments,
        min_text_length=matching_config.anchor_min_text_length,
        max_gap_seconds=heavy_config.max_transcript_gap_seconds,
    )
    search_radius = max(len(cues) // 12, 70)
    candidate_lists = [
        build_heavy_match_candidates(
            phrase,
            cue_windows,
            cue_count=len(cues),
            phrase_count=len(phrases),
            matching_config=matching_config,
            search_radius=search_radius,
            heavy_config=heavy_config,
        )
        for phrase in phrases
    ]
    candidate_lists = rerank_heavy_candidates_locally(
        phrases,
        len(cues),
        candidate_lists,
    )
    aligned_matches = select_monotonic_heavy_alignment(phrases, len(cues), candidate_lists)
    anchors = thin_heavy_alignment_to_anchors(aligned_matches)
    validate_heavy_anchor_stability(anchors)
    baseline = build_global_baseline(anchors)
    sample_windows = build_full_transcription_windows(metadata.duration_seconds, chunk_minutes)
    plan = SyncPlan(
        subtitle_language_alpha2=subtitle_language_alpha2,
        subtitle_language_alpha3=subtitle_language_alpha3,
        audio_stream_index=audio_stream.index,
        sample_minutes=chunk_minutes,
        sample_windows=sample_windows,
        start_anchor=anchors[0],
        end_anchor=anchors[-1],
        offset_seconds=baseline.offset_seconds,
        scale=baseline.scale,
    )
    return plan, anchors


def heavy_sync_subtitle_file(
    video_path: str,
    subtitle_path: str,
    chunk_minutes: int,
    output_path: Optional[str] = None,
    transcription_config: Optional[WhisperTranscriptionConfig] = None,
    matching_config: Optional[SyncMatchingConfig] = None,
    heavy_config: Optional[HeavySyncConfig] = None,
) -> SyncResult:
    logger.info(
        'subtitle_sync: heavy sync start video=%s subtitle=%s chunk_minutes=%s output_override=%s',
        video_path,
        subtitle_path,
        chunk_minutes,
        output_path,
    )
    plan, anchors = heavy_plan_sync(
        video_path=video_path,
        subtitle_path=subtitle_path,
        chunk_minutes=chunk_minutes,
        transcription_config=transcription_config,
        matching_config=matching_config,
        heavy_config=heavy_config,
    )
    resolved_output = output_path or build_output_path(subtitle_path)
    apply_piecewise_transform(subtitle_path, resolved_output, anchors, enforce_baseline_guard=False)
    logger.info('subtitle_sync: heavy sync complete output=%s', resolved_output)
    return SyncResult(plan=plan, output_path=resolved_output)


def sync_subtitle_file(
    video_path: str,
    subtitle_path: str,
    sample_minutes: int,
    output_path: Optional[str] = None,
    transcription_config: Optional[WhisperTranscriptionConfig] = None,
    matching_config: Optional[SyncMatchingConfig] = None,
    heavy_config: Optional[HeavySyncConfig] = None,
    metadata_provider: Callable[[str], VideoMetadata] = probe_video_metadata,
    audio_extractor: Callable[[str, int, AudioSampleWindow, str], str] = extract_audio_window,
    transcriber: Callable[[str, str, Optional[WhisperTranscriptionConfig]], List[TranscriptSegment]] = transcribe_audio,
) -> SyncResult:
    del metadata_provider, audio_extractor, transcriber
    return heavy_sync_subtitle_file(
        video_path=video_path,
        subtitle_path=subtitle_path,
        chunk_minutes=sample_minutes,
        output_path=output_path,
        transcription_config=transcription_config,
        matching_config=matching_config,
        heavy_config=heavy_config,
    )