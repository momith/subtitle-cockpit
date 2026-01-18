"""
OCR Subtitle Converter - Convert SUB/SUP files to SRT using Tesseract OCR

This module provides functionality to extract subtitle images from SUB (VobSub) and SUP (Blu-ray)
files and perform OCR using Tesseract 5.5.0 to generate SRT subtitle files.

Based on the SubtitleEdit OCR implementation.
"""

import os
import struct
import tempfile
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
from datetime import timedelta
import re
import concurrent.futures

try:
    from vobsub_parser import parseSub
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from vobsub_parser import parseSub

try:
    from PIL import Image, ImageDraw
    import numpy as np
except ImportError:
    print("ERROR: Required packages not installed. Please run:")
    print("pip install Pillow numpy")
    exit(1)


@dataclass
class TimeCode:
    """Represents a subtitle timecode"""
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    milliseconds: int = 0
    
    @classmethod
    def from_milliseconds(cls, ms: float):
        """Create TimeCode from milliseconds"""
        total_seconds = int(ms / 1000)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        milliseconds = int(ms % 1000)
        return cls(hours, minutes, seconds, milliseconds)
    
    @classmethod
    def from_pts(cls, pts: int):
        """Create TimeCode from PTS timestamp (90kHz clock)"""
        ms = pts / 90.0
        return cls.from_milliseconds(ms)
    
    def to_srt_format(self) -> str:
        """Convert to SRT format: HH:MM:SS,mmm"""
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d},{self.milliseconds:03d}"
    
    def total_milliseconds(self) -> float:
        """Get total milliseconds"""
        return (self.hours * 3600000 + self.minutes * 60000 + 
                self.seconds * 1000 + self.milliseconds)


@dataclass
class SubtitleEntry:
    """Represents a single subtitle entry"""
    index: int
    start_time: TimeCode
    end_time: TimeCode
    text: str
    
    def to_srt(self) -> str:
        """Convert to SRT format"""
        return (f"{self.index}\n"
                f"{self.start_time.to_srt_format()} --> {self.end_time.to_srt_format()}\n"
                f"{self.text}\n")


class BluRaySupParser:
    """Parser for Blu-ray SUP files"""
    
    SEGMENT_TYPE_PCS = 0x16  # Picture Control Segment
    SEGMENT_TYPE_WDS = 0x17  # Window Definition Segment
    SEGMENT_TYPE_PDS = 0x14  # Palette Definition Segment
    SEGMENT_TYPE_ODS = 0x15  # Object Definition Segment
    SEGMENT_TYPE_END = 0x80  # End of Display Set
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.subtitles = []
        
    def parse(self) -> List[Dict]:
        """Parse SUP file and extract subtitle data with proper end times"""
        subtitles = []
        current_subtitle = None
        
        with open(self.filepath, 'rb') as f:
            while True:
                # Read segment header (13 bytes)
                header = f.read(13)
                if len(header) < 13:
                    break
                
                # Check for PG magic bytes
                if header[0] != 0x50 or header[1] != 0x47:  # 'PG'
                    break
                
                # Parse header
                pts = struct.unpack('>I', header[2:6])[0]
                dts = struct.unpack('>I', header[6:10])[0]
                segment_type = header[10]
                segment_size = struct.unpack('>H', header[11:13])[0]
                
                # Read segment data
                segment_data = f.read(segment_size)
                if len(segment_data) < segment_size:
                    break
                
                # Process PCS (Picture Control Segment)
                if segment_type == self.SEGMENT_TYPE_PCS:
                    pcs_data = self._parse_pcs(segment_data, pts)
                    if pcs_data:
                        # Check if this is a "clear" segment (no objects = end previous subtitle)
                        if pcs_data['num_objects'] == 0 and current_subtitle:
                            # This PCS clears the screen - set end time for previous subtitle
                            current_subtitle['end_pts'] = pts
                            current_subtitle = None
                        elif pcs_data['num_objects'] > 0:
                            # New subtitle with objects - start collecting segments
                            current_subtitle = {
                                'pcs': pcs_data,
                                'palettes': [],
                                'objects': [],
                                'start_pts': pts,
                                'end_pts': None  # Will be set when we find the clear segment
                            }
                            subtitles.append(current_subtitle)
                
                # Process PDS (Palette Definition Segment)
                elif segment_type == self.SEGMENT_TYPE_PDS and current_subtitle:
                    palette = self._parse_pds(segment_data)
                    current_subtitle['palettes'].append(palette)
                
                # Process ODS (Object Definition Segment)
                elif segment_type == self.SEGMENT_TYPE_ODS and current_subtitle:
                    obj_data = self._parse_ods(segment_data)
                    if obj_data:
                        if obj_data.get('is_first'):
                            # First fragment - add as new object
                            current_subtitle['objects'].append(obj_data)
                        elif not obj_data.get('is_first') and current_subtitle['objects']:
                            # Continuation fragment - append data to last object
                            last_obj = current_subtitle['objects'][-1]
                            if 'data' in last_obj and 'data' in obj_data:
                                last_obj['data'] += obj_data['data']
        
        return subtitles
    
    def _parse_pcs(self, data: bytes, pts: int) -> Optional[Dict]:
        """Parse Picture Control Segment"""
        if len(data) < 11:
            return None
        
        width = struct.unpack('>H', data[0:2])[0]
        height = struct.unpack('>H', data[2:4])[0]
        frame_rate = data[4]
        comp_num = struct.unpack('>H', data[5:7])[0]
        comp_state = data[7]
        palette_update = data[8] == 0x80
        palette_id = data[9]
        num_objects = data[10]
        
        objects = []
        offset = 11
        for i in range(num_objects):
            if offset + 8 <= len(data):
                obj_id = struct.unpack('>H', data[offset:offset+2])[0]
                window_id = data[offset+2]
                flags = data[offset+3]
                x = struct.unpack('>H', data[offset+4:offset+6])[0]
                y = struct.unpack('>H', data[offset+6:offset+8])[0]
                objects.append({'id': obj_id, 'x': x, 'y': y})
                offset += 8
        
        return {
            'width': width,
            'height': height,
            'comp_num': comp_num,
            'palette_id': palette_id,
            'num_objects': num_objects,
            'objects': objects,
            'pts': pts
        }
    
    def _parse_pds(self, data: bytes) -> Dict:
        """Parse Palette Definition Segment"""
        palette_id = data[0]
        palette_version = data[1]
        
        # Each palette entry is 5 bytes: index, Y, Cr, Cb, Alpha
        num_entries = (len(data) - 2) // 5
        palette = {}
        
        for i in range(num_entries):
            offset = 2 + i * 5
            idx = data[offset]
            y = data[offset + 1]
            cr = data[offset + 2]
            cb = data[offset + 3]
            alpha = data[offset + 4]
            
            # Convert YCbCr to RGB using BT.709 standard (same as SubtitleEdit)
            # BT.709 for YCbCr 16..235 -> RGB 0..255 (PC)
            y_adj = y - 16
            cb_adj = cb - 128
            cr_adj = cr - 128
            
            y1 = y_adj * 1.164383562
            r = y1 + cr_adj * 1.792741071
            g = y1 - cr_adj * 0.5329093286 - cb_adj * 0.2132486143
            b = y1 + cb_adj * 2.112401786
            
            # Clamp to 0-255 range
            r = max(0, min(255, int(r + 0.5)))
            g = max(0, min(255, int(g + 0.5)))
            b = max(0, min(255, int(b + 0.5)))
            
            palette[idx] = (r, g, b, alpha)
        
        return palette
    
    def _parse_ods(self, data: bytes) -> Optional[Dict]:
        """Parse Object Definition Segment"""
        if len(data) < 4:
            return None
        
        obj_id = struct.unpack('>H', data[0:2])[0]
        obj_version = data[2]
        sequence = data[3]
        
        is_first = (sequence & 0x80) == 0x80
        is_last = (sequence & 0x40) == 0x40
        
        if is_first and len(data) >= 11:
            width = struct.unpack('>H', data[7:9])[0]
            height = struct.unpack('>H', data[9:11])[0]
            image_data = data[11:]
            
            return {
                'id': obj_id,
                'width': width,
                'height': height,
                'data': image_data,
                'is_first': is_first
            }
        elif not is_first:
            # Fragment - append to previous
            return {
                'id': obj_id,
                'data': data[4:],
                'is_first': False
            }
        
        return None
    
    def decode_image(self, obj_data: Dict, palette: Dict) -> Optional[Image.Image]:
        """Decode RLE-compressed image data"""
        if not obj_data or 'width' not in obj_data:
            return None
        
        width = obj_data['width']
        height = obj_data['height']
        data = obj_data['data']
        
        if width <= 0 or height <= 0:
            return None
        
        # Create RGBA image
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        pixels = img.load()
        
        x, y = 0, 0
        i = 0
        
        while i < len(data) and y < height:
            b = data[i]
            i += 1
            
            if b == 0 and i < len(data):
                b2 = data[i]
                i += 1
                
                if b2 == 0:
                    # New line
                    x = 0
                    y += 1
                elif (b2 & 0xC0) == 0x40:
                    # 00 4x xx -> xxx zeros
                    if i < len(data):
                        count = ((b2 - 0x40) << 8) + data[i]
                        i += 1
                        color = palette.get(0, (0, 0, 0, 0))
                        for _ in range(count):
                            if x < width and y < height:
                                pixels[x, y] = color
                            x += 1
                elif (b2 & 0xC0) == 0x80:
                    # 00 8x yy -> x times value y
                    count = b2 - 0x80
                    if i < len(data):
                        color_idx = data[i]
                        i += 1
                        color = palette.get(color_idx, (0, 0, 0, 0))
                        for _ in range(count):
                            if x < width and y < height:
                                pixels[x, y] = color
                            x += 1
                elif (b2 & 0xC0) != 0:
                    # 00 cx yy zz -> xyy times value z
                    if i + 1 < len(data):
                        count = ((b2 - 0xC0) << 8) + data[i]
                        i += 1
                        color_idx = data[i]
                        i += 1
                        color = palette.get(color_idx, (0, 0, 0, 0))
                        for _ in range(count):
                            if x < width and y < height:
                                pixels[x, y] = color
                            x += 1
                else:
                    # 00 xx -> xx times 0
                    color = palette.get(0, (0, 0, 0, 0))
                    for _ in range(b2):
                        if x < width and y < height:
                            pixels[x, y] = color
                        x += 1
            else:
                # Single pixel
                color = palette.get(b, (0, 0, 0, 0))
                if x < width and y < height:
                    pixels[x, y] = color
                x += 1
        
        return img

class TesseractOCR:
    """Wrapper for Tesseract OCR"""
    
    def __init__(self, tesseract_path: str = None, tessdata_path: str = None):
        """
        Initialize Tesseract OCR
        
        Args:
            tesseract_path: Path to tesseract.exe (default: searches in PATH)
            tessdata_path: Path to tessdata directory
        """
        if tesseract_path:
            self.tesseract_cmd = tesseract_path
        else:
            # Try to find tesseract in common locations
            self.tesseract_cmd = self._find_tesseract()
        
        self.tessdata_path = tessdata_path
        
    def _find_tesseract(self) -> str:
        """Find tesseract executable"""
        # Check if running on Windows or Linux
        if os.name == 'nt':
            # Windows paths
            common_paths = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                "tesseract.exe"
            ]
        else:
            # Linux/Unix paths
            common_paths = [
                "/usr/bin/tesseract",
                "/usr/local/bin/tesseract",
                "/opt/tesseract/bin/tesseract"
            ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        # Try to find in PATH
        return "tesseract"
    
    def preprocess_image(self, img: Image.Image) -> Image.Image:
        """
        Preprocess image:
        1. Convert black pixels to transparent
        2. Non-transparent pixels → black
        3. Transparent pixels → white
        4. Add white margin
        """
        # Convert to RGBA if not already
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Convert to numpy array
        img_array = np.array(img)
        
        # Extract channels
        r = img_array[:, :, 0]
        g = img_array[:, :, 1]
        b = img_array[:, :, 2]
        alpha = img_array[:, :, 3]
        
        # Convert black pixels (R=0, G=0, B=0) to transparent
        is_black = (r == 0) & (g == 0) & (b == 0)
        alpha = np.where(is_black, 0, alpha)
        
        # Create output: if alpha > 0 (not transparent) → 0 (black), else → 255 (white)
        output = np.where(alpha > 0, 0, 255).astype(np.uint8)
        
        # Convert to PIL Image (grayscale)
        img_processed = Image.fromarray(output, mode='L')
        
        # Add white margin
        img_processed = self._add_margin(img_processed, 10)
        
        return img_processed
    
    def _crop_transparent(self, img: Image.Image) -> Image.Image:
        """Crop transparent areas from image"""
        # Get bounding box of non-transparent pixels
        if img.mode != 'RGBA':
            return img
        
        # Get alpha channel
        alpha = img.split()[-1]
        bbox = alpha.getbbox()
        
        if bbox:
            return img.crop(bbox)
        return img
    
    def _add_margin(self, img: Image.Image, margin: int) -> Image.Image:
        """Add white margin around image"""
        new_width = img.width + 2 * margin
        new_height = img.height + 2 * margin
        
        # Create image with white margin
        new_img = Image.new('L', (new_width, new_height), 255)  # White background
        
        # Convert input to grayscale if needed
        if img.mode == 'RGBA':
            # Convert RGBA to L (grayscale)
            img_gray = img.convert('L')
        else:
            img_gray = img
        
        # Paste the grayscale image onto white background
        new_img.paste(img_gray, (margin, margin))
        
        return new_img
    
    def ocr_image(self, img: Image.Image, language: str = 'eng', 
                  psm: int = 6, oem: int = 3, debug_save_path: str = None) -> str:
        """
        Perform OCR on image using Tesseract
        
        Args:
            img: PIL Image to OCR
            language: Tesseract language code (e.g., 'eng', 'deu')
            psm: Page segmentation mode (6 = uniform block of text)
            oem: OCR Engine mode (0 = legacy = default)
            debug_save_path: Optional path to save preprocessed image for debugging
        
        Returns:
            Extracted text
        """
        # Preprocess image
        img_processed = self.preprocess_image(img)
        
        # Save preprocessed image for debugging
        if debug_save_path:
            img_processed.save(debug_save_path)
            print(f"Saved preprocessed image: {debug_save_path}")
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_img:
            img_processed.save(tmp_img.name, 'PNG')
            tmp_img_path = tmp_img.name
        
        try:
            # Run Tesseract
            text = self._run_tesseract(tmp_img_path, language, psm, oem, debug_save_path)
            return text.strip()
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_img_path)
            except:
                pass
    
    def _run_tesseract(self, image_path: str, language: str, 
                       psm: int, oem: int, debug_save_path: str = None) -> str:
        """Run Tesseract command and get plain text output"""
        with tempfile.NamedTemporaryFile(suffix='', delete=False) as tmp_out:
            output_base = tmp_out.name
        
        try:
            # Build command for plain text output
            cmd = [self.tesseract_cmd, image_path, output_base, '-l', language]
            
            if self.tessdata_path:
                cmd.insert(1, '--tessdata-dir')
                cmd.insert(2, self.tessdata_path)
            
            cmd.extend(['--psm', str(psm)])
            cmd.extend(['--oem', str(oem)])
            
            # Debug: print command for first few
            if debug_save_path:
                print(f"Tesseract command: {' '.join(cmd)}")
            
            # Run Tesseract
            timeout_seconds = _get_tesseract_timeout_seconds()
            result = subprocess.run(cmd, capture_output=True, text=True, 
                                   timeout=timeout_seconds, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            
            if debug_save_path and result.stderr:
                print(f"Tesseract stderr: {result.stderr}")
            
            # Read text output
            txt_path = output_base + '.txt'
            
            if os.path.exists(txt_path):
                with open(txt_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                
                if debug_save_path:
                    print(f"OCR text output: '{text.strip()}'")
                
                return text.strip()
            
            return ""
        
        finally:
            # Clean up temp files
            for ext in ['', '.txt']:
                try:
                    os.unlink(output_base + ext)
                except:
                    pass
    
    def _parse_hocr(self, hocr: str) -> str:
        """Parse HOCR HTML output to extract text"""
        lines = []
        
        # Find all ocr_line spans (handle both single and double quotes)
        line_pattern = r'<span class=["\']ocr_line["\'][^>]*>(.*?)</span>'
        line_matches = re.finditer(line_pattern, hocr, re.DOTALL)
        
        for line_match in line_matches:
            line_content = line_match.group(1)
            
            # Find all ocrx_word spans within this line (handle both quote styles)
            word_pattern = r'<span class=["\']ocrx_word["\'][^>]*>(.*?)</span>'
            word_matches = re.finditer(word_pattern, line_content, re.DOTALL)
            
            words = []
            for word_match in word_matches:
                word_text = word_match.group(1)
                # Remove any remaining HTML tags
                word_text = re.sub(r'<[^>]+>', '', word_text)
                # Handle HTML entities
                word_text = word_text.replace('&amp;', '&')
                word_text = word_text.replace('&lt;', '<')
                word_text = word_text.replace('&gt;', '>')
                word_text = word_text.replace('&quot;', '"')
                word_text = word_text.replace('&#39;', "'")
                word_text = word_text.replace('&apos;', "'")
                # Handle italics
                word_text = word_text.replace('<em>', '<i>').replace('</em>', '</i>')
                word_text = word_text.replace('<strong>', '').replace('</strong>', '')
                
                if word_text.strip():
                    words.append(word_text.strip())
            
            if words:
                lines.append(' '.join(words))
        
        # If no lines found with the pattern above, try alternative parsing
        if not lines:
            # Try to find any text content in span elements
            all_spans = re.finditer(r'<span[^>]*>(.*?)</span>', hocr, re.DOTALL)
            for span in all_spans:
                text = span.group(1)
                # Skip if it contains other HTML tags (nested spans)
                if '<span' not in text:
                    text = re.sub(r'<[^>]+>', '', text).strip()
                    if text and len(text) > 0:
                        lines.append(text)
        
        return '\n'.join(lines)


def _get_ocr_worker_count() -> int:
    env = os.getenv('OCR_WORKERS')
    if env:
        try:
            v = int(env)
            return max(1, v)
        except Exception:
            return max(1, os.cpu_count() or 1)
    return max(1, os.cpu_count() or 1)


def _print_progress(message: str) -> None:
    if sys.stdout.isatty():
        print(message, end='\r', flush=True)
    else:
        print(message, flush=True)


def _get_tesseract_timeout_seconds() -> int:
    env = os.getenv('TESSERACT_TIMEOUT_SECONDS')
    if env:
        try:
            v = int(env)
            return max(1, v)
        except Exception:
            return 120
    return 120


def convert_sup_to_srt(sup_path: str, output_path: str, language: str = 'eng',
                       tesseract_path: str = None, tessdata_path: str = None,
                       debug_mode: bool = False, debug_subtitle_index: int = None) -> bool:
    """
    Convert SUP file to SRT using Tesseract OCR
    
    Args:
        sup_path: Path to input SUP file
        output_path: Path to output SRT file
        language: Tesseract language code (e.g., 'eng', 'deu')
        tesseract_path: Path to tesseract.exe
        tessdata_path: Path to tessdata directory
        debug_mode: If True, save preprocessed images for debugging
        debug_subtitle_index: If set, only save debug images for this subtitle index (1-based)
    
    Returns:
        True if successful, False otherwise
    """
    print(f"Converting {sup_path} to {output_path}...")
    
    # Parse SUP file
    parser = BluRaySupParser(sup_path)
    subtitles = parser.parse()
    
    if not subtitles:
        print("ERROR: No subtitles found in SUP file")
        return False
    
    print(f"Found {len(subtitles)} subtitle entries")
    
    # Initialize OCR
    worker_count = _get_ocr_worker_count()
    print(f"OCR workers: {worker_count}")

    def _ocr_one_sup(i: int, img: Image.Image, debug_path: Optional[str]) -> Tuple[int, str]:
        ocr = TesseractOCR(tesseract_path, tessdata_path)
        text = ocr.ocr_image(img, language, debug_save_path=debug_path)
        return i, text

    pending: List[Tuple[int, Dict, Image.Image, Optional[str]]] = []
    for idx, sub in enumerate(subtitles, 1):
        if idx == 1 or idx % 25 == 0:
            _print_progress(f"Decoding subtitle images {idx}/{len(subtitles)}...")

        # Get palette (use last one if multiple)
        palette = sub['palettes'][-1] if sub['palettes'] else {}

        # Get first object (main subtitle image)
        if not sub['objects']:
            continue

        obj = sub['objects'][0]

        # Decode image (do this in main thread)
        img = parser.decode_image(obj, palette)
        if not img:
            continue

        debug_path = None
        if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
            original_debug_path = f"debug_sub{idx}_original.png"
            img.save(original_debug_path)
            print(f"\n[DEBUG] Saved original image: {original_debug_path}")
            debug_path = f"debug_sub{idx}_preprocessed.png"

        pending.append((idx, sub, img, debug_path))

    if not sys.stdout.isatty():
        print(f"Decoded {len(pending)} subtitle images", flush=True)

    srt_entries = []
    if worker_count <= 1 or len(pending) <= 1:
        for idx, sub, img, debug_path in pending:
            _print_progress(f"OCR subtitle {idx}/{len(pending)}...")
            _, text = _ocr_one_sup(idx, img, debug_path)
            if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
                if debug_path:
                    print(f"[DEBUG] Saved preprocessed image: {debug_path}")
                print(f"[DEBUG] OCR result for subtitle {idx}: '{text}'")

            if text:
                start_time = TimeCode.from_pts(sub['start_pts'])
                if sub.get('end_pts'):
                    end_time = TimeCode.from_pts(sub['end_pts'])
                else:
                    end_time = TimeCode.from_milliseconds(start_time.total_milliseconds() + 3000)
                srt_entries.append({'start_time': start_time, 'end_time': end_time, 'text': text, 'original_index': idx})
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_item: Dict[concurrent.futures.Future, Tuple[int, Dict, Optional[str]]] = {}
            for idx, sub, img, debug_path in pending:
                future = executor.submit(_ocr_one_sup, idx, img, debug_path)
                future_to_item[future] = (idx, sub, debug_path)

            done_count = 0
            for future in concurrent.futures.as_completed(future_to_item):
                idx, sub, debug_path = future_to_item[future]
                done_count += 1
                _print_progress(f"OCR subtitle {done_count}/{len(pending)}...")
                try:
                    _, text = future.result()
                except Exception as e:
                    print(f"\nERROR: OCR failed for subtitle {idx}: {e}", flush=True)
                    continue

                if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
                    if debug_path:
                        print(f"[DEBUG] Saved preprocessed image: {debug_path}")
                    print(f"[DEBUG] OCR result for subtitle {idx}: '{text}'")

                if text:
                    start_time = TimeCode.from_pts(sub['start_pts'])
                    if sub.get('end_pts'):
                        end_time = TimeCode.from_pts(sub['end_pts'])
                    else:
                        end_time = TimeCode.from_milliseconds(start_time.total_milliseconds() + 3000)
                    srt_entries.append({'start_time': start_time, 'end_time': end_time, 'text': text, 'original_index': idx})

        srt_entries.sort(key=lambda x: x.get('original_index', 0))
    
    print(f"\nProcessed {len(srt_entries)} subtitles with text")
    
    # Write SRT file with sequential numbering
    with open(output_path, 'w', encoding='utf-8') as f:
        for srt_idx, entry in enumerate(srt_entries, 1):
            # Create SubtitleEntry and write to file
            subtitle_entry = SubtitleEntry(
                index=srt_idx,
                start_time=entry['start_time'],
                end_time=entry['end_time'],
                text=entry['text']
            )
            f.write(subtitle_entry.to_srt())
            f.write('\n')
    
    print(f"Saved SRT to: {output_path}")
    return True


def convert_sub_to_srt(sub_path: str, output_path: str, language: str = 'eng',
                       tesseract_path: str = None, tessdata_path: str = None,
                       debug_mode: bool = False, debug_subtitle_index: int = None) -> bool:
    """
    Convert SUB/IDX file to SRT using Tesseract OCR
    
    Args:
        sub_path: Path to input SUB file
        output_path: Path to output SRT file
        language: Tesseract language code (e.g., 'eng', 'deu')
        tesseract_path: Path to tesseract.exe
        tessdata_path: Path to tessdata directory
        debug_mode: If True, save preprocessed images for debugging
        debug_subtitle_index: If set, only save debug images for this subtitle index (1-based)
    
    Returns:
        True if successful, False otherwise
    """
    print(f"Converting {sub_path} to {output_path}...")
    
    subtitles = parseSub(sub_path)
    
    if not subtitles:
        print("ERROR: No subtitles found in SUB file")
        return False
    
    print(f"Found {len(subtitles)} subtitle entries")
    
    worker_count = _get_ocr_worker_count()
    print(f"OCR workers: {worker_count}")

    def _ocr_one_vobsub(i: int, img: Image.Image, debug_path: Optional[str]) -> Tuple[int, str]:
        ocr = TesseractOCR(tesseract_path, tessdata_path)
        text = ocr.ocr_image(img, language, debug_save_path=debug_path)
        return i, text

    pending_vobsub: List[Tuple[int, object, Image.Image, Optional[str]]] = []
    for idx, sub in enumerate(subtitles, 1):
        if idx == 1 or idx % 25 == 0:
            _print_progress(f"Collecting subtitle images {idx}/{len(subtitles)}...")
        img = sub.image
        if not img:
            continue

        debug_path = None
        if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
            original_debug_path = f"debug_sub{idx}_original.png"
            img.save(original_debug_path)
            print(f"\n[DEBUG] Saved original image: {original_debug_path}")
            debug_path = f"debug_sub{idx}_preprocessed.png"

        pending_vobsub.append((idx, sub, img, debug_path))

    srt_entries = []
    if worker_count <= 1 or len(pending_vobsub) <= 1:
        for idx, sub, img, debug_path in pending_vobsub:
            _print_progress(f"OCR subtitle {idx}/{len(pending_vobsub)}...")
            _, text = _ocr_one_vobsub(idx, img, debug_path)
            if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
                if debug_path:
                    print(f"[DEBUG] Saved preprocessed image: {debug_path}")
                print(f"[DEBUG] OCR result for subtitle {idx}: '{text}'")

            if text:
                start_time = TimeCode.from_milliseconds(sub.start_ms)
                end_time = TimeCode.from_milliseconds(sub.end_ms)
                srt_entries.append({'start_time': start_time, 'end_time': end_time, 'text': text, 'original_index': idx})
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_item_vobsub: Dict[concurrent.futures.Future, Tuple[int, object, Optional[str]]] = {}
            for idx, sub, img, debug_path in pending_vobsub:
                future = executor.submit(_ocr_one_vobsub, idx, img, debug_path)
                future_to_item_vobsub[future] = (idx, sub, debug_path)

            done_count = 0
            for future in concurrent.futures.as_completed(future_to_item_vobsub):
                idx, sub, debug_path = future_to_item_vobsub[future]
                done_count += 1
                _print_progress(f"OCR subtitle {done_count}/{len(pending_vobsub)}...")
                try:
                    _, text = future.result()
                except Exception as e:
                    print(f"\nERROR: OCR failed for subtitle {idx}: {e}", flush=True)
                    continue

                if debug_mode and (debug_subtitle_index is None or idx == debug_subtitle_index):
                    if debug_path:
                        print(f"[DEBUG] Saved preprocessed image: {debug_path}")
                    print(f"[DEBUG] OCR result for subtitle {idx}: '{text}'")

                if text:
                    start_time = TimeCode.from_milliseconds(sub.start_ms)
                    end_time = TimeCode.from_milliseconds(sub.end_ms)
                    srt_entries.append({'start_time': start_time, 'end_time': end_time, 'text': text, 'original_index': idx})

        srt_entries.sort(key=lambda x: x.get('original_index', 0))
    
    print(f"\nProcessed {len(srt_entries)} subtitles with text")
    
    if not srt_entries:
        print("ERROR: No text extracted from subtitles")
        return False
    
    # Write SRT file with sequential numbering
    with open(output_path, 'w', encoding='utf-8') as f:
        for srt_idx, entry in enumerate(srt_entries, 1):
            # Create SubtitleEntry and write to file
            subtitle_entry = SubtitleEntry(
                index=srt_idx,
                start_time=entry['start_time'],
                end_time=entry['end_time'],
                text=entry['text']
            )
            f.write(subtitle_entry.to_srt())
            f.write('\n')
    
    print(f"Saved SRT to: {output_path}")
    return True


def main():
    """Main entry point for command-line usage"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Convert SUB/SUP subtitle files to SRT using Tesseract OCR'
    )
    parser.add_argument('input', help='Input SUB or SUP file')
    parser.add_argument('language', help='Language code (e.g., eng, deu)')
    parser.add_argument('output', nargs='?', help='Output SRT file (optional)')
    parser.add_argument('--tesseract', help='Path to tesseract.exe')
    parser.add_argument('--tessdata', help='Path to tessdata directory')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode (save original and preprocessed images)')
    parser.add_argument('--debug-index', type=int, metavar='N', help='Debug only subtitle at index N (1-based)')
    
    args = parser.parse_args()
    
    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.splitext(args.input)[0] + '.srt'
    
    # Determine file type and convert
    ext = os.path.splitext(args.input)[1].lower()
    
    if ext == '.sup':
        success = convert_sup_to_srt(args.input, output_path, args.language,
                                     args.tesseract, args.tessdata,
                                     debug_mode=args.debug, debug_subtitle_index=args.debug_index)
    elif ext == '.sub':
        success = convert_sub_to_srt(args.input, output_path, args.language,
                                     args.tesseract, args.tessdata,
                                     debug_mode=args.debug, debug_subtitle_index=args.debug_index)
    else:
        print(f"ERROR: Unsupported file format: {ext}")
        print("Supported formats: .sup, .sub")
        return 1
    
    return 0 if success else 1


if __name__ == '__main__':
    exit(main())
