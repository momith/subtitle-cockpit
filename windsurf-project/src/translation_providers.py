"""
Translation providers for subtitle translation.
Supports DeepL and Azure Translator with optional VPN support.
"""
import time
import logging
import os
import subprocess
import json
import re
from typing import List, Optional
from bs4 import BeautifulSoup
import requests
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential


def _split_batches(texts: List[str], max_items: int = 50, max_chars: int = 0) -> List[List[str]]:
    max_items = max(1, int(max_items) if max_items is not None else 50)
    try:
        max_chars = int(max_chars or 0)
    except Exception:
        max_chars = 0
    if max_chars < 0:
        max_chars = 0

    batches: List[List[str]] = []
    current: List[str] = []
    current_chars = 0

    for t in texts:
        s = t if isinstance(t, str) else str(t)
        s_len = len(s)

        would_exceed_items = len(current) >= max_items
        would_exceed_chars = max_chars > 0 and current and (current_chars + s_len) > max_chars

        if would_exceed_items or would_exceed_chars:
            batches.append(current)
            current = []
            current_chars = 0

        if max_chars > 0 and not current and s_len > max_chars:
            # Single oversized item: send it alone.
            batches.append([s])
            continue

        current.append(s)
        current_chars += s_len

    if current:
        batches.append(current)

    return batches


def start_vpn(vpn_config_path: str) -> bool:
    """
    Start Mullvad VPN using WireGuard.
    
    Args:
        vpn_config_path: Path to the WireGuard configuration file
        
    Returns:
        True if VPN started successfully, False otherwise
    """
    logging.info("Starting Mullvad VPN (WireGuard)...")
    
    if not os.path.exists(vpn_config_path):
        logging.error(f"VPN config not found at {vpn_config_path}")
        return False
    
    # Copy config to WireGuard directory
    wg_config = "/etc/wireguard/mullvad.conf"
    
    try:
        os.makedirs("/etc/wireguard", exist_ok=True)
        subprocess.run(["cp", vpn_config_path, wg_config], check=True)
        subprocess.run(["wg-quick", "up", wg_config], check=True)
        
        logging.info("VPN connected. Verifying IP...")
        result = subprocess.run(
            ["curl", "-s", "https://am.i.mullvad.net/ip"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            logging.info(f"VPN IP: {result.stdout.strip()}")
        else:
            logging.warning("Could not verify VPN IP")
        
        return True
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to start VPN: {e}")
        return False
    except Exception as e:
        logging.error(f"Error starting VPN: {e}")
        return False


def stop_vpn() -> bool:
    """
    Stop Mullvad VPN.
    
    Returns:
        True if VPN stopped successfully, False otherwise
    """
    wg_config = "/etc/wireguard/mullvad.conf"
    
    try:
        if os.path.exists(wg_config):
            logging.info("Stopping VPN...")
            subprocess.run(["wg-quick", "down", wg_config], check=True)
            logging.info("VPN stopped")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to stop VPN: {e}")
        return False
    except Exception as e:
        logging.error(f"Error stopping VPN: {e}")
        return False


def translate_texts_deepl(
    texts: List[str],
    target_lang: str,
    api_key: str,
    endpoint: str = "https://api-free.deepl.com/v2/translate",
    context: str = "These are subtitles from a video file.",
    batch_size: int = 50,
    max_chars_per_request: int = 0,
    delay: float = 0.0,
    max_retries: int = 5,
) -> List[str]:
    """
    Translate texts using DeepL API.
    
    Args:
        texts: List of text strings to translate
        target_lang: Target language code (e.g., 'TH', 'EN', 'DE')
        api_key: DeepL API key
        
    Returns:
        List of translated text strings
    """
    # DeepL can handle HTML if tag_handling is set; don't strip tags.
    cleaned = [t if isinstance(t, str) else str(t) for t in texts]

    url = endpoint.strip()
    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key}",
        "Content-Type": "application/json",
    }

    def _post(payload_obj: dict) -> dict:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload_obj, ensure_ascii=False),
            timeout=(10, 120),
        )
        if not resp.ok:
            body_preview = (resp.text or "").strip().replace("\n", " ")[:500]
            raise RuntimeError(f"DeepL API returned HTTP {resp.status_code}. Response: {body_preview}")
        try:
            return resp.json()
        except Exception as e:
            body_preview = (resp.text or "").strip().replace("\n", " ")[:500]
            raise RuntimeError(f"DeepL returned non-JSON response: {e}. Response: {body_preview}") from e

    translations: list[str] = []
    batches = _split_batches(cleaned, max_items=batch_size, max_chars=max_chars_per_request)
    for bi, batch in enumerate(batches):
        payload_obj = {
            "text": batch,
            "target_lang": target_lang.upper(),
            "context": context,
            "tag_handling": "html",
            "split_sentences": "1",
            "preserve_formatting": False,
        }

        data = _post(payload_obj)
        items = data.get("translations")
        if not isinstance(items, list) or len(items) != len(batch):
            raise ValueError(f"DeepL returned unexpected response shape: {data}")

        translations.extend([str(x.get("text", "")) for x in items])

        if bi + 1 < len(batches) and delay and delay > 0:
            time.sleep(delay)

    return translations


def translate_texts_azure(
    texts: List[str],
    target_lang: str,
    api_key: str,
    endpoint: str = "https://api.cognitive.microsofttranslator.com",
    region: str = "germanywestcentral",
    batch_size: int = 50,
    max_chars_per_request: int = 0,
    delay: float = 1.0
) -> List[str]:
    """
    Translate texts using Azure Translator API.
    
    Args:
        texts: List of text strings to translate
        target_lang: Target language code (e.g., 'th', 'en', 'de')
        api_key: Azure Translator API key
        endpoint: Azure Translator endpoint URL
        region: Azure region
        batch_size: Number of texts per batch
        delay: Delay between batches in seconds
        
    Returns:
        List of translated text strings
    """
    client = TextTranslationClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(api_key),
        region=region
    )
    
    # Clean HTML tags from texts
    cleaned = [BeautifulSoup(t, "html.parser").get_text() for t in texts]
    all_translations = []
    
    # Process in batches
    batches = _split_batches(cleaned, max_items=batch_size, max_chars=max_chars_per_request)
    for bi, batch in enumerate(batches):
        request_body = [{"text": t} for t in batch]
        
        response = client.translate(
            body=request_body,
            to_language=[target_lang.lower()]
        )
        all_translations.extend([item.translations[0].text for item in response])
        
        # Sleep between batches (except after the last batch)
        if bi + 1 < len(batches) and delay and delay > 0:
            time.sleep(delay)
    
    return all_translations


def translate_texts_gemini(
    texts: List[str],
    target_lang: str,
    api_key: str,
    model_name: str = "gemini-2.0-flash",
    batch_size: int = 50,
    max_chars_per_request: int = 0,
    delay: float = 1.0,
    max_retries: int = 6
) -> List[str]:
    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        raise ImportError(
            "Gemini provider requires the 'google-genai' package. "
            "Install it with: pip install -U google-genai"
        ) from e

    cleaned = [BeautifulSoup(t, "html.parser").get_text() for t in texts]

    def _clean_json_string(s: str) -> str:
        s2 = (s or "").strip()
        s2 = re.sub(r'^```(?:json)?\s*', '', s2, flags=re.IGNORECASE)
        s2 = re.sub(r'\s*```\s*$', '', s2)
        return s2.strip()

    def _extract_json_payload(s: str) -> str:
        s2 = _clean_json_string(s)
        start = s2.find('[')
        end = s2.rfind(']')
        if start != -1 and end != -1 and end > start:
            return s2[start:end + 1].strip()
        return s2

    def _escape_control_chars_in_json_strings(s: str) -> str:
        # Gemini sometimes returns invalid JSON by placing literal newlines/control chars
        # inside quoted strings. JSON requires these be escaped.
        out: list[str] = []
        in_string = False
        escaped = False
        for ch in s:
            if in_string:
                if escaped:
                    out.append(ch)
                    escaped = False
                    continue
                if ch == '\\':
                    out.append(ch)
                    escaped = True
                    continue
                if ch == '"':
                    out.append(ch)
                    in_string = False
                    continue
                if ch == '\n':
                    out.append('\\n')
                    continue
                if ch == '\r':
                    out.append('\\r')
                    continue
                if ch == '\t':
                    out.append('\\t')
                    continue
                o = ord(ch)
                if o < 0x20:
                    out.append(f"\\u{o:04x}")
                    continue
                out.append(ch)
                continue

            # not in string
            out.append(ch)
            if ch == '"':
                in_string = True
                escaped = False

        return ''.join(out)

    def _normalize_translated_payload(payload, expected_len: int) -> list[str]:
        if isinstance(payload, dict):
            for k in ("translations", "items", "data", "result", "output"):
                if isinstance(payload.get(k), list):
                    payload = payload.get(k)
                    break

        if not isinstance(payload, list):
            raise ValueError("Gemini returned non-list JSON")

        idx_to_texts: dict[int, list[str]] = {}
        for pos, item in enumerate(payload):
            if isinstance(item, str):
                idx = pos
                idx_to_texts.setdefault(idx, []).append(item)
                continue

            if not isinstance(item, dict):
                raise ValueError("Gemini returned invalid item structure")

            raw_idx = item.get("index")
            idx = pos
            if raw_idx is not None:
                try:
                    idx = int(str(raw_idx))
                except Exception:
                    idx = pos

            content = item.get("content")
            if content is None:
                content = item.get("text")
            if content is None:
                content = item.get("translation")
            if content is None and len(item) == 1:
                content = next(iter(item.values()))
            if content is None:
                raise ValueError("Gemini returned invalid item structure")

            idx_to_texts.setdefault(idx, []).append(str(content))

        ordered: list[str] = []
        for i in range(expected_len):
            parts = idx_to_texts.get(i)
            if not parts:
                raise ValueError("Gemini returned unexpected indices")
            merged = "\n".join([p for p in parts if p is not None])
            ordered.append(merged)

        if len(ordered) != expected_len:
            raise ValueError("Gemini returned unexpected indices")

        return ordered

    system_text = (
        f"You are an assistant that translates subtitles to {target_lang}.\n"
        "You will receive a JSON list of objects with keys: index (string) and content (string).\n"
        "The 'index' key is the index of the subtitle dialog.\n"
        "The 'content' key is the dialog to be translated.\n"
        "The indices must remain the same in the response as in the request.\n"
        "Dialogs must be translated as they are without any changes.\n"
        "Do NOT create, remove, split, or merge items. Exactly one output item per input item.\n"
        "If you need line breaks, use the literal newline escape \\n inside the content string; do not create additional JSON items.\n"
        "Never output literal newlines inside JSON strings. All line breaks must be escaped as \\n.\n"
        "Return ONLY valid JSON, with the exact same number of items as the request. No markdown, no explanations."
    )

    response_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "index": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["index", "content"],
            "additionalProperties": False
        }
    }

    client = genai.Client(api_key=api_key)

    def _call_with_retries(contents: str) -> str:
        resp = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_text,
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=response_schema,
            ),
        )
        return resp.text or ""

    results: list[str] = []
    # If a max char budget is configured, batching should primarily follow it.
    # We intentionally do not also hard-cap by the default batch_size (50), otherwise
    # small total-char files would still be split into many requests.
    split_max_items = len(cleaned) if (max_chars_per_request and max_chars_per_request > 0) else batch_size
    batches = _split_batches(cleaned, max_items=split_max_items, max_chars=max_chars_per_request)
    for bi, batch in enumerate(batches):
        user_json = json.dumps(
            [{"index": str(j), "content": t} for j, t in enumerate(batch)],
            ensure_ascii=False,
        )

        text = _call_with_retries(user_json)
        json_text = _extract_json_payload(text)
        try:
            payload = json.loads(json_text)
        except Exception as e:
            try:
                fixed = _escape_control_chars_in_json_strings(json_text)
                payload = json.loads(fixed)
            except Exception:
                preview = (text or "").strip().replace("\n", " ")[:500]
                raise ValueError(f"Gemini returned non-JSON output: {e}. Output preview: {preview}")

        translated_texts = _normalize_translated_payload(payload, expected_len=len(batch))
        results.extend(translated_texts)

        if bi + 1 < len(batches) and delay and delay > 0:
            time.sleep(delay)

    return results


def translate_srt_file(
    file_path: str,
    output_path: str,
    target_lang: str,
    provider: str,
    api_key: str,
    wait_ms: int = 1000,
    max_chars_per_request: int = 0,
    deepl_endpoint: str = "https://api-free.deepl.com/v2/translate",
    azure_endpoint: str = "https://api.cognitive.microsofttranslator.com",
    azure_region: str = "germanywestcentral",
    gemini_model: str = "gemini-2.0-flash"
) -> bool:
    """
    Translate an SRT subtitle file.
    
    Args:
        file_path: Path to the input SRT file
        output_path: Path to the output translated SRT file
        target_lang: Target language code (e.g., 'th', 'en', 'de')
        provider: Translation provider ('deepl' or 'azure')
        api_key: API key for the provider
        wait_ms: Milliseconds to wait between requests (for Azure)
        azure_endpoint: Azure Translator endpoint URL
        azure_region: Azure Translator region
        
    Returns:
        True if successful, False otherwise
    """
    logging.info(f"Reading SRT file: {file_path}")
    
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        logging.error(f"Failed to read file: {e}")
        return False
    
    # Parse SRT structure
    translated_lines = []
    buffer = []
    text_blocks = []
    block_positions = []
    
    for line in lines:
        # Check if line is a subtitle number, timestamp, or empty line
        if line.strip() == "" or line.strip().isdigit() or "-->" in line:
            # If we have buffered text, save it as a block to translate
            if buffer:
                joined_text = " ".join(buffer)
                text_blocks.append(joined_text)
                block_positions.append(len(translated_lines))
                translated_lines.append(None)  # Placeholder for translation
                buffer = []
            translated_lines.append(line)
        else:
            # This is subtitle text - add to buffer
            buffer.append(line.strip())
    
    # Handle any remaining buffer
    if buffer:
        joined_text = " ".join(buffer)
        text_blocks.append(joined_text)
        block_positions.append(len(translated_lines))
        translated_lines.append(None)
    
    # Translate text blocks
    if text_blocks:
        logging.info(f"Translating {len(text_blocks)} text blocks using {provider}...")
        
        try:
            delay_seconds = wait_ms / 1000.0
            
            if provider.lower() == "deepl":
                translations = translate_texts_deepl(
                    text_blocks,
                    target_lang,
                    api_key,
                    endpoint=deepl_endpoint,
                    delay=delay_seconds,
                    max_chars_per_request=max_chars_per_request,
                )
            elif provider.lower() == "azure":
                translations = translate_texts_azure(
                    text_blocks,
                    target_lang,
                    api_key,
                    endpoint=azure_endpoint,
                    region=azure_region,
                    delay=delay_seconds,
                    max_chars_per_request=max_chars_per_request,
                )
            elif provider.lower() == "gemini":
                translations = translate_texts_gemini(
                    text_blocks,
                    target_lang,
                    api_key,
                    model_name=gemini_model,
                    delay=delay_seconds,
                    max_chars_per_request=max_chars_per_request,
                )
            else:
                logging.error(f"Unsupported provider: {provider}")
                return False
            
            # Insert translations back into the structure
            for pos, translation in zip(block_positions, translations):
                translated_lines[pos] = translation + "\n"
                
        except Exception as e:
            logging.error(f"Translation failed: {e}")
            raise
    
    # Write translated file
    logging.info(f"Writing translated file: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(translated_lines)
        return True
    except Exception as e:
        logging.error(f"Failed to write output file: {e}")
        return False
