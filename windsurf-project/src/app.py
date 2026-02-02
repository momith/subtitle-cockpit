from flask import Flask, render_template, jsonify, request
import os
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import threading
import time
import json
import subprocess
import tempfile
from datetime import timedelta
from PIL import Image
import pytesseract
import logging
from job_queue import get_job_queue, JOB_TYPE_SUP_TO_SRT, JOB_TYPE_TRANSLATE, JOB_TYPE_EXTRACT, JOB_TYPE_SEARCH_SUBTITLES

logging.basicConfig(level=logging.INFO)

# Get the parent directory of src/ where templates/ and static/ are located
parent_dir = os.path.dirname(os.path.dirname(__file__))
app = Flask(__name__, 
           template_folder=os.path.join(parent_dir, 'templates'),
           static_folder=os.path.join(parent_dir, 'static'))

# Initialize job queue
job_queue = None

# Configuration
# Use /media as default in Docker, fall back to user's home directory
BASE_DIR = os.environ.get('DEFAULT_MEDIA_DIR', 'D:\\')
if not os.path.exists(BASE_DIR):
    BASE_DIR = os.path.expanduser('~')
app.config['BASE_DIR'] = BASE_DIR
SETTINGS_FILE = os.path.join(parent_dir, 'settings.json')
ALLOWED_PROVIDERS = ["DeepL", "Azure", "Gemini"]
OBSERVER = None
OBSERVED_PATH = None

class FileChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        # This will be triggered on any file system change
        pass

# Start file system observer
def start_observer(path):
    global OBSERVER, OBSERVED_PATH
    # Stop existing observer
    if OBSERVER is not None:
        try:
            OBSERVER.stop()
            OBSERVER.join(timeout=2)
        except Exception:
            pass
        OBSERVER = None
        OBSERVED_PATH = None
    # Start new observer for given path if exists
    if path and os.path.isdir(path):
        event_handler = FileChangeHandler()
        observer = Observer()
        observer.schedule(event_handler, path, recursive=True)
        observer.start()
        OBSERVER = observer
        OBSERVED_PATH = path
    return OBSERVER

def init_job_queue():
    """Initialize job queue with settings from config"""
    global job_queue
    settings = read_settings()
    max_parallel = settings.get('max_parallel_jobs', 1)
    job_queue = get_job_queue(max_parallel=max_parallel)
    return job_queue

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/jobs')
def jobs_page():
    """Jobs queue management page"""
    return render_template('jobs.html')

# Job Queue API Endpoints

@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    """Get all jobs in queue"""
    if not job_queue:
        init_job_queue()
    
    pending = job_queue.get_pending_jobs(limit=50)
    running = job_queue.get_running_jobs()
    failed = job_queue.get_recent_failed_jobs(hours=1)
    
    return jsonify({
        'pending': pending,
        'running': running,
        'failed': failed
    })

@app.route('/api/jobs/<int:job_id>', methods=['DELETE'])
def delete_job(job_id):
    """Delete a pending job"""
    if not job_queue:
        init_job_queue()
    
    success = job_queue.delete_job(job_id)
    if success:
        return jsonify({'ok': True, 'message': 'Job deleted'})
    else:
        return jsonify({'ok': False, 'error': 'Job not found or cannot be deleted'}), 404

@app.route('/settings')
def settings_page():
    return render_template('settings.html')

@app.route('/api/list')
def list_files():
    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR') or os.path.expanduser('~')
    path = request.args.get('path', '')
    full_path = os.path.join(base_dir, path)
    
    if not os.path.exists(full_path):
        return jsonify({'error': 'Path does not exist'}), 404
    
    items = []
    try:
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            is_dir = os.path.isdir(item_path)
            items.append({
                'name': item,
                'is_dir': is_dir,
                'path': os.path.join(path, item).replace('\\', '/'),
                'size': os.path.getsize(item_path) if not is_dir else 0
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
    return jsonify({
        'path': path,
        'parent': os.path.dirname(path).replace('\\', '/'),
        'items': sorted(items, key=lambda x: (not x['is_dir'], x['name'].lower()))
    })

def _default_settings():
    return {
        'root_dir': app.config.get('BASE_DIR') or os.path.expanduser('~'),
        'mullvad_vpn_config_dir': '',
        'excluded_file_types': '',
        'max_parallel_jobs': 1,
        'subtitle_max_downloads': 1,
        'provider': 'DeepL',
        'translation_target_language': '',
        'ocr_source_language': 'eng',
        'extraction_source_language': 'eng',
        'gemini_model': 'gemini-2.0-flash',
        'deepl_endpoint': 'https://api-free.deepl.com/v2/translate',
        # Subtitle search defaults
        'subtitle_search_languages': ['en'],
        'subtitle_providers': {
            'opensubtitles': {
                'enabled': False,
                'username': '',
                'password': ''
            },
            'addic7ed': {
                'enabled': False,
                'username': '',
                'password': ''
            }
        },
        'auto_switch_on_error': False,
        'azure_endpoint': 'https://api.cognitive.microsofttranslator.com',
        'azure_region': 'eastus',
        'wait_ms': {p: 0 for p in ALLOWED_PROVIDERS},
        'max_chars_per_request': {p: 0 for p in ALLOWED_PROVIDERS},
        'retry_after_days': {'DeepL': 0, 'Azure': 0, 'Gemini': 0},
        'auto_change_key_on_error': {'DeepL': False, 'Azure': False, 'Gemini': False},
        'provider_keys': {
            'DeepL': [],
            'Azure': [],
            'Gemini': []
        }
    }

def _normalize_keys_list(raw_list):
    # Accept list[str] or list[dict]
    normalized = []
    if isinstance(raw_list, list):
        for item in raw_list:
            if isinstance(item, dict):
                val = str(item.get('value', '')).strip()
                if not val:
                    continue
                normalized.append({
                    'value': val,
                    'active': bool(item.get('active', False)),
                    'last_usage': item.get('last_usage') or None,
                    'last_error': item.get('last_error') or None,
                    'last_error_at': item.get('last_error_at') or None,
                    'vpn_config': item.get('vpn_config') or None
                })
            else:
                val = str(item).strip()
                if not val:
                    continue
                normalized.append({
                    'value': val,
                    'active': False,
                    'last_usage': None,
                    'last_error': None,
                    'last_error_at': None,
                    'vpn_config': None
                })
    # Enforce single active
    active_set = False
    for k in normalized:
        if k.get('active') and not active_set:
            active_set = True
        else:
            k['active'] = False
    # If none active but keys exist, set first as active
    if normalized and not any(k.get('active') for k in normalized):
        normalized[0]['active'] = True
    return normalized

def read_settings():
    if not os.path.exists(SETTINGS_FILE):
        return _default_settings()
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Base defaults
            base = _default_settings()
            # Root dir
            root_dir = data.get('root_dir') or base['root_dir']
            # Mullvad VPN config directory
            mullvad_vpn_config_dir = str(data.get('mullvad_vpn_config_dir', ''))
            # Azure-specific settings
            azure_endpoint = str(data.get('azure_endpoint', base['azure_endpoint']))
            azure_region = str(data.get('azure_region', base['azure_region']))
            # DeepL-specific settings
            deepl_endpoint = str(data.get('deepl_endpoint', base.get('deepl_endpoint', 'https://api-free.deepl.com/v2/translate')))
            # Provider
            provider = data.get('provider') if data.get('provider') in ALLOWED_PROVIDERS else base['provider']
            # Languages (migrate old key target_language -> translation_target_language)
            translation_target_language = str(data.get('translation_target_language') or data.get('target_language') or base['translation_target_language'])
            # Gemini-specific settings
            gemini_model = str(data.get('gemini_model', base.get('gemini_model', 'gemini-2.0-flash')))
            # OCR source language
            ocr_source_language = str(data.get('ocr_source_language', base.get('ocr_source_language', 'eng')))
            # Extraction source language
            extraction_source_language = str(data.get('extraction_source_language', base.get('extraction_source_language', 'eng')))
            # Subtitle search languages
            raw_sub_langs = data.get('subtitle_search_languages', base.get('subtitle_search_languages', ['en']))
            if isinstance(raw_sub_langs, list):
                subtitle_search_languages = [str(x).strip() for x in raw_sub_langs if str(x).strip()]
            elif isinstance(raw_sub_langs, str):
                subtitle_search_languages = [s.strip() for s in raw_sub_langs.split(',') if s.strip()]
            else:
                subtitle_search_languages = base.get('subtitle_search_languages', ['en'])
            if not subtitle_search_languages:
                subtitle_search_languages = ['en']
            # Subtitle providers
            base_providers = base.get('subtitle_providers', {})
            data_providers = data.get('subtitle_providers', {}) or {}
            subtitle_providers = {
                'opensubtitles': {
                    'enabled': bool(data_providers.get('opensubtitles', {}).get('enabled', base_providers.get('opensubtitles', {}).get('enabled', False))),
                    'username': str(data_providers.get('opensubtitles', {}).get('username', base_providers.get('opensubtitles', {}).get('username', ''))),
                    'password': str(data_providers.get('opensubtitles', {}).get('password', base_providers.get('opensubtitles', {}).get('password', '')))
                },
                'addic7ed': {
                    'enabled': bool(data_providers.get('addic7ed', {}).get('enabled', base_providers.get('addic7ed', {}).get('enabled', False))),
                    'username': str(data_providers.get('addic7ed', {}).get('username', base_providers.get('addic7ed', {}).get('username', ''))),
                    'password': str(data_providers.get('addic7ed', {}).get('password', base_providers.get('addic7ed', {}).get('password', '')))
                }
            }
            # Auto switch
            auto_switch = bool(data.get('auto_switch_on_error', base['auto_switch_on_error']))
            # wait_ms
            wait_ms = base['wait_ms']
            if isinstance(data.get('wait_ms'), dict):
                for p in ALLOWED_PROVIDERS:
                    try:
                        wait_ms[p] = int(data['wait_ms'].get(p, 0))
                    except Exception:
                        wait_ms[p] = 0
            # max_chars_per_request
            max_chars_per_request = base.get('max_chars_per_request', {p: 0 for p in ALLOWED_PROVIDERS})
            if isinstance(data.get('max_chars_per_request'), dict):
                for p in ALLOWED_PROVIDERS:
                    try:
                        max_chars_per_request[p] = int(data['max_chars_per_request'].get(p, 0))
                    except Exception:
                        max_chars_per_request[p] = 0
            # retry_after_days
            retry_after_days = base['retry_after_days']
            if isinstance(data.get('retry_after_days'), dict):
                for p in ['DeepL', 'Azure', 'Gemini']:
                    try:
                        retry_after_days[p] = int(data['retry_after_days'].get(p, 0))
                    except Exception:
                        retry_after_days[p] = 0
            # auto_change_key_on_error
            auto_change_key_on_error = base['auto_change_key_on_error']
            if isinstance(data.get('auto_change_key_on_error'), dict):
                for p in ['DeepL', 'Azure', 'Gemini']:
                    auto_change_key_on_error[p] = bool(data['auto_change_key_on_error'].get(p, False))
            # provider_keys with migration
            pk = data.get('provider_keys') if isinstance(data.get('provider_keys'), dict) else {}
            deepL_keys = _normalize_keys_list(pk.get('DeepL', []))
            azure_keys = _normalize_keys_list(pk.get('Azure', []))
            gemini_keys = _normalize_keys_list(pk.get('Gemini', []))
            # New settings
            excluded_file_types = str(data.get('excluded_file_types', base.get('excluded_file_types', '')))
            max_parallel_jobs = int(data.get('max_parallel_jobs', base.get('max_parallel_jobs', 1)))
            subtitle_max_downloads = int(data.get('subtitle_max_downloads', base.get('subtitle_max_downloads', 1)))
            return {
                'root_dir': root_dir,
                'mullvad_vpn_config_dir': mullvad_vpn_config_dir,
                'excluded_file_types': excluded_file_types,
                'max_parallel_jobs': max_parallel_jobs,
                'subtitle_max_downloads': subtitle_max_downloads,
                'provider': provider,
                'translation_target_language': translation_target_language,
                'ocr_source_language': ocr_source_language,
                'extraction_source_language': extraction_source_language,
                'gemini_model': gemini_model,
                'deepl_endpoint': deepl_endpoint,
                'subtitle_search_languages': subtitle_search_languages,
                'subtitle_providers': subtitle_providers,
                'auto_switch_on_error': auto_switch,
                'azure_endpoint': azure_endpoint,
                'azure_region': azure_region,
                'wait_ms': wait_ms,
                'max_chars_per_request': max_chars_per_request,
                'retry_after_days': retry_after_days,
                'auto_change_key_on_error': auto_change_key_on_error,
                'provider_keys': {
                    'DeepL': deepL_keys,
                    'Azure': azure_keys,
                    'Gemini': gemini_keys
                }
            }
    except Exception:
        return _default_settings()

def write_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        current = read_settings()
        return jsonify({
            'settings': current,
            'allowed_providers': ALLOWED_PROVIDERS
        })
    payload = request.get_json(silent=True) or {}
    provider = payload.get('provider')
    provider_keys = payload.get('provider_keys', {})
    if provider not in ALLOWED_PROVIDERS:
        return jsonify({'error': 'Invalid provider'}), 400
    # Merge with existing settings so unspecified providers keep their settings
    existing = read_settings()
    # Normalize keys payload (list of dicts or strings)
    keys_data = existing.get('provider_keys', {'DeepL': [], 'Azure': [], 'Gemini': []})
    if not isinstance(keys_data, dict):
        keys_data = {'DeepL': [], 'Azure': [], 'Gemini': []}
    if isinstance(provider_keys, dict):
        for k in ['DeepL', 'Azure', 'Gemini']:
            v = provider_keys.get(k, None)
            if v is not None:
                keys_data[k] = _normalize_keys_list(v)

    # Global and per-provider options
    auto_switch_on_error = bool(payload.get('auto_switch_on_error', existing.get('auto_switch_on_error', False)))
    wait_ms_payload = payload.get('wait_ms', {})
    wait_ms = existing.get('wait_ms', {p: 0 for p in ALLOWED_PROVIDERS})
    if isinstance(wait_ms_payload, dict):
        for p in ALLOWED_PROVIDERS:
            try:
                if p in wait_ms_payload:
                    wait_ms[p] = int(wait_ms_payload[p])
            except Exception:
                pass

    max_chars_payload = payload.get('max_chars_per_request', {})
    max_chars_per_request = existing.get('max_chars_per_request', {p: 0 for p in ALLOWED_PROVIDERS})
    if isinstance(max_chars_payload, dict):
        for p in ALLOWED_PROVIDERS:
            try:
                if p in max_chars_payload:
                    max_chars_per_request[p] = int(max_chars_payload[p])
            except Exception:
                pass
    for p in ALLOWED_PROVIDERS:
        try:
            if int(max_chars_per_request.get(p, 0)) < 0:
                max_chars_per_request[p] = 0
        except Exception:
            max_chars_per_request[p] = 0
    retry_payload = payload.get('retry_after_days', {})
    retry_after_days = existing.get('retry_after_days', {'DeepL': 0, 'Azure': 0, 'Gemini': 0})
    if isinstance(retry_payload, dict):
        for p in ['DeepL', 'Azure', 'Gemini']:
            try:
                if p in retry_payload:
                    retry_after_days[p] = int(retry_payload[p])
            except Exception:
                pass
    auto_change_payload = payload.get('auto_change_key_on_error', {})
    auto_change_key_on_error = existing.get('auto_change_key_on_error', {'DeepL': False, 'Azure': False, 'Gemini': False})
    if isinstance(auto_change_payload, dict):
        for p in ['DeepL', 'Azure', 'Gemini']:
            if p in auto_change_payload:
                auto_change_key_on_error[p] = bool(auto_change_payload[p])

    # Root dir (allow client to update)
    root_dir = payload.get('root_dir', existing.get('root_dir'))
    if not isinstance(root_dir, str) or not root_dir:
        root_dir = existing.get('root_dir')
    
    # Mullvad VPN config directory
    mullvad_vpn_config_dir = str(payload.get('mullvad_vpn_config_dir', existing.get('mullvad_vpn_config_dir', '')))
    
    # Azure-specific settings
    azure_endpoint = str(payload.get('azure_endpoint', existing.get('azure_endpoint', 'https://api.cognitive.microsofttranslator.com')))
    azure_region = str(payload.get('azure_region', existing.get('azure_region', 'eastus')))

    # DeepL-specific settings
    deepl_endpoint = str(payload.get('deepl_endpoint', existing.get('deepl_endpoint', _default_settings().get('deepl_endpoint', 'https://api-free.deepl.com/v2/translate'))))
    if not deepl_endpoint:
        deepl_endpoint = existing.get('deepl_endpoint', _default_settings().get('deepl_endpoint', 'https://api-free.deepl.com/v2/translate'))

    # Languages
    translation_target_language = str(payload.get('translation_target_language', existing.get('translation_target_language', existing.get('target_language', ''))))
    ocr_source_language = str(payload.get('ocr_source_language', existing.get('ocr_source_language', 'eng')))
    extraction_source_language = str(payload.get('extraction_source_language', existing.get('extraction_source_language', 'eng')))
    gemini_model = str(payload.get('gemini_model', existing.get('gemini_model', _default_settings().get('gemini_model', 'gemini-2.0-flash'))))
    # Subtitle search languages (accept list or comma-separated string)
    sub_langs_payload = payload.get('subtitle_search_languages', existing.get('subtitle_search_languages', ['en']))
    subtitle_search_languages: list[str]
    if isinstance(sub_langs_payload, list):
        subtitle_search_languages = [str(x).strip() for x in sub_langs_payload if str(x).strip()]
    elif isinstance(sub_langs_payload, str):
        subtitle_search_languages = [s.strip() for s in sub_langs_payload.split(',') if s.strip()]
    else:
        subtitle_search_languages = existing.get('subtitle_search_languages', ['en'])
    if not subtitle_search_languages:
        subtitle_search_languages = ['en']

    # Subtitle providers
    existing_providers = existing.get('subtitle_providers', _default_settings().get('subtitle_providers', {}))
    payload_providers = payload.get('subtitle_providers') or {}
    subtitle_providers = {
        'opensubtitles': {
            'enabled': bool((payload_providers.get('opensubtitles') or {}).get('enabled', (existing_providers.get('opensubtitles') or {}).get('enabled', False))),
            'username': str((payload_providers.get('opensubtitles') or {}).get('username', (existing_providers.get('opensubtitles') or {}).get('username', ''))),
            'password': str((payload_providers.get('opensubtitles') or {}).get('password', (existing_providers.get('opensubtitles') or {}).get('password', '')))
        },
        'addic7ed': {
            'enabled': bool((payload_providers.get('addic7ed') or {}).get('enabled', (existing_providers.get('addic7ed') or {}).get('enabled', False))),
            'username': str((payload_providers.get('addic7ed') or {}).get('username', (existing_providers.get('addic7ed') or {}).get('username', ''))),
            'password': str((payload_providers.get('addic7ed') or {}).get('password', (existing_providers.get('addic7ed') or {}).get('password', '')))
        }
    }
    
    # File exclusions and job settings
    excluded_file_types = str(payload.get('excluded_file_types', existing.get('excluded_file_types', '')))
    max_parallel_jobs = int(payload.get('max_parallel_jobs', existing.get('max_parallel_jobs', 1)))
    if max_parallel_jobs < 1:
        max_parallel_jobs = 1
    elif max_parallel_jobs > 10:
        max_parallel_jobs = 10
    subtitle_max_downloads = int(payload.get('subtitle_max_downloads', existing.get('subtitle_max_downloads', 1)))
    if subtitle_max_downloads < 1:
        subtitle_max_downloads = 1

    new_settings = {
        'root_dir': root_dir,
        'mullvad_vpn_config_dir': mullvad_vpn_config_dir,
        'excluded_file_types': excluded_file_types,
        'max_parallel_jobs': max_parallel_jobs,
        'subtitle_max_downloads': subtitle_max_downloads,
        'provider': provider,
        'translation_target_language': translation_target_language,
        'ocr_source_language': ocr_source_language,
        'extraction_source_language': extraction_source_language,
        'gemini_model': gemini_model,
        'deepl_endpoint': deepl_endpoint,
        'subtitle_search_languages': subtitle_search_languages,
        'subtitle_providers': subtitle_providers,
        'auto_switch_on_error': auto_switch_on_error,
        'azure_endpoint': azure_endpoint,
        'azure_region': azure_region,
        'wait_ms': wait_ms,
        'max_chars_per_request': max_chars_per_request,
        'retry_after_days': retry_after_days,
        'auto_change_key_on_error': auto_change_key_on_error,
        'provider_keys': keys_data
    }
    write_settings(new_settings)
    
    # Update job queue max_parallel if it changed
    if job_queue and job_queue.max_parallel != max_parallel_jobs:
        job_queue.max_parallel = max_parallel_jobs
        logging.info(f'Updated job queue max_parallel to {max_parallel_jobs}')
    
    # Restart observer if root changed
    try:
        if root_dir and root_dir != OBSERVED_PATH:
            start_observer(root_dir)
    except Exception:
        pass
    return jsonify({'ok': True, 'settings': new_settings})

@app.route('/api/vpn_configs', methods=['GET'])
def list_vpn_configs():
    """List available VPN config files and their assignment status"""
    settings = read_settings()
    vpn_dir = settings.get('mullvad_vpn_config_dir', '')
    if not vpn_dir or not os.path.isdir(vpn_dir):
        return jsonify({'configs': [], 'assigned': {}})
    
    # Get all .conf files
    configs = []
    try:
        for fname in os.listdir(vpn_dir):
            if fname.lower().endswith('.conf'):
                configs.append(fname)
    except Exception as e:
        logging.error(f'Error listing VPN configs: {e}')
        return jsonify({'error': str(e)}), 500
    
    # Build map of which configs are assigned
    assigned = {}
    provider_keys = settings.get('provider_keys', {})
    for provider in ['DeepL', 'Azure', 'Gemini']:
        keys = provider_keys.get(provider, [])
        if isinstance(keys, list):
            for key_obj in keys:
                if isinstance(key_obj, dict):
                    vpn_cfg = key_obj.get('vpn_config')
                    if vpn_cfg:
                        key_val = key_obj.get('value', '')
                        assigned[vpn_cfg] = {'provider': provider, 'key': key_val}
    
    return jsonify({'configs': sorted(configs), 'assigned': assigned})

@app.route('/api/settings/export', methods=['GET'])
def export_settings():
    """Export settings.json for download"""
    from flask import send_file
    try:
        if not os.path.exists(SETTINGS_FILE):
            return jsonify({'error': 'Settings file not found'}), 404
        return send_file(
            SETTINGS_FILE,
            mimetype='application/json',
            as_attachment=True,
            download_name='settings.json'
        )
    except Exception as e:
        logging.exception(f'Error exporting settings: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings/import', methods=['POST'])
def import_settings():
    """Import settings.json from upload"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.json'):
            return jsonify({'error': 'File must be a JSON file'}), 400
        
        # Read and validate JSON
        try:
            content = file.read().decode('utf-8')
            settings_data = json.loads(content)
        except json.JSONDecodeError as e:
            return jsonify({'error': f'Invalid JSON format: {str(e)}'}), 400
        except Exception as e:
            return jsonify({'error': f'Failed to read file: {str(e)}'}), 400
        
        # Validate it's a dict
        if not isinstance(settings_data, dict):
            return jsonify({'error': 'Settings must be a JSON object'}), 400
        
        # Backup current settings
        if os.path.exists(SETTINGS_FILE):
            backup_path = SETTINGS_FILE + '.backup'
            try:
                import shutil
                shutil.copy2(SETTINGS_FILE, backup_path)
                logging.info(f'Created settings backup at {backup_path}')
            except Exception as e:
                logging.warning(f'Failed to create backup: {e}')
        
        # Write new settings
        write_settings(settings_data)
        
        # Update job queue max_parallel if changed
        if job_queue:
            max_parallel = settings_data.get('max_parallel_jobs', 2)
            if job_queue.max_parallel != max_parallel:
                job_queue.max_parallel = max_parallel
                logging.info(f'Updated job queue max_parallel to {max_parallel}')
        
        # Restart observer if root changed
        try:
            root_dir = settings_data.get('root_dir')
            if root_dir and root_dir != OBSERVED_PATH:
                start_observer(root_dir)
        except Exception as e:
            logging.warning(f'Failed to restart observer: {e}')
        
        return jsonify({
            'ok': True,
            'message': 'Settings imported successfully',
            'settings': settings_data
        })
        
    except Exception as e:
        logging.exception(f'Error importing settings: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download_file():
    """Download a file from the file explorer"""
    from flask import send_file
    try:
        data = request.get_json(silent=True) or {}
        rel_path = data.get('path', '').strip()
        
        if not rel_path:
            return jsonify({'error': 'No file path provided'}), 400
        
        settings = read_settings()
        base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
        if not base_dir:
            return jsonify({'error': 'Base directory not configured'}), 400
        
        # Normalize and ensure path stays within base_dir
        target_path = os.path.normpath(os.path.join(base_dir, rel_path))
        if not target_path.startswith(os.path.normpath(base_dir)):
            return jsonify({'error': 'Path outside base directory'}), 403
        
        if not os.path.exists(target_path):
            return jsonify({'error': 'File not found'}), 404
        
        if os.path.isdir(target_path):
            return jsonify({'error': 'Cannot download directories'}), 400

        download_name = os.path.basename(target_path)
        ext = os.path.splitext(download_name)[1].lower()
        mimetype = None
        if ext in {'.srt', '.ass', '.ssa'}:
            mimetype = 'text/plain'

        # Send file for download
        return send_file(
            target_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype,
            max_age=0,
        )
        
    except Exception as e:
        logging.exception(f'Error downloading file: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload a file to the file explorer"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Get target directory from form data
        target_dir = request.form.get('path', '').strip()
        
        settings = read_settings()
        base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
        if not base_dir:
            return jsonify({'error': 'Base directory not configured'}), 400
        
        # Normalize and ensure path stays within base_dir
        full_target_dir = os.path.normpath(os.path.join(base_dir, target_dir))
        if not full_target_dir.startswith(os.path.normpath(base_dir)):
            return jsonify({'error': 'Path outside base directory'}), 403
        
        if not os.path.exists(full_target_dir):
            return jsonify({'error': 'Target directory does not exist'}), 404
        
        if not os.path.isdir(full_target_dir):
            return jsonify({'error': 'Target path is not a directory'}), 400
        
        # Secure filename
        from werkzeug.utils import secure_filename
        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({'error': 'Invalid filename'}), 400
        
        target_path = os.path.join(full_target_dir, filename)
        
        # Check if file already exists
        if os.path.exists(target_path):
            return jsonify({'error': f'File "{filename}" already exists in this directory'}), 409

        tmp_target_path = target_path + '.uploading'
        if os.path.exists(tmp_target_path):
            return jsonify({'error': f'Upload already in progress for "{filename}"'}), 409

        # Save file (streamed) with progress logging
        start_time = time.time()
        last_log_time = start_time
        bytes_written = 0
        chunk_size = 8 * 1024 * 1024  # 8 MiB

        total_size = None
        try:
            total_size = int(getattr(file, 'content_length', None) or 0) or None
        except Exception:
            total_size = None

        if not total_size:
            try:
                total_size = int(getattr(request, 'content_length', None) or 0) or None
            except Exception:
                total_size = None

        logging.info(
            f'Upload started: filename={filename}, target_path={target_path}, total_size={total_size}, from={request.remote_addr}'
        )

        try:
            with open(tmp_target_path, 'wb') as out:
                while True:
                    chunk = file.stream.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    bytes_written += len(chunk)

                    now = time.time()
                    if now - last_log_time >= 2:
                        elapsed = max(now - start_time, 0.001)
                        speed_mbps = (bytes_written / elapsed) / (1024 * 1024)
                        if total_size:
                            pct = (bytes_written / total_size) * 100
                            logging.info(
                                f'Upload progress: filename={filename}, bytes={bytes_written}/{total_size} ({pct:.1f}%), speed={speed_mbps:.1f} MiB/s'
                            )
                        else:
                            logging.info(
                                f'Upload progress: filename={filename}, bytes={bytes_written}, speed={speed_mbps:.1f} MiB/s'
                            )
                        last_log_time = now

            os.replace(tmp_target_path, target_path)
            elapsed = max(time.time() - start_time, 0.001)
            speed_mbps = (bytes_written / elapsed) / (1024 * 1024)
            logging.info(
                f'Upload finished: filename={filename}, target_path={target_path}, bytes={bytes_written}, seconds={elapsed:.2f}, speed={speed_mbps:.1f} MiB/s'
            )

        except Exception:
            try:
                if os.path.exists(tmp_target_path):
                    os.remove(tmp_target_path)
            except Exception:
                pass
            raise
        
        # Return relative path for frontend
        rel_path = os.path.relpath(target_path, base_dir).replace('\\', '/')
        
        return jsonify({
            'ok': True,
            'message': f'File "{filename}" uploaded successfully',
            'path': rel_path,
            'filename': filename
        })
        
    except Exception as e:
        logging.exception(f'Error uploading file: {e}')
        return jsonify({'error': str(e)}), 500


@app.route('/api/upload_raw', methods=['POST'])
def upload_file_raw():
    """Upload a file via raw request body (streams directly, supports progress logging)"""
    try:
        # Get target directory from query param
        target_dir = (request.args.get('path', '') or '').strip()
        requested_filename = (request.args.get('filename', '') or '').strip()

        settings = read_settings()
        base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
        if not base_dir:
            return jsonify({'error': 'Base directory not configured'}), 400

        # Normalize and ensure path stays within base_dir
        full_target_dir = os.path.normpath(os.path.join(base_dir, target_dir))
        if not full_target_dir.startswith(os.path.normpath(base_dir)):
            return jsonify({'error': 'Path outside base directory'}), 403

        if not os.path.exists(full_target_dir):
            return jsonify({'error': 'Target directory does not exist'}), 404

        if not os.path.isdir(full_target_dir):
            return jsonify({'error': 'Target path is not a directory'}), 400

        from werkzeug.utils import secure_filename
        filename = secure_filename(requested_filename)
        if not filename:
            return jsonify({'error': 'Invalid filename'}), 400

        target_path = os.path.join(full_target_dir, filename)

        # Check if file already exists
        if os.path.exists(target_path):
            return jsonify({'error': f'File "{filename}" already exists in this directory'}), 409

        tmp_target_path = target_path + '.uploading'
        if os.path.exists(tmp_target_path):
            return jsonify({'error': f'Upload already in progress for "{filename}"'}), 409

        # Stream request body to disk with progress logging
        start_time = time.time()
        last_log_time = start_time
        bytes_written = 0
        chunk_size = 8 * 1024 * 1024  # 8 MiB

        total_size = None
        try:
            total_size = int(getattr(request, 'content_length', None) or 0) or None
        except Exception:
            total_size = None

        logging.info(
            f'Upload(raw) started: filename={filename}, target_path={target_path}, total_size={total_size}, from={request.remote_addr}'
        )

        try:
            with open(tmp_target_path, 'wb') as out:
                while True:
                    chunk = request.stream.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    bytes_written += len(chunk)

                    now = time.time()
                    if now - last_log_time >= 2:
                        elapsed = max(now - start_time, 0.001)
                        speed_mibs = (bytes_written / elapsed) / (1024 * 1024)
                        if total_size:
                            pct = (bytes_written / total_size) * 100
                            logging.info(
                                f'Upload(raw) progress: filename={filename}, bytes={bytes_written}/{total_size} ({pct:.1f}%), speed={speed_mibs:.1f} MiB/s'
                            )
                        else:
                            logging.info(
                                f'Upload(raw) progress: filename={filename}, bytes={bytes_written}, speed={speed_mibs:.1f} MiB/s'
                            )
                        last_log_time = now

            os.replace(tmp_target_path, target_path)
            elapsed = max(time.time() - start_time, 0.001)
            speed_mibs = (bytes_written / elapsed) / (1024 * 1024)
            logging.info(
                f'Upload(raw) finished: filename={filename}, target_path={target_path}, bytes={bytes_written}, seconds={elapsed:.2f}, speed={speed_mibs:.1f} MiB/s'
            )

        except Exception:
            try:
                if os.path.exists(tmp_target_path):
                    os.remove(tmp_target_path)
            except Exception:
                pass
            raise

        rel_path = os.path.relpath(target_path, base_dir).replace('\\', '/')
        return jsonify({
            'ok': True,
            'message': f'File "{filename}" uploaded successfully',
            'path': rel_path,
            'filename': filename
        })

    except Exception as e:
        logging.exception(f'Error uploading file (raw): {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete', methods=['POST'])
def delete_files():
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not isinstance(paths, list):
        return jsonify({'error': 'Invalid payload'}), 400

    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
    if not base_dir:
        return jsonify({'error': 'Base directory not configured'}), 400

    results = []
    for rel_path in paths:
        try:
            # Normalize and ensure path stays within base_dir
            target_path = os.path.normpath(os.path.join(base_dir, rel_path))
            logging.info(f'Delete request: base_dir={base_dir}, rel_path={rel_path}, target_path={target_path}')
            if not target_path.startswith(os.path.normpath(base_dir)):
                logging.error(f'Path outside base directory: {target_path}')
                results.append({'path': rel_path, 'status': 'error', 'message': 'Path outside base directory'})
                continue
            if not os.path.exists(target_path):
                logging.error(f'File not found: {target_path}')
                results.append({'path': rel_path, 'status': 'error', 'message': 'Not found'})
                continue
            if os.path.isdir(target_path):
                logging.info(f'Skipping directory: {target_path}')
                results.append({'path': rel_path, 'status': 'skipped', 'message': 'Directories are not deleted'})
                continue
            logging.info(f'Attempting to delete: {target_path}')
            os.remove(target_path)
            logging.info(f'Successfully deleted: {target_path}')
            results.append({'path': rel_path, 'status': 'deleted'})
        except Exception as e:
            logging.exception(f'Delete error for {rel_path}: {e}')
            results.append({'path': rel_path, 'status': 'error', 'message': str(e)})

    return jsonify({'results': results})

def _get_host_path(container_path):
    """Convert container path to host path for Docker volume mounts"""
    # Get the host media directory from environment
    host_media_dir = os.environ.get('HOST_MEDIA_DIR', '/media')
    container_media_dir = os.environ.get('DEFAULT_MEDIA_DIR', '/media')
    
    # If container path starts with container_media_dir, replace with host path
    if container_path.startswith(container_media_dir):
        relative = os.path.relpath(container_path, container_media_dir)
        if relative == '.':
            return host_media_dir
        return os.path.join(host_media_dir, relative)
    return container_path

@app.route('/api/sup_to_srt', methods=['POST'])
def sup_to_srt():
    """Add SUP/SUB to SRT conversion jobs to queue"""
    if not job_queue:
        init_job_queue()
    
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not isinstance(paths, list) or len(paths) == 0:
        return jsonify({'error': 'No files provided'}), 400
    
    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
    if not base_dir:
        return jsonify({'error': 'Base directory not configured'}), 400
    
    # Get host path for Docker volume mount
    host_base_dir = _get_host_path(base_dir)
    
    # Get OCR source language from settings (default to 'en')
    ocr_source_language = settings.get('ocr_source_language', 'eng')
    
    added_jobs = []
    errors = []
    warnings = []
    
    for rel in paths:
        try:
            safe_rel = rel.lstrip('/').replace('..','')
            abs_path = os.path.join(base_dir, safe_rel)
            ext = os.path.splitext(abs_path)[1].lower()
            
            # Accept .sup and .sub files
            if ext not in ['.sup', '.sub'] or not os.path.isfile(abs_path):
                continue  # Skip silently
            
            # Validate .idx file exists for .sub files (VobSub requires both .sub and .idx)
            if ext == '.sub':
                idx_path = os.path.splitext(abs_path)[0] + '.idx'
                if not os.path.isfile(idx_path):
                    warnings.append({
                        'path': rel,
                        'message': f'Skipped: Missing corresponding .idx file'
                    })
                    continue
            
            # Add job to queue
            job_id = job_queue.add_job(
                JOB_TYPE_SUP_TO_SRT,
                safe_rel,
                params={
                    'host_base_dir': host_base_dir,
                    'base_dir': base_dir,
                    'ocr_source_language': ocr_source_language
                }
            )
            added_jobs.append({'path': rel, 'job_id': job_id})
            
        except Exception as e:
            logging.exception(f'Error adding job for {rel}: {e}')
            errors.append({'path': rel, 'message': str(e)})
    
    if errors:
        return jsonify({'error': f'Failed to add {len(errors)} job(s)', 'details': errors}), 500
    
    response = {
        'message': f'Added {len(added_jobs)} SUP/SUB->SRT job(s) to queue',
        'jobs': added_jobs
    }
    
    if warnings:
        response['warnings'] = warnings
    
    return jsonify(response)

@app.route('/api/translate', methods=['POST'])
def translate_subtitles():
    """Add translation jobs to queue"""
    if not job_queue:
        init_job_queue()
    
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not isinstance(paths, list) or len(paths) == 0:
        return jsonify({'error': 'No files provided'}), 400
    
    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
    if not base_dir:
        return jsonify({'error': 'Base directory not configured'}), 400
    
    vpn_dir = settings.get('mullvad_vpn_config_dir', '')
    target_lang = settings.get('translation_target_language', '').strip()
    if not target_lang:
        return jsonify({'error': 'Translation target language not configured'}), 400
    
    # Get host path for Docker volume mount
    host_base_dir = _get_host_path(base_dir)
    
    added_jobs = []
    errors = []
    
    for rel_path in paths:
        try:
            safe_rel = rel_path.lstrip('/').replace('..', '')
            abs_path = os.path.join(base_dir, safe_rel)
            ext = os.path.splitext(abs_path)[1].lower()
            
            if ext != '.srt' or not os.path.isfile(abs_path):
                continue  # Skip silently
            
            # Add job to queue
            job_id = job_queue.add_job(
                JOB_TYPE_TRANSLATE,
                safe_rel,
                params={
                    'base_dir': base_dir,
                    'host_base_dir': host_base_dir,
                    'vpn_dir': vpn_dir,
                    'target_lang': target_lang,
                    'settings_file': SETTINGS_FILE
                }
            )
            added_jobs.append({'path': rel_path, 'job_id': job_id})
            
        except Exception as e:
            logging.exception(f'Error adding translation job for {rel_path}: {e}')
            errors.append({'path': rel_path, 'message': str(e)})
    
    if errors:
        return jsonify({'error': f'Failed to add {len(errors)} job(s)', 'details': errors}), 500
    
    return jsonify({
        'message': f'Added {len(added_jobs)} translation job(s) to queue',
        'jobs': added_jobs
    })

@app.route('/api/search_subtitles', methods=['POST'])
def search_subtitles():
    """Queue online subtitle search/download jobs"""
    if not job_queue:
        init_job_queue()

    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])

    if not isinstance(paths, list) or len(paths) == 0:
        return jsonify({'error': 'No video files provided'}), 400

    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
    if not base_dir:
        return jsonify({'error': 'Base directory not configured'}), 400

    added_jobs = []
    errors = []

    for rel_path in paths:
        try:
            safe_rel = rel_path.lstrip('/').replace('..', '')
            abs_path = os.path.join(base_dir, safe_rel)

            if not os.path.isfile(abs_path):
                errors.append({'path': rel_path, 'message': 'File not found'})
                continue

            job_id = job_queue.add_job(
                JOB_TYPE_SEARCH_SUBTITLES,
                safe_rel,
                params={
                    'base_dir': base_dir,
                    'settings_file': SETTINGS_FILE
                }
            )
            added_jobs.append({'path': rel_path, 'job_id': job_id})

        except Exception as e:
            logging.exception(f'Error adding subtitle search job for {rel_path}: {e}')
            errors.append({'path': rel_path, 'message': str(e)})

    if errors and not added_jobs:
        return jsonify({'error': 'Failed to add subtitle search job(s)', 'details': errors}), 500

    return jsonify({
        'message': f'Added {len(added_jobs)} subtitle search job(s) to queue',
        'jobs': added_jobs,
        'errors': errors if errors else None
    })

def _translate_file_with_failover(file_path, settings, base_dir, vpn_dir, target_lang):
    """
    Attempt to translate a file with automatic failover.
    Returns (success: bool, message: str)
    """
    from datetime import datetime, timedelta
    
    provider = settings.get('provider', 'DeepL')
    max_attempts = 2  # Try current provider, then one fallback
    
    for attempt in range(max_attempts):
        # Get active API key info (if provider supports keys)
        provider_keys = settings.get('provider_keys', {})
        active_key_info = None
        
        if provider in ['DeepL', 'Azure', 'Gemini']:
            keys_list = provider_keys.get(provider, [])
            if not isinstance(keys_list, list) or len(keys_list) == 0:
                return False, f'No API keys configured for {provider}'
            
            # Find active key
            active_key_info = next((k for k in keys_list if k.get('active')), None)
            if not active_key_info:
                return False, f'No active API key for {provider}'
        
        # Run translation
        success, error_msg, updated_settings = _run_translator_docker(
            file_path, provider, active_key_info, settings, base_dir, vpn_dir, target_lang
        )
        
        if success:
            # Update settings with timestamp
            write_settings(updated_settings)
            return True, 'Translation completed'
        
        # Translation failed - handle failover
        logging.warning(f'Translation failed with {provider}: {error_msg}')
        
        # Update settings with error info
        write_settings(updated_settings)
        
        # Try to switch API key or provider
        switched = False
        
        if provider in ['DeepL', 'Azure', 'Gemini'] and active_key_info:
            auto_change_key = settings.get('auto_change_key_on_error', {}).get(provider, False)
            if auto_change_key:
                new_settings = _switch_to_next_api_key(updated_settings, provider)
                if new_settings != updated_settings:
                    settings = new_settings
                    write_settings(settings)
                    switched = True
                    logging.info(f'Switched to next API key for {provider}')
        
        if not switched:
            # Try switching provider
            auto_switch_provider = settings.get('auto_switch_on_error', False)
            if auto_switch_provider and attempt < max_attempts - 1:
                new_provider = _get_next_provider(provider)
                if new_provider and new_provider != provider:
                    settings['provider'] = new_provider
                    provider = new_provider
                    write_settings(settings)
                    switched = True
                    logging.info(f'Switched to provider {provider}')
        
        if switched:
            # Reload and retry
            settings = read_settings()
            continue
        else:
            # No more fallback options
            return False, error_msg
    
    return False, 'Translation failed after all attempts'

def _run_translator_docker(file_path, provider, key_info, settings, base_dir, vpn_dir, target_lang):
    """
    Run translator-service docker container.
    Returns (success: bool, error_msg: str, updated_settings: dict)
    """
    from datetime import datetime
    import copy
    
    updated_settings = copy.deepcopy(settings)
    
    vpn_config_path = ''
    api_key = ''
    
    if key_info:
        api_key = key_info.get('value', '')
        vpn_config = key_info.get('vpn_config', '')
        if vpn_config and vpn_dir and os.path.isdir(vpn_dir):
            vpn_config_path = os.path.join(vpn_dir, vpn_config)
            if not os.path.isfile(vpn_config_path):
                vpn_config_path = ''
            else:
                # Convert container VPN path to host path
                if vpn_config_path.startswith('/vpn-configs/'):
                    host_vpn_dir = os.environ.get('MULLVAD_VPN_DIR', '')
                    if host_vpn_dir:
                        vpn_filename = os.path.basename(vpn_config_path)
                        vpn_config_path = os.path.join(host_vpn_dir, vpn_filename)
    
    wait_ms = settings.get('wait_ms', {}).get(provider, 0)
    
    # Get host path for Docker volume mount
    host_base_dir = _get_host_path(base_dir)
    
    # Build docker command
    cmd = ['docker', 'run', '--rm']
    
    # Add --privileged flag if VPN is used
    if vpn_config_path:
        cmd.append('--privileged')
    
    # Mount base directory (use host path)
    cmd.extend(['-v', f'{host_base_dir}:/work'])
    
    # Mount VPN config if available
    if vpn_config_path:
        cmd.extend(['-v', f'{vpn_config_path}:/vpn.conf:ro'])
    
    cmd.append('translator-service:latest')
    
    # Add arguments
    if vpn_config_path:
        cmd.extend(['--vpn-config', '/vpn.conf'])
    cmd.extend(['--provider', provider.lower()])
    if api_key:
        cmd.extend(['--api-key', api_key])
    
    # Add Azure-specific arguments
    if provider == 'Azure':
        azure_endpoint = settings.get('azure_endpoint', 'https://api.cognitive.microsofttranslator.com')
        azure_region = settings.get('azure_region', 'eastus')
        cmd.extend(['--azure-endpoint', azure_endpoint])
        cmd.extend(['--azure-region', azure_region])
    
    # Calculate relative path from base_dir to preserve directory structure
    rel_file_path = os.path.relpath(file_path, base_dir)
    cmd.extend(['--file', f'/work/{rel_file_path}'])
    cmd.extend(['--wait-ms', str(wait_ms)])
    cmd.extend(['--target-lang', target_lang])
    
    logging.info(f'Running translator-service: {" ".join(cmd[:8])}... (args hidden)')
    
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
        
        # Update last_usage timestamp
        now_iso = datetime.now().isoformat()
        if key_info and provider in ['DeepL', 'Azure']:
            provider_keys = updated_settings.get('provider_keys', {})
            keys_list = provider_keys.get(provider, [])
            for k in keys_list:
                if k.get('value') == key_info.get('value'):
                    k['last_usage'] = now_iso
                    break
        
        if proc.returncode == 0:
            return True, '', updated_settings
        else:
            # Capture error
            error_msg = (proc.stderr or proc.stdout or '').strip()
            if not error_msg:
                error_msg = f'Docker exited with code {proc.returncode}'
            
            # Update error info for API key
            if key_info and provider in ['DeepL', 'Azure']:
                provider_keys = updated_settings.get('provider_keys', {})
                keys_list = provider_keys.get(provider, [])
                for k in keys_list:
                    if k.get('value') == key_info.get('value'):
                        k['last_error'] = error_msg
                        k['last_error_at'] = now_iso
                        break
            
            return False, error_msg, updated_settings
            
    except subprocess.TimeoutExpired:
        error_msg = 'Translation timeout (10 minutes)'
        if key_info and provider in ['DeepL', 'Azure']:
            now_iso = datetime.now().isoformat()
            provider_keys = updated_settings.get('provider_keys', {})
            keys_list = provider_keys.get(provider, [])
            for k in keys_list:
                if k.get('value') == key_info.get('value'):
                    k['last_error'] = error_msg
                    k['last_error_at'] = now_iso
                    break
        return False, error_msg, updated_settings
    except Exception as e:
        return False, str(e), updated_settings

def _switch_to_next_api_key(settings, provider):
    """
    Switch to the next available API key for the provider.
    Returns updated settings.
    """
    from datetime import datetime, timedelta
    import copy
    
    updated = copy.deepcopy(settings)
    provider_keys = updated.get('provider_keys', {})
    keys_list = provider_keys.get(provider, [])
    
    if not isinstance(keys_list, list) or len(keys_list) <= 1:
        return settings  # No other keys to switch to
    
    retry_days = settings.get('retry_after_days', {}).get(provider, 0)
    now = datetime.now()
    
    # Find current active key index
    current_idx = next((i for i, k in enumerate(keys_list) if k.get('active')), 0)
    
    # Try to find next usable key
    for offset in range(1, len(keys_list)):
        next_idx = (current_idx + offset) % len(keys_list)
        candidate = keys_list[next_idx]
        
        # Check if this key had recent error
        if retry_days > 0:
            last_error_at = candidate.get('last_error_at')
            if last_error_at:
                try:
                    error_time = datetime.fromisoformat(last_error_at)
                    if (now - error_time).days < retry_days:
                        continue  # Skip this key
                except Exception:
                    pass
        
        # Switch to this key
        for i, k in enumerate(keys_list):
            k['active'] = (i == next_idx)
        
        return updated
    
    # No usable key found
    return settings

def _get_next_provider(current_provider):
    """Get next provider in rotation"""
    if current_provider == 'Gemini':
        return 'DeepL'
    elif current_provider == 'DeepL':
        return 'Azure'
    elif current_provider == 'Azure':
        return 'Gemini'
    return None

def _srt_timestamp(seconds: float) -> str:
    if seconds is None:
        seconds = 0.0
    td = timedelta(seconds=max(0.0, float(seconds)))
    # hours:minutes:seconds,milliseconds
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    millis = int((td.total_seconds() - total_seconds) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def _ffprobe_subtitle_streams(video_path: str):
    try:
        cmd = [
            'ffprobe','-v','error','-show_entries','stream=index,codec_name,codec_type,disposition:stream_tags=language','-of','json', video_path
        ]
        logging.info('Running ffprobe for streams: %s', ' '.join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            logging.error('ffprobe error: %s', proc.stderr)
            return []
        data = json.loads(proc.stdout)
        subs = []
        sub_order = 0
        for s in data.get('streams', []):
            if s.get('codec_type') == 'subtitle':
                subs.append({
                    'index': s.get('index'),           # absolute index
                    'sub_order': sub_order,             # order among subtitle streams
                    'codec': s.get('codec_name'),
                    'language': (s.get('tags') or {}).get('language')
                })
                sub_order += 1
        return subs
    except Exception as e:
        logging.exception('ffprobe failed: %s', e)
        return []

def _ffprobe_packets(video_path: str, sub_order: int):
    try:
        # Use subtitle-order selector without input index for ffprobe
        cmd = ['ffprobe','-v','error','-select_streams', f's:{sub_order}', '-show_packets','-of','json', video_path]
        logging.info('Running ffprobe for packets: %s', ' '.join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            logging.error('ffprobe packets error: %s', proc.stderr)
            return []
        data = json.loads(proc.stdout)
        return data.get('packets', [])
    except Exception as e:
        logging.exception('ffprobe packets failed: %s', e)
        return []

def _extract_sub_images(video_path: str, sub_order: int, out_dir: str) -> (bool, str):
    # Try to dump subtitle bitmaps to png sequence
    try:
        os.makedirs(out_dir, exist_ok=True)
        # -copyts to preserve pts; -frame_pts to write pts in filenames when available
        cmd = [
            'ffmpeg','-y',
            '-analyzeduration','200M','-probesize','50M',
            '-i', video_path,
            '-map', f'0:s:{sub_order}',
            '-vsync','0','-frame_pts','1',
            '-f','image2','-c:v','png',
            os.path.join(out_dir, 'frame_%010d.png')
        ]
        logging.info('Running ffmpeg to extract subtitle bitmaps: %s', ' '.join(cmd))
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            logging.error('ffmpeg error: %s', proc.stderr)
            return False, proc.stderr
        return True, ''
    except Exception as e:
        logging.exception('ffmpeg extraction failed: %s', e)
        return False, str(e)

@app.route('/api/rename', methods=['POST'])
def rename_file():
    """Rename or move a file or folder"""
    data = request.get_json(silent=True) or {}
    old_path = data.get('old_path', '').strip()
    new_path = data.get('new_path', '').strip()
    
    if not old_path or not new_path:
        return jsonify({'error': 'Both old_path and new_path are required'}), 400
    
    # Validate paths are absolute
    if not os.path.isabs(old_path) or not os.path.isabs(new_path):
        return jsonify({'error': 'Paths must be absolute'}), 400
    
    # Normalize paths
    old_path = os.path.normpath(old_path)
    new_path = os.path.normpath(new_path)
    
    # Check if source exists
    if not os.path.exists(old_path):
        return jsonify({'error': 'Source does not exist'}), 404
    
    # Check if target already exists
    if os.path.exists(new_path):
        return jsonify({'error': 'Target path already exists'}), 409
    
    # Ensure target parent directory exists
    target_parent_dir = os.path.dirname(new_path)
    try:
        if not os.path.exists(target_parent_dir):
            os.makedirs(target_parent_dir, exist_ok=True)
            logging.info(f'Created parent directory: {target_parent_dir}')
    except Exception as e:
        logging.exception(f'Failed to create target parent directory {target_parent_dir}: {e}')
        return jsonify({'error': f'Failed to create target parent directory: {str(e)}'}), 500
    
    # Perform rename/move (works for both files and directories)
    try:
        is_dir = os.path.isdir(old_path)
        item_type = 'Folder' if is_dir else 'File'
        
        logging.info(f'Renaming {item_type.lower()}: {old_path} -> {new_path}')
        os.rename(old_path, new_path)
        logging.info(f'Successfully renamed/moved {item_type.lower()}')
        
        # Determine if this was a rename or move
        old_dir = os.path.dirname(old_path)
        new_dir = os.path.dirname(new_path)
        operation = 'moved' if old_dir != new_dir else 'renamed'
        
        return jsonify({
            'message': f'{item_type} {operation} successfully',
            'old_path': old_path,
            'new_path': new_path,
            'is_directory': is_dir
        })
    except Exception as e:
        logging.exception(f'Rename/move error: {e}')
        return jsonify({'error': f'Failed to rename/move: {str(e)}'}), 500

@app.route('/api/extract_subtitles', methods=['POST'])
def extract_subtitles():
    """Add subtitle extraction jobs to queue"""
    if not job_queue:
        init_job_queue()
    
    data = request.get_json(silent=True) or {}
    paths = data.get('paths', [])
    if not isinstance(paths, list) or len(paths) == 0:
        return jsonify({'error': 'No files provided'}), 400
    
    settings = read_settings()
    base_dir = settings.get('root_dir') or app.config.get('BASE_DIR')
    if not base_dir:
        return jsonify({'error': 'Base directory not configured'}), 400
    
    # Get extraction source language from settings
    extraction_source_language = settings.get('extraction_source_language', 'eng')
    
    video_exts = {'.mkv','.mp4','.avi','.mov','.wmv','.flv','.webm','.m4v','.mpeg','.mpg'}
    added_jobs = []
    errors = []
    
    for rel in paths:
        try:
            safe_rel = rel.lstrip('/').replace('..','')
            video_path = os.path.join(base_dir, safe_rel)
            ext = os.path.splitext(video_path)[1].lower()
            if ext not in video_exts or not os.path.isfile(video_path):
                continue  # Skip silently
            
            # Add job to queue
            job_id = job_queue.add_job(
                JOB_TYPE_EXTRACT,
                safe_rel,
                params={
                    'base_dir': base_dir,
                    'extraction_source_language': extraction_source_language
                }
            )
            added_jobs.append({'path': rel, 'job_id': job_id})
            
        except Exception as e:
            logging.exception(f'Error adding job for {rel}: {e}')
            errors.append({'path': rel, 'message': str(e)})
    
    if errors:
        return jsonify({'error': f'Failed to add {len(errors)} job(s)', 'details': errors}), 500
    
    return jsonify({
        'message': f'Added {len(added_jobs)} extraction job(s) to queue',
        'jobs': added_jobs
    })

if __name__ == '__main__':
    # Initialize job queue
    logging.info('Initializing job queue...')
    init_job_queue()
    
    # Start the file system observer in a separate thread using configured root_dir
    def boot_observer():
        settings = read_settings()
        root = settings.get('root_dir') or app.config.get('BASE_DIR')
        start_observer(root)
    observer_thread = threading.Thread(target=boot_observer)
    observer_thread.daemon = True
    observer_thread.start()
    
    # Create necessary directories
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)
    
    app.run(debug=True)
