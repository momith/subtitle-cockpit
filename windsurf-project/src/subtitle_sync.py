import json
import logging
import math
import os
import shutil
import re
import subprocess
import tempfile
import time
from datetime import datetime
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import pysubs2
from babelfish import Language


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


def build_sliding_windows(cues: List[NormalizedCue], max_window_size: int = 8) -> List[SlidingCueWindow]:
    windows: List[SlidingCueWindow] = []
    for start in range(len(cues)):
        parts: List[str] = []
        for end in range(start, min(len(cues), start + max_window_size)):
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


def build_anchor_candidates(segments: Iterable[TranscriptSegment], from_end: bool) -> List[AnchorCandidate]:
    ordered = list(segments)
    if from_end:
        ordered = list(reversed(ordered))

    candidates: List[AnchorCandidate] = []
    for start in range(min(len(ordered), 2)):
        parts: List[str] = []
        anchor_seconds = ordered[start].end_seconds if from_end else ordered[start].start_seconds
        for end in range(start, min(len(ordered), start + 4)):
            parts.append(ordered[end].text)
            normalized = normalize_text(' '.join(parts))
            if len(normalized) < 12:
                continue
            candidates.append(AnchorCandidate(normalized_text=normalized, anchor_seconds=anchor_seconds))

    if not candidates:
        raise SubtitleSyncError('Transcription did not contain a usable sentence for anchor matching')
    return candidates


def _similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


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


def calculate_linear_transform(start_anchor: AnchorMatch, end_anchor: AnchorMatch) -> tuple[float, float]:
    subtitle_delta = end_anchor.subtitle_seconds - start_anchor.subtitle_seconds
    transcript_delta = end_anchor.transcript_seconds - start_anchor.transcript_seconds

    if subtitle_delta <= 0 or transcript_delta <= 0:
        raise SubtitleSyncError('Invalid anchors: timestamps must move forward for start and end anchors')

    scale = transcript_delta / subtitle_delta
    if not math.isfinite(scale) or scale <= 0:
        raise SubtitleSyncError('Computed invalid subtitle scale factor')

    # Reject implausible drift corrections. A few percent covers timing/framerate issues.
    if abs(scale - 1.0) > 0.08:
        raise SubtitleSyncError(f'Anchor drift is too large for a reliable linear sync (scale={scale:.5f})')

    offset = start_anchor.transcript_seconds - (start_anchor.subtitle_seconds * scale)

    predicted_end = end_anchor.subtitle_seconds * scale + offset
    end_error = abs(predicted_end - end_anchor.transcript_seconds)
    if end_error > 1.0:
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
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    candidate = base.parent / f'{base.name}.synced-{timestamp}.srt'
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


def transcribe_audio(audio_path: str, language_alpha2: str, model_name: str = 'tiny') -> List[TranscriptSegment]:
    model_name = os.environ.get('SUBTITLE_SYNC_WHISPER_MODEL', model_name)
    device = os.environ.get('SUBTITLE_SYNC_WHISPER_DEVICE', 'cpu')
    compute_type = os.environ.get('SUBTITLE_SYNC_WHISPER_COMPUTE_TYPE', 'int8')
    cpu_threads = max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_CPU_THREADS', os.cpu_count() or 1)), 1)
    num_workers = max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_NUM_WORKERS', '1')), 1)
    beam_size = max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_BEAM_SIZE', '1')), 1)
    best_of = max(int(os.environ.get('SUBTITLE_SYNC_WHISPER_BEST_OF', '1')), 1)
    condition_on_previous_text = os.environ.get('SUBTITLE_SYNC_WHISPER_CONDITION_ON_PREVIOUS', '0') == '1'
    model = _get_whisper_model(model_name, device, compute_type, cpu_threads, num_workers)
    started_at = time.perf_counter()
    logger.info(
        'subtitle_sync: starting transcription audio=%s model=%s language=%s beam_size=%s best_of=%s condition_on_previous_text=%s',
        audio_path,
        model_name,
        language_alpha2,
        beam_size,
        best_of,
        condition_on_previous_text,
    )
    segments, _info = model.transcribe(
        audio_path,
        language=language_alpha2,
        vad_filter=True,
        beam_size=beam_size,
        best_of=best_of,
        patience=1,
        condition_on_previous_text=condition_on_previous_text,
        temperature=0.0,
        word_timestamps=False,
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
    metadata_provider: Callable[[str], VideoMetadata] = probe_video_metadata,
    audio_extractor: Callable[[str, int, AudioSampleWindow, str], str] = extract_audio_window,
    transcriber: Callable[[str, str], List[TranscriptSegment]] = transcribe_audio,
) -> SyncPlan:
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
    cue_windows = build_sliding_windows(cues)
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
            segments = transcriber(audio_path, subtitle_language_alpha2)
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
    start_candidates = build_anchor_candidates(transcripts['start'], from_end=False)
    end_candidates = build_anchor_candidates(transcripts['end'], from_end=True)
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
    )
    end_anchor = find_anchor_match(
        cues=cues,
        windows=cue_windows,
        candidates=end_candidates,
        window_name='end',
        search_from_end=True,
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
    offset_seconds, scale = calculate_linear_transform(start_anchor, end_anchor)
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


def sync_subtitle_file(
    video_path: str,
    subtitle_path: str,
    sample_minutes: int,
    output_path: Optional[str] = None,
    metadata_provider: Callable[[str], VideoMetadata] = probe_video_metadata,
    audio_extractor: Callable[[str, int, AudioSampleWindow, str], str] = extract_audio_window,
    transcriber: Callable[[str, str], List[TranscriptSegment]] = transcribe_audio,
) -> SyncResult:
    logger.info(
        'subtitle_sync: sync file start video=%s subtitle=%s sample_minutes=%s output_override=%s',
        video_path,
        subtitle_path,
        sample_minutes,
        output_path,
    )
    plan = plan_sync(
        video_path=video_path,
        subtitle_path=subtitle_path,
        sample_minutes=sample_minutes,
        metadata_provider=metadata_provider,
        audio_extractor=audio_extractor,
        transcriber=transcriber,
    )
    logger.info('subtitle_sync: sync plan complete subtitle=%s', subtitle_path)
    resolved_output = output_path or build_output_path(subtitle_path)
    logger.info('subtitle_sync: applying sync plan to output=%s', resolved_output)
    started_at = time.perf_counter()
    apply_transform_to_subtitles(subtitle_path, resolved_output, plan.offset_seconds, plan.scale)
    logger.info('subtitle_sync: wrote synced subtitle file=%s in %.2fs', resolved_output, time.perf_counter() - started_at)
    logger.info('subtitle_sync: sync file complete output=%s', resolved_output)
    return SyncResult(plan=plan, output_path=resolved_output)