"""
Local Google Translate Implementation for Subtitle Translation
Based on Bazarr's implementation using deep_translator library
"""

import logging
import pysubs2
import threading
from retry.api import retry
from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests, RequestError, TranslationNotFound
from concurrent.futures import ThreadPoolExecutor
import os

logger = logging.getLogger(__name__)


class LocalGoogleTranslator:
    """
    Local Google Translate service for subtitle files
    Uses the free Google Translate web interface (no API key required)
    """
    
    def __init__(self, source_srt_file, dest_srt_file=None, target_lang='en', source_lang='auto', max_workers=10):
        """
        Initialize the translator
        
        Args:
            source_srt_file: Path to the source SRT file
            dest_srt_file: Path to the destination SRT file (if None, overwrites source)
            target_lang: Target language code (e.g., 'en', 'th', 'de')
            source_lang: Source language code or 'auto' for auto-detection
            max_workers: Number of parallel translation threads
        """
        self.source_srt_file = source_srt_file
        self.dest_srt_file = dest_srt_file if dest_srt_file else source_srt_file
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.max_workers = max_workers
        
        # Language code conversions for Google Translate compatibility
        self.language_code_convert_dict = {
            'he': 'iw',
            'zh': 'zh-CN',
            'zt': 'zh-TW',
        }
        
    def translate(self, progress_callback=None):
        """
        Translate the subtitle file
        
        Args:
            progress_callback: Optional callback function(current, total) for progress updates
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Load subtitles using pysubs2
            subs = pysubs2.load(self.source_srt_file, encoding='utf-8')
            subs.remove_miscellaneous_events()
            lines_list = [x.plaintext for x in subs]
            lines_list_len = len(lines_list)
            
            if lines_list_len == 0:
                logger.warning(f'No subtitle lines found in {self.source_srt_file}')
                return False
            
            logger.info(f'Starting translation of {lines_list_len} lines from {self.source_srt_file}')
            
            translated_lines = []
            translation_lock = threading.Lock()
            
            def translate_line(line_id, subtitle_line):
                """Translate a single subtitle line"""
                try:
                    if not subtitle_line or subtitle_line.strip() == '':
                        # Keep empty lines as-is
                        with translation_lock:
                            translated_lines.append({'id': line_id, 'line': subtitle_line})
                        return
                        
                    translated_text = self._translate_text(subtitle_line)
                    with translation_lock:
                        translated_lines.append({'id': line_id, 'line': translated_text})
                        
                except TranslationNotFound:
                    logger.debug(f'Unable to translate line: {subtitle_line}')
                    with translation_lock:
                        translated_lines.append({'id': line_id, 'line': subtitle_line})
                        
                except Exception as e:
                    logger.error(f'Error translating line {line_id}: {e}')
                    with translation_lock:
                        translated_lines.append({'id': line_id, 'line': subtitle_line})
                        
                finally:
                    # Report progress
                    if progress_callback:
                        with translation_lock:
                            progress_callback(len(translated_lines), lines_list_len)
            
            # Parallel translation using ThreadPoolExecutor
            logger.debug(f'Translating {lines_list_len} lines with {self.max_workers} workers')
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = []
                for i, line in enumerate(lines_list):
                    future = pool.submit(translate_line, i, line)
                    futures.append(future)
                
                # Wait for all translations to complete
                for future in futures:
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Error in translation task: {e}")
            
            # Sort translated lines by ID to maintain order
            translated_lines.sort(key=lambda x: x['id'])
            
            # Update subtitle lines with translations
            for item in translated_lines:
                line_id = item['id']
                translated_text = item['line']
                if line_id < len(subs):
                    subs[line_id].plaintext = translated_text
            
            # Save translated subtitles to destination file
            logger.info(f'Saving translated subtitles to {self.dest_srt_file}')
            subs.save(self.dest_srt_file)
            
            logger.info(f'Translation completed successfully: {self.source_srt_file} -> {self.dest_srt_file}')
            return True
            
        except Exception as e:
            logger.error(f'Translation failed: {str(e)}', exc_info=True)
            return False
    
    @retry(exceptions=(TooManyRequests, RequestError), tries=6, delay=1, backoff=2, jitter=(0, 1))
    def _translate_text(self, text):
        """
        Translate a single text using Google Translate with retry logic
        
        Args:
            text: Text to translate
            
        Returns:
            str: Translated text
        """
        try:
            # Convert target language code if needed
            target_code = self.language_code_convert_dict.get(self.target_lang, self.target_lang)
            
            # Create translator and translate
            translator = GoogleTranslator(source=self.source_lang, target=target_code)
            result = translator.translate(text=text)
            
            return result if result else text
            
        except (TooManyRequests, RequestError) as e:
            logger.warning(f'Google Translate API error (will retry): {str(e)}')
            raise
            
        except TranslationNotFound:
            logger.debug(f'Translation not found for: {text}')
            return text
            
        except Exception as e:
            logger.error(f'Unexpected error in Google translation: {str(e)}')
            return text


def translate_subtitle_file(srt_file_path, target_lang, source_lang='auto', max_workers=10, progress_callback=None):
    """
    Convenience function to translate a subtitle file
    
    Args:
        srt_file_path: Path to the SRT file to translate
        target_lang: Target language code (e.g., 'en', 'th', 'de')
        source_lang: Source language code or 'auto' for auto-detection
        max_workers: Number of parallel translation threads
        progress_callback: Optional callback function(current, total) for progress updates
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not os.path.exists(srt_file_path):
        logger.error(f'File not found: {srt_file_path}')
        return False
    
    if not srt_file_path.lower().endswith('.srt'):
        logger.error(f'File is not an SRT file: {srt_file_path}')
        return False
    
    translator = LocalGoogleTranslator(
        source_srt_file=srt_file_path,
        target_lang=target_lang,
        source_lang=source_lang,
        max_workers=max_workers
    )
    
    return translator.translate(progress_callback=progress_callback)


# For backwards compatibility with Bazarr-style interface
class GoogleTranslatorService:
    """Compatibility wrapper matching Bazarr's interface"""
    
    def __init__(self, source_srt_file, dest_srt_file, to_lang, from_lang='auto', **kwargs):
        self.source_srt_file = source_srt_file
        self.dest_srt_file = dest_srt_file
        self.to_lang = to_lang
        self.from_lang = from_lang
        
    def translate(self):
        """Execute translation"""
        translator = LocalGoogleTranslator(
            source_srt_file=self.source_srt_file,
            dest_srt_file=self.dest_srt_file,
            target_lang=self.to_lang,
            source_lang=self.from_lang
        )
        
        success = translator.translate()
        
        return self.dest_srt_file if success else False
