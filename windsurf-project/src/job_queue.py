"""
Job Queue System for Subtitle Processing
Manages asynchronous job processing with configurable parallelism and error handling
"""

import sqlite3
import threading
import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
import subprocess
import os

# Job status constants
STATUS_PENDING = 'pending'
STATUS_RUNNING = 'running'
STATUS_COMPLETED = 'completed'
STATUS_FAILED = 'failed'
STATUS_TIMEOUT = 'timeout'

# Job type constants
JOB_TYPE_EXTRACT = 'extract'
JOB_TYPE_SUP_TO_SRT = 'sup_to_srt'
JOB_TYPE_TRANSLATE = 'translate'
JOB_TYPE_SEARCH_SUBTITLES = 'search_subtitles'
JOB_TYPE_SYNC_SUBTITLES = 'sync_subtitles'
JOB_TYPE_PUBLISH_SUBTITLES = 'publish_subtitles'

# Timeout configurations per job type (in seconds)
JOB_TIMEOUTS = {
    JOB_TYPE_EXTRACT: 300,          # 5 minutes
    JOB_TYPE_SUP_TO_SRT: 600,       # 10 minutes
    JOB_TYPE_TRANSLATE: 1800,       # 30 minutes
    JOB_TYPE_SEARCH_SUBTITLES: 600,  # 10 minutes
    JOB_TYPE_SYNC_SUBTITLES: 1800,    # 30 minutes
    JOB_TYPE_PUBLISH_SUBTITLES: 600   # 10 minutes
}


class JobQueue:
    """Manages job queue with SQLite persistence and background processing"""
    
    def __init__(self, db_path='jobs.db', max_parallel=2):
        self.db_path = db_path
        self.max_parallel = max_parallel
        self.running = False
        self.processor_thread = None
        self.lock = threading.Lock()
        self._init_db()
        
    def _init_db(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                file_path TEXT NOT NULL,
                params TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                result TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at)
        ''')
        conn.commit()
        conn.close()
        
    def add_job(self, job_type: str, file_path: str, params: Dict = None) -> int:
        """Add a new job to the queue"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO jobs (job_type, status, file_path, params, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            job_type,
            STATUS_PENDING,
            file_path,
            json.dumps(params) if params else None,
            datetime.now().isoformat()
        ))
        job_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logging.info(f'Added job {job_id}: {job_type} for {file_path}')
        return job_id
    
    def get_pending_jobs(self, limit: int = 10) -> List[Dict]:
        """Get pending jobs"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE status = ? 
            ORDER BY created_at ASC 
            LIMIT ?
        ''', (STATUS_PENDING, limit))
        jobs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jobs
    
    def get_running_jobs(self) -> List[Dict]:
        """Get currently running jobs"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE status = ? 
            ORDER BY started_at ASC
        ''', (STATUS_RUNNING,))
        jobs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jobs
    
    def get_recent_failed_jobs(self, hours: int = 1) -> List[Dict]:
        """Get jobs that failed in the last N hours"""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs 
            WHERE (status = ? OR status = ?)
            AND completed_at > ?
            ORDER BY completed_at DESC
        ''', (STATUS_FAILED, STATUS_TIMEOUT, cutoff))
        jobs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jobs
    
    def get_all_jobs(self, limit: int = 100) -> List[Dict]:
        """Get all jobs (for admin view)"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM jobs 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (limit,))
        jobs = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jobs
    
    def update_job_status(self, job_id: int, status: str, error_message: str = None, result: str = None):
        """Update job status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        updates = ['status = ?']
        params = [status]
        
        if status == STATUS_RUNNING:
            updates.append('started_at = ?')
            params.append(datetime.now().isoformat())
        elif status in [STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT]:
            updates.append('completed_at = ?')
            params.append(datetime.now().isoformat())
        
        if error_message is not None:
            updates.append('error_message = ?')
            params.append(error_message)
        
        if result is not None:
            updates.append('result = ?')
            params.append(result)
        
        params.append(job_id)
        
        cursor.execute(f'''
            UPDATE jobs 
            SET {', '.join(updates)}
            WHERE id = ?
        ''', params)
        conn.commit()
        conn.close()
    
    def delete_job(self, job_id: int) -> bool:
        """Delete a job (only if pending)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT status FROM jobs WHERE id = ?', (job_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return False
        
        if row[0] != STATUS_PENDING:
            conn.close()
            return False
        
        cursor.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
        conn.commit()
        conn.close()
        logging.info(f'Deleted job {job_id}')
        return True
    
    def cleanup_old_jobs(self, days: int = 7):
        """Clean up completed/failed jobs older than N days"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM jobs 
            WHERE (status = ? OR status = ? OR status = ?)
            AND completed_at < ?
        ''', (STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT, cutoff))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            logging.info(f'Cleaned up {deleted} old jobs')
        return deleted
    
    def start_processor(self):
        """Start the background job processor"""
        if self.running:
            return
        
        self.running = True
        self.processor_thread = threading.Thread(target=self._process_jobs, daemon=True)
        self.processor_thread.start()
        logging.info(f'Job processor started with max_parallel={self.max_parallel}')
    
    def stop_processor(self):
        """Stop the background job processor"""
        self.running = False
        if self.processor_thread:
            self.processor_thread.join(timeout=5)
        logging.info('Job processor stopped')
    
    def _process_jobs(self):
        """Background thread that processes jobs"""
        while self.running:
            try:
                # Check for running jobs and update timeouts
                self._check_timeouts()
                
                # Get current running count
                running_jobs = self.get_running_jobs()
                running_count = len(running_jobs)
                
                # Start new jobs if capacity available
                if running_count < self.max_parallel:
                    slots_available = self.max_parallel - running_count
                    pending_jobs = self.get_pending_jobs(limit=slots_available)
                    
                    for job in pending_jobs:
                        if not self.running:
                            break
                        self._start_job(job)
                
                # Clean up old jobs periodically
                if int(time.time()) % 3600 == 0:  # Every hour
                    self.cleanup_old_jobs()
                
            except Exception as e:
                logging.exception(f'Error in job processor: {e}')
            
            time.sleep(2)  # Check every 2 seconds
    
    def _check_timeouts(self):
        """Check for jobs that have exceeded their timeout"""
        running_jobs = self.get_running_jobs()
        for job in running_jobs:
            timeout = JOB_TIMEOUTS.get(job['job_type'], 600)
            started_at = datetime.fromisoformat(job['started_at'])
            elapsed = (datetime.now() - started_at).total_seconds()
            
            if elapsed > timeout:
                self.update_job_status(
                    job['id'],
                    STATUS_TIMEOUT,
                    error_message=f'Job exceeded timeout of {timeout}s'
                )
                logging.warning(f'Job {job["id"]} timed out after {elapsed}s')
    
    def _start_job(self, job: Dict):
        """Start processing a job in a separate thread"""
        self.update_job_status(job['id'], STATUS_RUNNING)
        thread = threading.Thread(target=self._execute_job, args=(job,), daemon=True)
        thread.start()
    
    def _execute_job(self, job: Dict):
        """Execute a specific job"""
        job_id = job['id']
        job_type = job['job_type']
        file_path = job['file_path']
        params = json.loads(job['params']) if job['params'] else {}
        
        try:
            if job_type == JOB_TYPE_EXTRACT:
                result = self._execute_extract(file_path, params)
            elif job_type == JOB_TYPE_SUP_TO_SRT:
                result = self._execute_sup_to_srt(file_path, params)
            elif job_type == JOB_TYPE_TRANSLATE:
                result = self._execute_translate(file_path, params)
            elif job_type == JOB_TYPE_SEARCH_SUBTITLES:
                result = self._execute_search_subtitles(file_path, params)
            elif job_type == JOB_TYPE_SYNC_SUBTITLES:
                result = self._execute_sync_subtitles(file_path, params)
            elif job_type == JOB_TYPE_PUBLISH_SUBTITLES:
                result = self._execute_publish_subtitles(file_path, params)
            else:
                raise ValueError(f'Unknown job type: {job_type}')
            
            self.update_job_status(job_id, STATUS_COMPLETED, result=json.dumps(result))
            logging.info(f'Job {job_id} completed successfully')
            
        except Exception as e:
            error_msg = str(e)
            self.update_job_status(job_id, STATUS_FAILED, error_message=error_msg)
            logging.error(f'Job {job_id} failed: {error_msg}')
    
    def _execute_extract(self, file_path: str, params: Dict) -> Dict:
        """Execute subtitle extraction job - extracts ALL subtitles in the configured language"""
        import json
        
        base_dir = params.get('base_dir')
        extraction_source_language = params.get('extraction_source_language', 'eng')
        
        if not base_dir:
            raise ValueError('Missing required parameter: base_dir')
        
        video_path = os.path.join(base_dir, file_path)
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f'Video file not found: {video_path}')
        
        # Run ffprobe to detect subtitle streams
        probe_cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 's', video_path
        ]
        
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if probe_result.returncode != 0:
            raise RuntimeError(f'ffprobe failed: {probe_result.stderr}')
        
        probe_data = json.loads(probe_result.stdout)
        streams = probe_data.get('streams', [])
        
        # Find ALL subtitle streams matching the configured language
        # Priority: text subtitles first (SRT/ASS/etc.), then bitmap (PGS, then VobSub)
        text_codecs = ['subrip', 'ass', 'ssa', 'mov_text', 'webvtt']
        
        matching_subs = []
        
        # 1) Collect all text-based subtitles in configured language
        for idx, stream in enumerate(streams):
            codec = stream.get('codec_name', '')
            lang = (stream.get('tags', {}).get('language', '') or '').lower()
            if codec in text_codecs and lang == extraction_source_language.lower():
                matching_subs.append((idx, stream, 'text'))
        
        # 2) Collect all PGS (Blu‑ray bitmap) in configured language
        for idx, stream in enumerate(streams):
            codec = stream.get('codec_name', '')
            lang = (stream.get('tags', {}).get('language', '') or '').lower()
            if codec == 'hdmv_pgs_subtitle' and lang == extraction_source_language.lower():
                matching_subs.append((idx, stream, 'pgs'))
        
        # 3) Collect all VobSub (DVD bitmap) in configured language
        for idx, stream in enumerate(streams):
            codec = stream.get('codec_name', '')
            lang = (stream.get('tags', {}).get('language', '') or '').lower()
            if codec == 'dvd_subtitle' and lang == extraction_source_language.lower():
                matching_subs.append((idx, stream, 'vobsub'))
        
        if not matching_subs:
            raise RuntimeError(f'No subtitle streams found for language "{extraction_source_language}"')
        
        base_noext = os.path.splitext(video_path)[0]
        extracted_files = []
        
        # Extract each matching subtitle stream
        for sub_order, stream_info, codec_type in matching_subs:
            # Generate unique filename to avoid overwriting
            # Format: <video_name>.track<N>.<ext>
            if len(matching_subs) == 1:
                # Only one subtitle, use simple name
                suffix = ''
            else:
                # Multiple subtitles, add track number
                suffix = f'.track{sub_order}'
            
            # Choose output format based on codec
            if codec_type == 'pgs':
                # Extract to .sup file for PGS
                output_path = base_noext + suffix + '.sup'
                extract_cmd = [
                    'ffmpeg', '-y', '-analyzeduration', '200M', '-probesize', '50M',
                    '-i', video_path, '-map', f'0:s:{sub_order}',
                    '-c:s', 'copy', output_path
                ]
                logging.info(f'Extracting PGS subtitle track {sub_order} to SUP: {os.path.basename(output_path)}')
                
            elif codec_type == 'vobsub':
                # Extract VobSub (dvd_subtitle) to .sub/.idx pair using mkvextract (mkvtoolnix)
                output_path = base_noext + suffix + '.sub'
                track_id = stream_info.get('index')
                if track_id is None:
                    logging.warning(f'Could not determine Matroska track id for VobSub subtitle track {sub_order}, skipping')
                    continue
                extract_cmd = [
                    'mkvextract', video_path, 'tracks', f'{track_id}:{output_path}'
                ]
                logging.info(f'Extracting VobSub subtitle track {sub_order} to SUB/IDX via mkvextract: {os.path.basename(output_path)}')
                
            else:  # text-based formats (subrip, ass, ssa, mov_text, webvtt)
                # Extract to .srt file for text-based subtitles
                output_path = base_noext + suffix + '.srt'
                extract_cmd = [
                    'ffmpeg', '-y', '-analyzeduration', '200M', '-probesize', '50M',
                    '-i', video_path, '-map', f'0:s:{sub_order}',
                    '-c:s', 'srt', output_path
                ]
                logging.info(f'Extracting text subtitle track {sub_order} to SRT: {os.path.basename(output_path)}')
            
            extract_result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=300)
            
            if extract_result.returncode != 0:
                logging.error(f'Failed to extract track {sub_order}: {extract_result.stderr or extract_result.stdout}')
                continue
            
            if not os.path.exists(output_path):
                logging.error(f'Output file was not created for track {sub_order}')
                continue
            
            # For VobSub, also verify .idx was created by mkvextract
            if codec_type == 'vobsub':
                idx_path = base_noext + suffix + '.idx'
                if not os.path.exists(idx_path):
                    logging.error(f'VobSub .idx file was not created for track {sub_order}')
                    continue
                extracted_files.append(f'{os.path.basename(output_path)} + {os.path.basename(idx_path)}')
            else:
                extracted_files.append(os.path.basename(output_path))
        
        if not extracted_files:
            raise RuntimeError('Failed to extract any subtitle streams')
        
        return {
            'output_files': extracted_files,
            'message': f'Extracted {len(extracted_files)} subtitle(s): {", ".join(extracted_files)}'
        }
    
    def _execute_sup_to_srt(self, file_path: str, params: Dict) -> Dict:
        """Execute SUP/SUB to SRT conversion job using Python OCR converter
        
        Supports both:
        - .sup files (PGS/HDMV subtitles)
        - .sub files (VobSub/DVD subtitles, requires .idx companion file)
        """
        from ocr_subtitle_converter import convert_sup_to_srt, convert_sub_to_srt
        
        base_dir = params.get('base_dir')
        ocr_source_language = params.get('ocr_source_language', 'eng')
        debug_mode = params.get('debug_mode', False)
        debug_subtitle_index = params.get('debug_subtitle_index', None)
        
        if not base_dir:
            raise ValueError('Missing required parameter: base_dir')
        
        # Build full input path
        input_path = os.path.join(base_dir, file_path)
        if not os.path.exists(input_path):
            raise FileNotFoundError(f'Input file not found: {input_path}')
        
        # Build output path
        base_name = os.path.splitext(file_path)[0]
        out_path = base_name + '.srt'
        full_out_path = os.path.join(base_dir, out_path)
        
        # Determine file type and call appropriate converter
        ext = os.path.splitext(file_path)[1].lower()
        
        debug_info = f' (DEBUG MODE: subtitle #{debug_subtitle_index})' if debug_mode and debug_subtitle_index else ' (DEBUG MODE: all subtitles)' if debug_mode else ''
        logging.info(f'Converting {file_path} to SRT using Python OCR converter (language: {ocr_source_language}){debug_info}')
        
        try:
            if ext == '.sup':
                success = convert_sup_to_srt(input_path, full_out_path, ocr_source_language,
                                            debug_mode=debug_mode, debug_subtitle_index=debug_subtitle_index)
            elif ext == '.sub':
                success = convert_sub_to_srt(input_path, full_out_path, ocr_source_language,
                                            debug_mode=debug_mode, debug_subtitle_index=debug_subtitle_index)
            else:
                raise ValueError(f'Unsupported file type: {ext}. Expected .sup or .sub')
            
            if not success:
                raise RuntimeError('Conversion failed')
            
            # Verify output file was created
            if not os.path.exists(full_out_path):
                raise RuntimeError('Output file was not created')
            
            return {'output_file': out_path, 'message': 'Conversion successful'}
            
        except Exception as e:
            logging.error(f'OCR conversion failed: {e}')
            raise
    
    def _execute_translate(self, file_path: str, params: Dict) -> Dict:
        """Execute translation job
        
        Translates SRT files using translation_providers.py with automatic failover.
        """
        import json
        from datetime import datetime, timedelta
        
        base_dir = params.get('base_dir')
        host_base_dir = params.get('host_base_dir')
        vpn_dir = params.get('vpn_dir', '')
        target_lang = params.get('target_lang')
        settings_file = params.get('settings_file')
        
        if not all([base_dir, target_lang, settings_file]):
            raise ValueError('Missing required parameters: base_dir, target_lang, settings_file')
        
        abs_path = os.path.join(base_dir, file_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f'SRT file not found: {abs_path}')
        
        # Read current settings
        if os.path.exists(settings_file):
            with open(settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        else:
            raise RuntimeError('Settings file not found')
        
        provider = settings.get('provider', 'DeepL')

        success, message = self._translate_with_failover(
            file_path, settings, base_dir, target_lang, settings_file, vpn_dir
        )

        if not success:
            raise RuntimeError(f'Translation failed: {message}')

        # Generate output filename with target language code
        base_name = os.path.splitext(file_path)[0]
        ext = os.path.splitext(file_path)[1]
        output_rel_path = f"{base_name}.{target_lang}{ext}"

        return {
            'output_file': output_rel_path,
            'message': message
        }

    def _execute_search_subtitles(self, file_path: str, params: Dict) -> Dict:
        """Execute online subtitle search/download job

        Searches for subtitles using configured providers (currently OpenSubtitles)
        and downloads up to subtitle_max_downloads best matches next to the video.
        """
        import json
        from subtitle_search import SubtitleSearcher
        from babelfish import Language

        base_dir = params.get('base_dir')
        settings_file = params.get('settings_file')

        if not base_dir or not settings_file:
            raise ValueError('Missing required parameters: base_dir, settings_file')

        abs_path = os.path.join(base_dir, file_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f'Video file not found: {abs_path}')

        # Load settings
        if not os.path.exists(settings_file):
            raise FileNotFoundError(f'Settings file not found: {settings_file}')
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)

        raw_search_languages = settings.get('subtitle_search_languages', ['en'])
        if isinstance(raw_search_languages, str):
            search_languages = [s.strip() for s in raw_search_languages.split(',') if s.strip()]
        elif isinstance(raw_search_languages, list):
            search_languages = [str(x).strip() for x in raw_search_languages if str(x).strip()]
        else:
            search_languages = ['en']
        if not search_languages:
            search_languages = ['en']

        subtitle_max_downloads = int(settings.get('subtitle_max_downloads', 1) or 1)
        if subtitle_max_downloads < 1:
            subtitle_max_downloads = 1
        subtitle_providers = settings.get('subtitle_providers', {})

        os_config = subtitle_providers.get('opensubtitles', {})
        a7_config = subtitle_providers.get('addic7ed', {})
        subdl_config = subtitle_providers.get('subdl', {})

        enabled_providers = []
        if os_config.get('enabled') and os_config.get('username') and os_config.get('password'):
            enabled_providers.append('opensubtitles')
        if a7_config.get('enabled') and a7_config.get('username') and a7_config.get('password'):
            enabled_providers.append('addic7ed')
        if subdl_config.get('enabled') and subdl_config.get('api_key'):
            enabled_providers.append('subdl')

        if not enabled_providers:
            raise RuntimeError('No subtitle providers are configured. Please enable and configure OpenSubtitles, Addic7ed, or SubDL in Settings.')

        # Build searcher
        searcher = SubtitleSearcher(
            opensubtitles_username=os_config.get('username') if 'opensubtitles' in enabled_providers else None,
            opensubtitles_password=os_config.get('password') if 'opensubtitles' in enabled_providers else None,
            addic7ed_username=a7_config.get('username') if 'addic7ed' in enabled_providers else None,
            addic7ed_password=a7_config.get('password') if 'addic7ed' in enabled_providers else None,
            subdl_api_key=subdl_config.get('api_key') if 'subdl' in enabled_providers else None
        )

        logging.info(
            'Searching subtitles (job) for: %s (providers=%s languages=%s max_downloads=%s)',
            abs_path,
            ','.join(enabled_providers),
            ','.join(search_languages),
            subtitle_max_downloads
        )

        # Language fallback semantics: try first language, then second, etc.
        all_subtitles = []
        downloaded_files = []
        attempts = []
        used_language = None

        for lang in search_languages:
            logging.info('Subtitle search attempt language=%s for %s', lang, file_path)
            subtitles = searcher.search_subtitles(
                abs_path,
                [lang],
                providers=enabled_providers
            )
            all_subtitles.extend(subtitles)

            attempt_downloaded = []
            for sub_dict in subtitles[:subtitle_max_downloads]:
                try:
                    out_path = searcher.download_subtitle(sub_dict, abs_path, output_dir=os.path.dirname(abs_path))
                    if out_path:
                        attempt_downloaded.append(out_path)
                except Exception as e:
                    logging.exception(f'Error downloading subtitle in job for {file_path}: {e}')

            attempts.append({
                'language': lang,
                'results': len(subtitles),
                'downloaded': len(attempt_downloaded)
            })

            if attempt_downloaded:
                used_language = lang
                downloaded_files.extend(attempt_downloaded)
                logging.info('Subtitle search succeeded for %s with language=%s (downloaded=%s)', file_path, lang, len(attempt_downloaded))
                break
            logging.info('Subtitle search had no downloads for %s with language=%s (results=%s)', file_path, lang, len(subtitles))

        if not downloaded_files:
            raise RuntimeError(
                'No subtitles downloaded. Providers attempted: '
                + ','.join(enabled_providers)
                + '. Languages attempted: '
                + ','.join([a.get('language') for a in attempts])
                + '. Results by language: '
                + ', '.join([f"{a.get('language')}={a.get('results')}" for a in attempts])
            )

        # Build serializable subtitle list (without subtitle_object)
        subtitle_list = []
        for sub in all_subtitles:
            sub_copy = {k: v for k, v in sub.items() if k != 'subtitle_object'}
            subtitle_list.append(sub_copy)

        return {
            'path': file_path,
            'count': len(subtitle_list),
            'downloaded_files': downloaded_files,
            'used_language': used_language,
            'attempts': attempts,
            'subtitles': subtitle_list
        }

    def _execute_sync_subtitles(self, file_path: str, params: Dict) -> Dict:
        """Execute subtitle sync job using ffsubsync.

        Expects file_path to be the relative path of an .srt file. The corresponding
        video file is auto-detected in the same directory.
        """
        import json
        import re
        from difflib import SequenceMatcher
        import shutil

        base_dir = params.get('base_dir')
        settings_file = params.get('settings_file')

        if not base_dir or not settings_file:
            raise ValueError('Missing required parameters: base_dir, settings_file')

        sub_abs = os.path.join(base_dir, file_path)
        if not os.path.isfile(sub_abs):
            raise FileNotFoundError(f'Subtitle file not found: {sub_abs}')
        if not sub_abs.lower().endswith('.srt'):
            raise RuntimeError('Only SRT subtitle files are supported for syncing')

        if not os.path.exists(settings_file):
            raise FileNotFoundError(f'Settings file not found: {settings_file}')
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)

        dont_fix_framerate = bool(settings.get('sync_dont_fix_framerate', False))
        use_gss = bool(settings.get('sync_use_golden_section', False))
        vad = str(settings.get('sync_vad', 'default') or 'default').strip()

        video_exts = {
            '.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
            '.mpeg', '.mpg', '.ts', '.m2ts'
        }

        sub_dir = os.path.dirname(sub_abs)
        sub_stem = os.path.splitext(os.path.basename(sub_abs))[0]

        def _norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]+', '', (s or '').lower())

        stem_candidates = [sub_stem]
        parts = sub_stem.split('.')
        if len(parts) >= 2:
            stem_candidates.append('.'.join(parts[:-1]))
        if len(parts) >= 3:
            stem_candidates.append('.'.join(parts[:-2]))
        stem_candidates = list(dict.fromkeys([c for c in stem_candidates if c]))

        best = None
        best_score = -1.0
        for fname in os.listdir(sub_dir or '.'):  # same folder as subtitle
            ext = os.path.splitext(fname)[1].lower()
            if ext not in video_exts:
                continue
            vstem = os.path.splitext(fname)[0]
            score = 0.0
            for cand in stem_candidates:
                score = max(score, SequenceMatcher(None, _norm(cand), _norm(vstem)).ratio())
            if score > best_score:
                best_score = score
                best = fname

        if not best or best_score < 0.60:
            raise RuntimeError(f'No matching video file found for subtitle: {os.path.basename(sub_abs)}')

        video_abs = os.path.join(sub_dir, best)
        if not os.path.isfile(video_abs):
            raise RuntimeError(f'Auto-detected video file does not exist: {video_abs}')

        out_base = os.path.splitext(sub_abs)[0] + '.synced'
        out_abs = out_base + '.srt'
        if os.path.exists(out_abs):
            counter = 2
            while os.path.exists(f"{out_base}-{counter}.srt"):
                counter += 1
            out_abs = f"{out_base}-{counter}.srt"

        if not shutil.which('ffsubsync'):
            raise RuntimeError('ffsubsync is not installed or not found in PATH')

        cmd = ['ffsubsync', video_abs, '-i', sub_abs, '-o', out_abs]
        if dont_fix_framerate:
            cmd.append('--no-fix-framerate')
        if use_gss:
            cmd.append('--gss')
        if vad and vad != 'default':
            cmd.extend(['--vad', vad])

        logging.info('Running ffsubsync: %s', ' '.join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            stderr = (proc.stderr or '').strip()
            stdout = (proc.stdout or '').strip()

            def _tail(s: str, max_lines: int = 40, max_chars: int = 4000) -> str:
                if not s:
                    return ''
                lines = s.splitlines()
                tail = '\n'.join(lines[-max_lines:])
                if len(tail) > max_chars:
                    tail = tail[-max_chars:]
                return tail

            stderr_tail = _tail(stderr)
            stdout_tail = _tail(stdout)
            details = stderr_tail or stdout_tail or 'ffsubsync failed'
            raise RuntimeError(f'ffsubsync failed (exit_code={proc.returncode})\n{details}')

        out_rel = os.path.relpath(out_abs, base_dir).replace('\\', '/')
        video_rel = os.path.relpath(video_abs, base_dir).replace('\\', '/')
        return {
            'subtitle': file_path,
            'video': video_rel,
            'output_file': out_rel,
            'options': {
                'dont_fix_framerate': dont_fix_framerate,
                'use_golden_section': use_gss,
                'vad': vad,
            }
        }
    
    def _execute_publish_subtitles(self, file_path: str, params: Dict) -> Dict:
        """Execute subtitle publish job (uploads to enabled providers that support upload)."""
        import json
        import requests
        from guessit import guessit
        from babelfish import Language

        base_dir = params.get('base_dir')
        settings_file = params.get('settings_file')
        target = params.get('target') or {}
        comment = params.get('comment')

        if not isinstance(comment, str):
            comment = ''

        logging.info(
            'Publish job starting for %s (target type=%s tmdb_id=%s imdb_id=%s title=%s)',
            file_path,
            (target or {}).get('type'),
            (target or {}).get('tmdb_id'),
            (target or {}).get('imdb_id'),
            (target or {}).get('title')
        )

        if not base_dir or not settings_file:
            raise ValueError('Missing required parameters: base_dir, settings_file')

        abs_path = os.path.join(base_dir, file_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f'Subtitle file not found: {abs_path}')

        if not os.path.exists(settings_file):
            raise FileNotFoundError(f'Settings file not found: {settings_file}')
        with open(settings_file, 'r', encoding='utf-8') as f:
            settings = json.load(f)

        subtitle_providers = settings.get('subtitle_providers', {})
        subdl_config = subtitle_providers.get('subdl', {}) or {}

        subdl_enabled = bool(subdl_config.get('enabled'))
        subdl_token = str(subdl_config.get('upload_token') or '').strip()
        if not subdl_enabled or not subdl_token:
            raise RuntimeError('SubDL publishing is not enabled or upload token is missing.')

        logging.info('Publish attempting provider=subdl for %s', file_path)
        upload_result = self._publish_to_subdl(abs_path, target, subdl_token, guessit, Language, comment=comment)
        msg = None
        if isinstance(upload_result, dict):
            msg = upload_result.get('message') or upload_result.get('msg')
        logging.info('Publish succeeded provider=subdl for %s%s', file_path, (f" (message={msg})" if msg else ''))

        return {
            'path': file_path,
            'provider': 'subdl',
            'result': upload_result
        }

    def _publish_to_subdl(self, subtitle_abs_path: str, target: Dict, token: str, guessit_func, LanguageCls, comment: str = '') -> Dict:
        import os
        import json
        import requests
        import re

        t = (token or '').strip()
        bearer = t
        if t.lower().startswith('bearer '):
            bare = t[7:].strip()
        else:
            bare = t
        bearer = f'Bearer {bare}'

        # Step 1: get n_id
        step1_url = 'https://api3.subdl.com/user/getNId'
        logging.info('SubDL REST call: GET %s (headers: token=***)', step1_url)
        r1 = requests.get(step1_url, headers={'token': bare}, timeout=30)
        logging.info('SubDL REST response: GET %s status_code=%s', step1_url, r1.status_code)
        r1.raise_for_status()
        p1 = r1.json() or {}
        if not p1.get('ok') or not p1.get('n_id'):
            raise RuntimeError(p1.get('error') or 'Failed to get SubDL n_id')
        n_id = p1.get('n_id')
        logging.info('SubDL step1 ok (n_id=%s)', n_id)

        # Step 2: upload file
        try:
            size = os.path.getsize(subtitle_abs_path)
        except Exception:
            size = None
        logging.info(
            'SubDL step2 uploadSingleSubtitle (filename=%s size=%s)',
            os.path.basename(subtitle_abs_path),
            str(size) if size is not None else 'unknown'
        )
        step2_url = 'https://api3.subdl.com/user/uploadSingleSubtitle'
        logging.info(
            'SubDL REST call: POST %s (headers: token=***; form: n_id=%s; file=%s)',
            step2_url,
            n_id,
            os.path.basename(subtitle_abs_path)
        )
        with open(subtitle_abs_path, 'rb') as f:
            files = {'subtitle': (os.path.basename(subtitle_abs_path), f)}
            data = {'n_id': n_id}
            r2 = requests.post(
                step2_url,
                headers={'token': bare},
                files=files,
                data=data,
                timeout=120
            )

        logging.info('SubDL REST response: POST %s status_code=%s', step2_url, r2.status_code)
        r2.raise_for_status()
        p2 = r2.json() or {}
        if not p2.get('ok'):
            raise RuntimeError(p2.get('error') or 'Failed to upload subtitle file')

        file_info = p2.get('file') or {}
        file_n_id = file_info.get('file_n_id')
        if not file_n_id:
            raise RuntimeError('Missing file_n_id from SubDL uploadSingleSubtitle response')
        logging.info('SubDL step2 ok (file_n_id=%s)', file_n_id)

        # Infer metadata from filename
        base = os.path.basename(subtitle_abs_path)
        stem, _ext = os.path.splitext(base)

        # Language guess from filename: *.en.srt, *.eng.srt
        lang_guess = None
        m = re.search(r'\.([a-zA-Z]{2,3})(?:\.[0-9]+)?$', stem)
        if m:
            lang_guess = m.group(1)

        lang = 'EN'
        if lang_guess:
            try:
                code = str(lang_guess).strip().replace('-', '_')
                if len(code) == 2:
                    lang = LanguageCls.fromalpha2(code.lower()).alpha2.upper()
                elif len(code) == 3:
                    lang = LanguageCls.fromalpha3(code.lower()).alpha2.upper()
                else:
                    lang = code.upper()
            except Exception:
                lang = str(lang_guess).upper()

        # release string: try stripping language suffix
        release_name = stem
        if lang_guess and release_name.lower().endswith('.' + lang_guess.lower()):
            release_name = release_name[:-(len(lang_guess) + 1)]

        guess = {}
        try:
            guess = guessit_func(release_name) or {}
        except Exception:
            guess = {}

        content_type = str((target or {}).get('type') or '').strip().lower()
        if content_type not in ['movie', 'tv']:
            content_type = 'movie'

        tmdb_id = str((target or {}).get('tmdb_id') or '').strip()
        imdb_id = str((target or {}).get('imdb_id') or '').strip()
        title = str((target or {}).get('title') or '').strip()
        if not title:
            title = release_name

        # Basic season/episode inference
        season = int(guess.get('season') or 0) if content_type == 'tv' else 0
        ef = guess.get('episode')
        if isinstance(ef, list) and len(ef) > 0:
            ef = ef[0]
        ee = None
        if isinstance(guess.get('episode'), list) and len(guess.get('episode')) > 1:
            ee = guess.get('episode')[-1]
        try:
            ef_i = int(ef) if ef is not None else None
        except Exception:
            ef_i = None
        try:
            ee_i = int(ee) if ee is not None else None
        except Exception:
            ee_i = None

        # Quality inference
        low = release_name.lower()
        quality = 'web'
        if 'bluray' in low or 'bdrip' in low or 'bdremux' in low:
            quality = 'bluray'
        elif 'dvd' in low or 'dvdrip' in low:
            quality = 'dvd'
        elif 'hdtv' in low:
            quality = 'hdtv'
        elif 'cam' in low:
            quality = 'cam'

        hi = bool(re.search(r'\b(hi|sdh)\b', low))

        form = {
            'file_n_ids': json.dumps([file_n_id]),
            'n_id': n_id,
            'type': content_type,
            'quality': quality,
            'production_type': 3, # 3 = Machine translated
            'name': title,
            'releases': json.dumps([release_name]),
            'framerate': 0,
            'comment': str(comment or ''),
            'lang': lang,
            'season': season,
            'hi': str(hi).lower(),
            'is_full_season': 'false',
            'tags': json.dumps([])
        }
        if tmdb_id:
            form['tmdb_id'] = tmdb_id
        if imdb_id:
            form['imdb_id'] = imdb_id
        if content_type == 'tv':
            if ef_i is not None:
                form['ef'] = ef_i
            if ee_i is not None:
                form['ee'] = ee_i

        step3_url = 'https://api3.subdl.com/user/uploadSubtitle'
        try:
            safe_comment = (form.get('comment') or '')
            if len(safe_comment) > 200:
                safe_comment = safe_comment[:200] + '...'
        except Exception:
            safe_comment = ''
        logging.info(
            'SubDL REST call: POST %s (headers: Authorization=Bearer ***; form: type=%s lang=%s tmdb_id=%s imdb_id=%s season=%s ef=%s ee=%s hi=%s comment_len=%s)',
            step3_url,
            form.get('type'),
            form.get('lang'),
            form.get('tmdb_id'),
            form.get('imdb_id'),
            form.get('season'),
            form.get('ef'),
            form.get('ee'),
            form.get('hi'),
            len(safe_comment or '')
        )

        logging.info(
            'SubDL step3 uploadSubtitle (type=%s lang=%s quality=%s season=%s ef=%s ee=%s tmdb_id=%s imdb_id=%s name=%s releases_count=%s hi=%s)',
            content_type,
            lang,
            quality,
            form.get('season'),
            form.get('ef'),
            form.get('ee'),
            form.get('tmdb_id'),
            form.get('imdb_id'),
            form.get('name'),
            1,
            form.get('hi')
        )

        r3 = requests.post(
            step3_url,
            headers={'Authorization': bearer, 'Accept': 'application/json'},
            data=form,
            timeout=60
        )
        logging.info('SubDL REST response: POST %s status_code=%s', step3_url, r3.status_code)
        r3.raise_for_status()

        try:
            p3 = r3.json() or {}
            if isinstance(p3, dict) and p3.get('ok') is False:
                raise RuntimeError(p3.get('error') or p3.get('message') or 'SubDL uploadSubtitle failed')
            if isinstance(p3, dict) and p3.get('ok'):
                logging.info('SubDL step3 ok (message=%s)', p3.get('message') or p3.get('msg') or 'ok')
                return p3
        except ValueError:
            p3 = None

        text = (r3.text or '').strip()
        if text:
            logging.info('SubDL step3 ok (text_response=%s)', text[:300])
            return {'ok': True, 'message': text}
        logging.info('SubDL step3 ok (empty_response=subtitle sent for review)')
        return {'ok': True, 'message': 'subtitle sent for review'}

    def _translate_with_google_local(self, source_path, dest_path, target_lang):
        """
        Execute translation using local GoogleTranslate implementation
        Runs in the current thread (which is already a background job thread)
        
        Args:
            source_path: Absolute path to the source SRT file
            dest_path: Absolute path to the destination SRT file
            target_lang: Target language code (e.g., 'en', 'th', 'de')
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            from google_translate_local import LocalGoogleTranslator
            
            # Progress callback for logging
            def log_progress(current, total):
                if current % 10 == 0 or current == total:
                    logging.info(f'GoogleTranslate progress: {current}/{total} lines')
            
            # Create translator with destination path
            translator = LocalGoogleTranslator(
                source_srt_file=source_path,
                dest_srt_file=dest_path,
                target_lang=target_lang,
                source_lang='auto',
                max_workers=10
            )
            
            # Execute translation
            success = translator.translate(progress_callback=log_progress)
            
            return success
            
        except ImportError as e:
            logging.error(f'Failed to import google_translate_local: {e}')
            logging.error('Make sure dependencies are installed: pip install deep-translator pysubs2 beautifulsoup4 retry')
            return False
        except Exception as e:
            logging.error(f'Local GoogleTranslate failed: {e}', exc_info=True)
            return False
    
    def _translate_with_failover(self, file_path, settings, base_dir, target_lang, settings_file, vpn_dir=None):
        """Translation with automatic failover between providers and API keys using local translation"""
        from datetime import datetime
        import json
        from translation_providers import translate_srt_file
        
        provider = settings.get('provider', 'DeepL')
        max_attempts = 2
        
        for attempt in range(max_attempts):
            # Get active API key
            provider_keys = settings.get('provider_keys', {})
            active_key_info = None
            
            if provider in ['DeepL', 'Azure', 'Gemini']:
                keys_list = provider_keys.get(provider, [])
                if not keys_list:
                    return False, f'No API keys configured for {provider}'
                active_key_info = next((k for k in keys_list if k.get('active')), None)
                if not active_key_info:
                    return False, f'No active API key for {provider}'
            
            # Run translation using local translation_providers
            success, error_msg = self._run_local_translation(
                file_path, provider, active_key_info, settings, base_dir, target_lang, vpn_dir
            )
            
            if success:
                # Update last_used timestamp
                if active_key_info:
                    active_key_info['last_used'] = datetime.now().isoformat()
                with open(settings_file, 'w', encoding='utf-8') as f:
                    json.dump(settings, f, indent=2)
                return True, 'Translation completed'
            
            logging.warning(f'Translation failed with {provider}: {error_msg}')
            
            # Handle failover - try next API key or provider
            switched = False
            
            if provider in ['DeepL', 'Azure', 'Gemini'] and active_key_info:
                auto_change = settings.get('auto_change_key_on_error', {}).get(provider, False)
                if auto_change:
                    # Switch to next key
                    keys_list = provider_keys.get(provider, [])
                    current_idx = next((i for i, k in enumerate(keys_list) if k.get('active')), -1)
                    if current_idx >= 0:
                        keys_list[current_idx]['active'] = False
                        next_idx = (current_idx + 1) % len(keys_list)
                        keys_list[next_idx]['active'] = True
                        switched = True
                        logging.info(f'Switched to next API key for {provider}')
            
            if not switched:
                auto_switch_provider = settings.get('auto_switch_on_error', False)
                if auto_switch_provider and attempt < max_attempts - 1:
                    # Switch provider
                    providers = ['DeepL', 'Azure', 'Gemini']
                    current_idx = providers.index(provider) if provider in providers else 0
                    next_provider = providers[(current_idx + 1) % len(providers)]
                    settings['provider'] = next_provider
                    provider = next_provider
                    switched = True
                    logging.info(f'Switched to provider {provider}')
            
            if switched:
                with open(settings_file, 'w', encoding='utf-8') as f:
                    json.dump(settings, f, indent=2)
                continue
            else:
                return False, error_msg
        
        return False, 'Translation failed after all attempts'
    
    def _run_local_translation(self, file_path, provider, key_info, settings, base_dir, target_lang, vpn_dir=None):
        """Run translation using local translation_providers module with optional VPN"""
        from translation_providers import translate_srt_file, start_vpn, stop_vpn
        
        api_key = ''
        vpn_config_path = None
        vpn_started = False
        
        if key_info:
            api_key = key_info.get('value', '')
            vpn_config = key_info.get('vpn_config', '')
            
            # Build VPN config path if specified
            if vpn_config and vpn_dir and os.path.isdir(vpn_dir):
                vpn_config_path = os.path.join(vpn_dir, vpn_config)
                if not os.path.isfile(vpn_config_path):
                    logging.warning(f'VPN config not found: {vpn_config_path}')
                    vpn_config_path = None
        
        wait_ms = settings.get('wait_ms', {}).get(provider, 1000)
        max_chars_per_request = settings.get('max_chars_per_request', {}).get(provider, 0)
        
        # Build paths
        abs_path = os.path.join(base_dir, file_path)
        base_name = os.path.splitext(file_path)[0]
        ext = os.path.splitext(file_path)[1]
        output_rel_path = f"{base_name}.{target_lang}{ext}"
        output_abs_path = os.path.join(base_dir, output_rel_path)
        
        # Azure-specific settings
        azure_endpoint = settings.get('azure_endpoint', 'https://api.cognitive.microsofttranslator.com')
        azure_region = settings.get('azure_region', 'germanywestcentral')
        # Gemini-specific settings
        gemini_model = settings.get('gemini_model', 'gemini-2.0-flash')
        # DeepL-specific settings
        deepl_endpoint = settings.get('deepl_endpoint', 'https://api-free.deepl.com/v2/translate')
        
        try:
            # Start VPN if configured
            if vpn_config_path:
                logging.info(f'Starting VPN with config: {vpn_config}')
                vpn_started = start_vpn(vpn_config_path)
                if not vpn_started:
                    logging.warning('VPN failed to start, continuing without VPN')
            
            logging.info(f'Translating {file_path} using local {provider} provider')
            
            success = translate_srt_file(
                file_path=abs_path,
                output_path=output_abs_path,
                target_lang=target_lang,
                provider=provider,
                api_key=api_key,
                wait_ms=wait_ms,
                max_chars_per_request=max_chars_per_request,
                deepl_endpoint=deepl_endpoint,
                azure_endpoint=azure_endpoint,
                azure_region=azure_region,
                gemini_model=gemini_model
            )
            
            if success:
                return True, None
            else:
                return False, 'Translation function returned False'
                
        except Exception as e:
            error_msg = str(e)
            logging.error(f'Local translation failed: {error_msg}', exc_info=True)
            return False, error_msg
        finally:
            # Always stop VPN if it was started
            if vpn_started:
                stop_vpn()


# Global job queue instance
_job_queue = None

def get_job_queue(max_parallel: int = 2) -> JobQueue:
    """Get or create the global job queue instance"""
    global _job_queue
    if _job_queue is None:
        _job_queue = JobQueue(max_parallel=max_parallel)
        _job_queue.start_processor()
    return _job_queue
