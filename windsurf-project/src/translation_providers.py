"""
Translation providers for subtitle translation.
Supports DeepL and Azure Translator with optional VPN support.
"""
import time
import logging
import os
import subprocess
from typing import List, Optional
from bs4 import BeautifulSoup
import deepl
from azure.ai.translation.text import TextTranslationClient
from azure.core.credentials import AzureKeyCredential


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


def translate_texts_deepl(texts: List[str], target_lang: str, api_key: str) -> List[str]:
    """
    Translate texts using DeepL API.
    
    Args:
        texts: List of text strings to translate
        target_lang: Target language code (e.g., 'TH', 'EN', 'DE')
        api_key: DeepL API key
        
    Returns:
        List of translated text strings
    """
    client = deepl.Translator(api_key)
    
    # Clean HTML tags from texts
    cleaned = [BeautifulSoup(t, "html.parser").get_text() for t in texts]
    
    # Translate with context
    responses = client.translate_text(
        cleaned,
        target_lang=target_lang.upper(),
        context="These are subtitles from a video file."
    )
    
    return [r.text for r in responses]


def translate_texts_azure(
    texts: List[str],
    target_lang: str,
    api_key: str,
    endpoint: str = "https://api.cognitive.microsofttranslator.com",
    region: str = "germanywestcentral",
    batch_size: int = 50,
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
    for i in range(0, len(cleaned), batch_size):
        batch = cleaned[i:i + batch_size]
        request_body = [{"text": t} for t in batch]
        
        success = False
        retry_count = 0
        max_retries = 3
        
        while not success and retry_count < max_retries:
            try:
                response = client.translate(
                    body=request_body,
                    to_language=[target_lang.lower()]
                )
                all_translations.extend([item.translations[0].text for item in response])
                success = True
            except Exception as e:
                if "(429" in str(e):
                    # Rate limit hit
                    logging.warning(f"Rate limit hit. Waiting 9 seconds... (retry {retry_count + 1}/{max_retries})")
                    time.sleep(9)
                    retry_count += 1
                else:
                    raise
        
        if not success:
            raise RuntimeError(f"Failed to translate batch after {max_retries} retries")
        
        # Sleep between batches (except after the last batch)
        if i + batch_size < len(cleaned):
            time.sleep(delay)
    
    return all_translations


def translate_srt_file(
    file_path: str,
    output_path: str,
    target_lang: str,
    provider: str,
    api_key: str,
    wait_ms: int = 1000,
    azure_endpoint: str = "https://api.cognitive.microsofttranslator.com",
    azure_region: str = "germanywestcentral"
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
                translations = translate_texts_deepl(text_blocks, target_lang, api_key)
            elif provider.lower() == "azure":
                translations = translate_texts_azure(
                    text_blocks,
                    target_lang,
                    api_key,
                    endpoint=azure_endpoint,
                    region=azure_region,
                    delay=delay_seconds
                )
            else:
                logging.error(f"Unsupported provider: {provider}")
                return False
            
            # Insert translations back into the structure
            for pos, translation in zip(block_positions, translations):
                translated_lines[pos] = translation + "\n"
                
        except Exception as e:
            logging.error(f"Translation failed: {e}")
            return False
    
    # Write translated file
    logging.info(f"Writing translated file: {output_path}")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(translated_lines)
        return True
    except Exception as e:
        logging.error(f"Failed to write output file: {e}")
        return False
