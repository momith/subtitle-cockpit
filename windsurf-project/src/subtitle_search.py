# -*- coding: utf-8 -*-
"""
Subtitle Search Module
Searches for subtitles using OpenSubtitles.com and Addic7ed providers
Based on subliminal library
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from babelfish import Language
from ass_cleaner import clean_ass_file
#import subliminal
#from subliminal import Video, Episode, Movie
#from subliminal.providers.opensubtitles import OpenSubtitlesProvider
#from subliminal.providers.addic7ed import Addic7edProvider

logger = logging.getLogger(__name__)


def detect_subtitle_format(content: bytes) -> str:
    """
    Detect subtitle format from content
    
    Args:
        content: Subtitle file content as bytes
        
    Returns:
        File extension: 'srt', 'ass', or 'ssa'
    """
    try:
        # Try to decode as text
        text = content.decode('utf-8', errors='ignore')
        text_lower = text.lower()
        
        # Check for ASS format (Advanced SubStation Alpha)
        if '[script info]' in text_lower and 'scripttype:' in text_lower:
            if 'scripttype: v4.00+' in text_lower:
                return 'ass'
            return 'ssa'
        
        # Check for SSA format (SubStation Alpha)
        if '[script info]' in text_lower:
            return 'ssa'
        
        # Default to SRT format
        return 'srt'
    except Exception as e:
        logger.warning(f'Error detecting subtitle format: {e}')
        return 'srt'


def convert_ass_to_srt(content: bytes) -> bytes:
    """
    Convert ASS/SSA subtitle format to SRT format
    
    Args:
        content: ASS/SSA subtitle content as bytes
        
    Returns:
        SRT formatted subtitle content as bytes
    """
    try:
        from pyasstosrt import Subtitle
    except Exception as e:
        logger.warning(f"pyasstosrt is not installed; cannot convert ASS/SSA to SRT: {e}")
        return content

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            ass_path = os.path.join(tmpdir, 'input.ass')
            with open(ass_path, 'wb') as f:
                f.write(content)

            sub = Subtitle(ass_path, removing_effects=True)
            sub.export(output_dir=tmpdir, encoding='utf-8')

            srt_name = Path(ass_path).with_suffix('.srt').name
            srt_path = os.path.join(tmpdir, srt_name)
            with open(srt_path, 'rb') as f:
                return f.read()

    except Exception as e:
        logger.exception(f'Error converting ASS/SSA to SRT: {e}')
        return content


def convert_ass_time_to_srt(ass_time: str) -> str:
    """
    Convert ASS time format (H:MM:SS.CC) to SRT format (HH:MM:SS,mmm)
    
    Args:
        ass_time: Time in ASS format (e.g., "0:01:23.45")
        
    Returns:
        Time in SRT format (e.g., "00:01:23,450")
    """
    import re
    
    try:
        # Parse ASS time: H:MM:SS.CC
        match = re.match(r'(\d+):(\d+):(\d+)\.(\d+)', ass_time)
        if not match:
            return None
        
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        centiseconds = int(match.group(4))
        
        # Convert centiseconds to milliseconds
        milliseconds = centiseconds * 10
        
        # Format as SRT: HH:MM:SS,mmm
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
        
    except Exception as e:
        logger.warning(f'Error converting ASS time to SRT: {e}')
        return None


class SubtitleSearcher:
    """Search and download subtitles using OpenSubtitles.com and Addic7ed"""
    
    def __init__(self, opensubtitles_username: str = None, opensubtitles_password: str = None,
                 addic7ed_username: str = None, addic7ed_password: str = None):
        """
        Initialize subtitle searcher with provider credentials
        
        Args:
            opensubtitles_username: OpenSubtitles.com username
            opensubtitles_password: OpenSubtitles.com password
            addic7ed_username: Addic7ed username
            addic7ed_password: Addic7ed password
        """
        self.opensubtitles_username = opensubtitles_username
        self.opensubtitles_password = opensubtitles_password
        self.addic7ed_username = addic7ed_username
        self.addic7ed_password = addic7ed_password
        
    def search_subtitles(self, video_path: str, languages: List[str], 
                        providers: List[str] = None) -> List[Dict]:
        """
        Search for subtitles for a video file
        
        Args:
            video_path: Absolute path to the video file
            languages: List of language codes (e.g., ['en', 'de', 'fr'])
            providers: List of providers to use (default: ['opensubtitles', 'addic7ed'])
            
        Returns:
            List of subtitle dictionaries with metadata
        """
        try:
            import subliminal
        except Exception as e:
            raise RuntimeError(
                "Subtitle search requires the 'subliminal' package. Install it to use this feature."
            ) from e

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        if providers is None:
            providers = []
            if self.opensubtitles_username and self.opensubtitles_password:
                providers.append('opensubtitles')
            if self.addic7ed_username and self.addic7ed_password:
                providers.append('addic7ed')
            
            if not providers:
                raise ValueError("No provider credentials configured")
        
        # Convert language codes to Language objects (support common forms like 'en', 'eng', 'en-US')
        language_set = set()
        for code in languages:
            if not code:
                continue
            code = str(code).strip()
            try:
                # Try alpha2 (e.g. 'en', 'de')
                if len(code) == 2:
                    lang_obj = Language.fromalpha2(code)
                # Try alpha3 (e.g. 'eng')
                elif len(code) == 3:
                    lang_obj = Language.fromalpha3(code)
                else:
                    # Fall back to IETF (e.g. 'en-US')
                    lang_obj = Language.fromietf(code)
                language_set.add(lang_obj)
            except ValueError:
                logger.warning(f"Invalid language code for subtitle search, skipping: {code}")

        if not language_set:
            raise ValueError(f"No valid language codes for subtitle search: {languages}")
        
        # Scan video file to extract metadata
        logger.info(f'Scanning video file: {video_path}')
        video = subliminal.scan_video(video_path)
        
        # Build provider configs
        provider_configs = {}
        if 'opensubtitles' in providers and self.opensubtitles_username:
            provider_configs['opensubtitles'] = {
                'username': self.opensubtitles_username,
                'password': self.opensubtitles_password
            }
        if 'addic7ed' in providers and self.addic7ed_username:
            provider_configs['addic7ed'] = {
                'username': self.addic7ed_username,
                'password': self.addic7ed_password
            }
        
        # Search for subtitles
        logger.info(f'Searching subtitles with providers: {providers}')
        subtitles_dict = subliminal.list_subtitles({video}, language_set,
                                                   providers=providers,
                                                   provider_configs=provider_configs)
        
        # Extract subtitles for this video
        subtitles = subtitles_dict.get(video, [])
        logger.info(f'Found {len(subtitles)} subtitle(s)')
        
        # Convert to serializable dictionaries with scoring
        results = []
        for subtitle in subtitles:
            # Compute score (how well the subtitle matches)
            matches = subtitle.get_matches(video)
            score = subliminal.compute_score(subtitle, video)
            
            result = {
                'id': str(subtitle.id) if hasattr(subtitle, 'id') else None,
                'provider': subtitle.provider_name,
                'language': str(subtitle.language),
                'hearing_impaired': subtitle.hearing_impaired if hasattr(subtitle, 'hearing_impaired') else False,
                'release_info': subtitle.release_info if hasattr(subtitle, 'release_info') else None,
                'page_link': subtitle.page_link if hasattr(subtitle, 'page_link') else None,
                'score': score,
                'matches': list(matches),
                'subtitle_object': subtitle  # Keep for download
            }
            results.append(result)
        
        # Sort by score (descending)
        results.sort(key=lambda x: x['score'], reverse=True)
        
        return results
    
    def download_subtitle(self, subtitle_dict: Dict, video_path: str, output_dir: str = None) -> Optional[str]:
        """
        Download a specific subtitle
        
        Args:
            subtitle_dict: Subtitle dictionary from search_subtitles()
            video_path: Original video file path
            output_dir: Directory to save subtitle (default: same as video)
            
        Returns:
            Path to downloaded subtitle file, or None if failed
        """
        try:
            import subliminal
        except Exception as e:
            raise RuntimeError(
                "Subtitle download requires the 'subliminal' package. Install it to use this feature."
            ) from e

        subtitle = subtitle_dict.get('subtitle_object')
        if not subtitle:
            logger.error('No subtitle object found in dictionary')
            return None
        
        video = subliminal.scan_video(video_path)
        provider_name = subtitle.provider_name
        
        # Build provider config
        provider_configs = {}
        if provider_name == 'opensubtitles' and self.opensubtitles_username:
            provider_configs['opensubtitles'] = {
                'username': self.opensubtitles_username,
                'password': self.opensubtitles_password
            }
        elif provider_name == 'addic7ed' and self.addic7ed_username:
            provider_configs['addic7ed'] = {
                'username': self.addic7ed_username,
                'password': self.addic7ed_password
            }
        
        try:
            # Download subtitle
            logger.info(f'Downloading subtitle from {provider_name}')
            subliminal.download_subtitles([subtitle], providers=[provider_name],
                                         provider_configs=provider_configs)
            
            # Save subtitle
            if output_dir is None:
                output_dir = os.path.dirname(video_path)
            
            # Detect actual subtitle format from content
            detected_format = detect_subtitle_format(subtitle.content)
            logger.info(f'Detected subtitle format: {detected_format}')
            
            # Generate base output filename with correct extension
            video_basename = os.path.splitext(os.path.basename(video_path))[0]
            lang = subtitle.language
            base_filename = f"{video_basename}.{lang}.{detected_format}"
            output_path = os.path.join(output_dir, base_filename)

            # Avoid overwriting existing files: append numeric suffix if needed
            if os.path.exists(output_path):
                index = 1
                while True:
                    alt_filename = f"{video_basename}.{lang}.{index}.{detected_format}"
                    alt_path = os.path.join(output_dir, alt_filename)
                    if not os.path.exists(alt_path):
                        output_path = alt_path
                        break
                    index += 1

            # Save subtitle content in original format
            logger.info(f'Saving subtitle to: {output_path}')
            with open(output_path, 'wb') as f:
                f.write(subtitle.content)
            
            logger.info(f'Successfully downloaded subtitle: {output_path}')
            
            # If format is ASS or SSA, convert to SRT automatically
            if detected_format in ['ass', 'ssa']:
                logger.info(f'Converting {detected_format.upper()} to SRT format')
                try:
                    ass_content = subtitle.content
                    if detected_format == 'ass':
                        try:
                            clean_ass_file(Path(output_path), Path(output_path))
                            ass_content = Path(output_path).read_bytes()
                        except Exception as e:
                            logger.exception(f'Failed to clean ASS before conversion; converting original content: {e}')

                    srt_content = convert_ass_to_srt(ass_content)
                    
                    # Generate SRT filename
                    srt_base_filename = f"{video_basename}.{lang}.srt"
                    srt_output_path = os.path.join(output_dir, srt_base_filename)
                    
                    # Avoid overwriting existing SRT files
                    if os.path.exists(srt_output_path):
                        index = 1
                        while True:
                            alt_srt_filename = f"{video_basename}.{lang}.{index}.srt"
                            alt_srt_path = os.path.join(output_dir, alt_srt_filename)
                            if not os.path.exists(alt_srt_path):
                                srt_output_path = alt_srt_path
                                break
                            index += 1
                    
                    # Save converted SRT file
                    with open(srt_output_path, 'wb') as f:
                        f.write(srt_content)
                    
                    logger.info(f'Successfully converted and saved SRT file: {srt_output_path}')
                    
                except Exception as e:
                    logger.exception(f'Failed to convert {detected_format.upper()} to SRT: {e}')
            
            return output_path
            
        except Exception as e:
            logger.exception(f'Failed to download subtitle: {e}')
            return None


def search_and_list(video_path: str, languages: List[str], 
                    opensubtitles_username: str = None, opensubtitles_password: str = None,
                    addic7ed_username: str = None, addic7ed_password: str = None) -> List[Dict]:
    """
    Convenience function to search for subtitles
    
    Args:
        video_path: Path to video file
        languages: List of language codes
        opensubtitles_username: OpenSubtitles.com username
        opensubtitles_password: OpenSubtitles.com password
        addic7ed_username: Addic7ed username
        addic7ed_password: Addic7ed password
        
    Returns:
        List of subtitle dictionaries
    """
    searcher = SubtitleSearcher(
        opensubtitles_username=opensubtitles_username,
        opensubtitles_password=opensubtitles_password,
        addic7ed_username=addic7ed_username,
        addic7ed_password=addic7ed_password
    )
    
    return searcher.search_subtitles(video_path, languages)
