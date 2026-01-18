# -*- coding: utf-8 -*-
"""
Subtitle Search Module
Searches for subtitles using OpenSubtitles.com and Addic7ed providers
Based on subliminal library
"""

import logging
import os
from typing import List, Dict, Optional
from babelfish import Language
import subliminal
from subliminal import Video, Episode, Movie
from subliminal.providers.opensubtitles import OpenSubtitlesProvider
from subliminal.providers.addic7ed import Addic7edProvider

logger = logging.getLogger(__name__)


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
            
            # Generate base output filename
            video_basename = os.path.splitext(os.path.basename(video_path))[0]
            lang = subtitle.language
            base_filename = f"{video_basename}.{lang}.srt"
            output_path = os.path.join(output_dir, base_filename)

            # Avoid overwriting existing files: append numeric suffix if needed
            if os.path.exists(output_path):
                index = 1
                while True:
                    alt_filename = f"{video_basename}.{lang}.{index}.srt"
                    alt_path = os.path.join(output_dir, alt_filename)
                    if not os.path.exists(alt_path):
                        output_path = alt_path
                        break
                    index += 1

            # Save subtitle content
            logger.info(f'Saving subtitle to: {output_path}')
            with open(output_path, 'wb') as f:
                f.write(subtitle.content)
            
            logger.info(f'Successfully downloaded subtitle: {output_path}')
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
