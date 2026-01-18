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

# Timeout configurations per job type (in seconds)
JOB_TIMEOUTS = {
    JOB_TYPE_EXTRACT: 300,          # 5 minutes
    JOB_TYPE_SUP_TO_SRT: 600,       # 10 minutes
    JOB_TYPE_TRANSLATE: 1800,       # 30 minutes
    JOB_TYPE_SEARCH_SUBTITLES: 600  # 10 minutes
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
        
        Translates SRT files using translation_providers.py with automatic failover,
        or uses local GoogleTranslate implementation when that provider is selected.
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
        
        provider = settings.get('provider', 'GoogleTranslate')
        
        # Use local GoogleTranslate implementation if that provider is selected
        if provider == 'GoogleTranslate':
            # Generate output filename with target language code
            base_name = os.path.splitext(file_path)[0]
            ext = os.path.splitext(file_path)[1]
            output_rel_path = f"{base_name}.{target_lang}{ext}"
            output_abs_path = os.path.join(base_dir, output_rel_path)
            
            logging.info(f'Using local GoogleTranslate for {abs_path} -> {output_abs_path}')
            success = self._translate_with_google_local(abs_path, output_abs_path, target_lang)
            
            if not success:
                raise RuntimeError('Local GoogleTranslate translation failed')
            
            return {
                'output_file': output_rel_path,
                'message': f'Translation completed using local GoogleTranslate'
            }
        else:
            # Use local translation providers (DeepL or Azure)
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

        search_languages = settings.get('subtitle_search_languages', ['en'])
        subtitle_max_downloads = int(settings.get('subtitle_max_downloads', 1) or 1)
        if subtitle_max_downloads < 1:
            subtitle_max_downloads = 1
        subtitle_providers = settings.get('subtitle_providers', {})

        os_config = subtitle_providers.get('opensubtitles', {})
        a7_config = subtitle_providers.get('addic7ed', {})

        enabled_providers = []
        if os_config.get('enabled') and os_config.get('username') and os_config.get('password'):
            enabled_providers.append('opensubtitles')
        if a7_config.get('enabled') and a7_config.get('username') and a7_config.get('password'):
            enabled_providers.append('addic7ed')

        if not enabled_providers:
            raise RuntimeError('No subtitle providers are configured. Please enable and configure OpenSubtitles or Addic7ed in Settings.')

        # Build searcher
        searcher = SubtitleSearcher(
            opensubtitles_username=os_config.get('username') if 'opensubtitles' in enabled_providers else None,
            opensubtitles_password=os_config.get('password') if 'opensubtitles' in enabled_providers else None,
            addic7ed_username=a7_config.get('username') if 'addic7ed' in enabled_providers else None,
            addic7ed_password=a7_config.get('password') if 'addic7ed' in enabled_providers else None
        )

        logging.info(f'Searching subtitles (job) for: {abs_path}')

        subtitles = searcher.search_subtitles(
            abs_path,
            search_languages,
            providers=enabled_providers
        )

        downloaded_files = []
        for sub_dict in subtitles[:subtitle_max_downloads]:
            try:
                out_path = searcher.download_subtitle(sub_dict, abs_path, output_dir=os.path.dirname(abs_path))
                if out_path:
                    downloaded_files.append(out_path)
            except Exception as e:
                logging.exception(f'Error downloading subtitle in job for {file_path}: {e}')

        # Build serializable subtitle list (without subtitle_object)
        subtitle_list = []
        for sub in subtitles:
            sub_copy = {k: v for k, v in sub.items() if k != 'subtitle_object'}
            subtitle_list.append(sub_copy)

        return {
            'path': file_path,
            'count': len(subtitle_list),
            'downloaded_files': downloaded_files,
            'subtitles': subtitle_list
        }
    
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
        
        provider = settings.get('provider', 'GoogleTranslate')
        max_attempts = 2
        
        for attempt in range(max_attempts):
            # Get active API key
            provider_keys = settings.get('provider_keys', {})
            active_key_info = None
            
            if provider in ['DeepL', 'Azure']:
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
            
            if provider in ['DeepL', 'Azure'] and active_key_info:
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
                    providers = ['GoogleTranslate', 'DeepL', 'Azure']
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
        
        # Build paths
        abs_path = os.path.join(base_dir, file_path)
        base_name = os.path.splitext(file_path)[0]
        ext = os.path.splitext(file_path)[1]
        output_rel_path = f"{base_name}.{target_lang}{ext}"
        output_abs_path = os.path.join(base_dir, output_rel_path)
        
        # Azure-specific settings
        azure_endpoint = settings.get('azure_endpoint', 'https://api.cognitive.microsofttranslator.com')
        azure_region = settings.get('azure_region', 'germanywestcentral')
        
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
                azure_endpoint=azure_endpoint,
                azure_region=azure_region
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
