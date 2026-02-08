document.addEventListener('DOMContentLoaded', () => {
  const providerSelect = document.getElementById('providerSelect');
  const keysContainer = document.getElementById('keysContainer');
  const keysList = document.getElementById('keysList');
  const addKeyBtn = document.getElementById('addKeyBtn');
  const saveBtn = document.getElementById('saveBtn');
  const statusMsg = document.getElementById('statusMsg');
  const autoSwitchOnError = document.getElementById('autoSwitchOnError');
  const rootDirInput = document.getElementById('rootDirInput');
  const translationTargetLanguageInput = document.getElementById('translationTargetLanguageInput');
  const waitMsInput = document.getElementById('waitMsInput');
  const maxCharsPerRequestInput = document.getElementById('maxCharsPerRequestInput');
  const retryDaysInput = document.getElementById('retryDaysInput');
  const autoChangeKeyOnError = document.getElementById('autoChangeKeyOnError');
  const vpnConfigDirInput = document.getElementById('vpnConfigDirInput');
  const excludedFileTypesInput = document.getElementById('excludedFileTypesInput');
  const maxParallelJobsInput = document.getElementById('maxParallelJobsInput');
  const azureContainer = document.getElementById('azureContainer');
  const azureEndpointInput = document.getElementById('azureEndpointInput');
  const azureRegionInput = document.getElementById('azureRegionInput');
  const deeplContainer = document.getElementById('deeplContainer');
  const deeplEndpointInput = document.getElementById('deeplEndpointInput');
  const geminiContainer = document.getElementById('geminiContainer');
  const geminiModelInput = document.getElementById('geminiModelInput');
  const subtitleSearchLanguagesInput = document.getElementById('subtitleSearchLanguagesInput');
  const subtitleMaxDownloadsInput = document.getElementById('subtitleMaxDownloadsInput');
  const ocrSourceLanguageInput = document.getElementById('ocrSourceLanguageInput');
  const extractionSourceLanguageInput = document.getElementById('extractionSourceLanguageInput');
  const opensubtitlesEnabled = document.getElementById('opensubtitlesEnabled');
  const opensubtitlesConfig = document.getElementById('opensubtitlesConfig');
  const opensubtitlesUsername = document.getElementById('opensubtitlesUsername');
  const opensubtitlesPassword = document.getElementById('opensubtitlesPassword');
  const addic7edEnabled = document.getElementById('addic7edEnabled');
  const addic7edConfig = document.getElementById('addic7edConfig');
  const addic7edUsername = document.getElementById('addic7edUsername');
  const addic7edPassword = document.getElementById('addic7edPassword');
  const subdlEnabled = document.getElementById('subdlEnabled');
  const subdlConfig = document.getElementById('subdlConfig');
  const subdlApiKey = document.getElementById('subdlApiKey');
  const subdlUploadToken = document.getElementById('subdlUploadToken');
  const syncDontFixFramerate = document.getElementById('syncDontFixFramerate');
  const syncUseGoldenSection = document.getElementById('syncUseGoldenSection');
  const syncVadSelect = document.getElementById('syncVadSelect');

  let allowedProviders = [];
  let currentSettings = null;
  let availableVpnConfigs = []; // List of available .conf files
  let assignedVpnConfigs = {}; // Map of config -> {provider, key}
  // Cache edited keys per provider locally to preserve when switching providers
  let editedKeys = { DeepL: [], Azure: [], Gemini: [] }; // array of {value, active, last_usage, last_error, last_error_at, vpn_config}
  let editedWaitMs = {}; // provider -> ms
  let editedMaxCharsPerRequest = {}; // provider -> chars
  let editedRetryDays = { DeepL: 0, Azure: 0, Gemini: 0 };
  let editedAutoChangeOnError = { DeepL: false, Azure: false, Gemini: false };

  function setStatus(msg, timeout=2000) {
    statusMsg.textContent = msg;
    if (timeout) setTimeout(() => { statusMsg.textContent = ''; }, timeout);
  }

  function parseSubtitleSearchLanguages() {
    const raw = (subtitleSearchLanguagesInput && subtitleSearchLanguagesInput.value) ? subtitleSearchLanguagesInput.value : '';
    const arr = raw.split(',').map(s => s.trim()).filter(Boolean);
    return arr.length > 0 ? arr : ['en'];
  }

  async function loadSettings() {
    const res = await fetch('/api/settings');
    const data = await res.json();
    allowedProviders = data.allowed_providers || [];
    currentSettings = data.settings || {};
    const savedKeys = (currentSettings && currentSettings.provider_keys) ? currentSettings.provider_keys : {};
    // Initialize local cache with saved settings BEFORE rendering
    editedKeys = {
      DeepL: Array.isArray(savedKeys.DeepL) ? savedKeys.DeepL.map(copyKeyObj) : [],
      Azure: Array.isArray(savedKeys.Azure) ? savedKeys.Azure.map(copyKeyObj) : [],
      Gemini: Array.isArray(savedKeys.Gemini) ? savedKeys.Gemini.map(copyKeyObj) : []
    };
    editedWaitMs = Object.assign({}, currentSettings.wait_ms || {});
    editedMaxCharsPerRequest = Object.assign({}, currentSettings.max_chars_per_request || {});
    editedRetryDays = Object.assign({ DeepL: 0, Azure: 0, Gemini: 0 }, currentSettings.retry_after_days || {});
    editedAutoChangeOnError = Object.assign({ DeepL: false, Azure: false, Gemini: false }, currentSettings.auto_change_key_on_error || {});
    renderProviders();
    // root dir
    rootDirInput.value = currentSettings.root_dir || '';
    // vpn config dir
    vpnConfigDirInput.value = currentSettings.mullvad_vpn_config_dir || '';
    // excluded file types
    excludedFileTypesInput.value = currentSettings.excluded_file_types || '';
    // max parallel jobs
    maxParallelJobsInput.value = currentSettings.max_parallel_jobs || 2;
    // Azure settings
    azureEndpointInput.value = currentSettings.azure_endpoint || 'https://api.cognitive.microsofttranslator.com';
    azureRegionInput.value = currentSettings.azure_region || 'eastus';
    if (deeplEndpointInput) {
      deeplEndpointInput.value = currentSettings.deepl_endpoint || 'https://api-free.deepl.com/v2/translate';
    }
    if (geminiModelInput) {
      geminiModelInput.value = currentSettings.gemini_model || 'gemini-2.0-flash';
    }
    // languages
    translationTargetLanguageInput.value = currentSettings.translation_target_language || currentSettings.target_language || '';
    // OCR source language
    if (ocrSourceLanguageInput) {
      ocrSourceLanguageInput.value = currentSettings.ocr_source_language || 'eng';
    }
    // Extraction source language
    if (extractionSourceLanguageInput) {
      extractionSourceLanguageInput.value = currentSettings.extraction_source_language || 'eng';
    }
    // subtitle search languages
    const subLangs = currentSettings.subtitle_search_languages || ['en'];
    subtitleSearchLanguagesInput.value = Array.isArray(subLangs) ? subLangs.join(', ') : String(subLangs || '');
    // subtitle max downloads
    subtitleMaxDownloadsInput.value = Number(currentSettings.subtitle_max_downloads || 1);
    // subtitle providers
    const subProviders = currentSettings.subtitle_providers || {};
    const osConfig = subProviders.opensubtitles || {};
    opensubtitlesEnabled.checked = osConfig.enabled || false;
    opensubtitlesUsername.value = osConfig.username || '';
    opensubtitlesPassword.value = osConfig.password || '';
    opensubtitlesConfig.style.display = osConfig.enabled ? 'block' : 'none';
    const a7Config = subProviders.addic7ed || {};
    addic7edEnabled.checked = a7Config.enabled || false;
    addic7edUsername.value = a7Config.username || '';
    addic7edPassword.value = a7Config.password || '';
    addic7edConfig.style.display = a7Config.enabled ? 'block' : 'none';
    const subdl = subProviders.subdl || {};
    if (subdlEnabled) subdlEnabled.checked = subdl.enabled || false;
    if (subdlApiKey) subdlApiKey.value = subdl.api_key || '';
    if (subdlUploadToken) subdlUploadToken.value = subdl.upload_token || '';
    if (subdlConfig) subdlConfig.style.display = (subdlEnabled && subdlEnabled.checked) ? 'block' : 'none';
    // set wait ms for current provider
    const curProv = (currentSettings && currentSettings.provider) || (allowedProviders[0] || '');
    waitMsInput.value = Number((currentSettings.wait_ms && currentSettings.wait_ms[curProv]) || 0);
    if (maxCharsPerRequestInput) {
      maxCharsPerRequestInput.value = Number((currentSettings.max_chars_per_request && currentSettings.max_chars_per_request[curProv]) || 0);
    }
    if (syncDontFixFramerate) syncDontFixFramerate.checked = !!currentSettings.sync_dont_fix_framerate;
    if (syncUseGoldenSection) syncUseGoldenSection.checked = !!currentSettings.sync_use_golden_section;
    if (syncVadSelect) syncVadSelect.value = currentSettings.sync_vad || 'default';
    renderGlobals();
    await loadVpnConfigs();
    renderKeysSection();
  }

  function renderProviders() {
    providerSelect.innerHTML = '';
    allowedProviders.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p;
      providerSelect.appendChild(opt);
    });
    if (currentSettings && currentSettings.provider) {
      providerSelect.value = currentSettings.provider;
    }
  }

  function providerSupportsKeys(provider) {
    return provider === 'DeepL' || provider === 'Azure' || provider === 'Gemini';
  }

  async function loadVpnConfigs() {
    try {
      const res = await fetch('/api/vpn_configs');
      const data = await res.json();
      availableVpnConfigs = data.configs || [];
      assignedVpnConfigs = data.assigned || {};
    } catch (e) {
      console.error('Failed to load VPN configs', e);
      availableVpnConfigs = [];
      assignedVpnConfigs = {};
    }
  }

  function copyKeyObj(k){
    return {
      value: (k && k.value) ? String(k.value) : String(k||''),
      active: !!(k && k.active),
      last_usage: (k && k.last_usage) || null,
      last_error: (k && k.last_error) || null,
      last_error_at: (k && k.last_error_at) || null,
      vpn_config: (k && k.vpn_config) || null
    };
  }

  function getVisibleKeys() {
    const rows = Array.from(document.querySelectorAll('#keysList .key-item'));
    const keys = rows.map(row => {
      const val = row.querySelector('.key-input').value.trim();
      const active = row.querySelector('input[type="radio"].active-radio').checked;
      const vpnSelect = row.querySelector('.vpn-select');
      const vpnConfig = vpnSelect ? vpnSelect.value : null;
      const lastUsage = row.dataset.lastUsage || null;
      const lastError = row.dataset.lastError || null;
      const lastErrorAt = row.dataset.lastErrorAt || null;
      return val ? {
        value: val,
        active,
        vpn_config: vpnConfig || null,
        last_usage: lastUsage,
        last_error: lastError,
        last_error_at: lastErrorAt
      } : null;
    }).filter(Boolean);
    // ensure single active
    enforceSingleActive(keys);
    return keys;
  }

  function persistCurrentProviderKeys() {
    const provider = providerSelect.value;
    if (providerSupportsKeys(provider)) {
      editedKeys[provider] = getVisibleKeys();
    }
  }

  function renderKeysSection() {
    const provider = providerSelect.value;
    const show = providerSupportsKeys(provider);
    keysContainer.classList.toggle('hidden', !show);
    
    // Show/hide Azure-specific settings
    const isAzure = provider === 'Azure';
    azureContainer.classList.toggle('hidden', !isAzure);

    // Show/hide DeepL-specific settings
    const isDeepL = provider === 'DeepL';
    if (deeplContainer) {
      deeplContainer.classList.toggle('hidden', !isDeepL);
    }

    // Show/hide Gemini-specific settings
    const isGemini = provider === 'Gemini';
    if (geminiContainer) {
      geminiContainer.classList.toggle('hidden', !isGemini);
    }
    
    if (!show) return;

    // Prefer cached edits; if empty and settings has saved keys, seed from there
    let arr = Array.isArray(editedKeys[provider]) ? editedKeys[provider].map(copyKeyObj) : [];
    if ((!arr || arr.length === 0) && currentSettings && currentSettings.provider_keys && Array.isArray(currentSettings.provider_keys[provider])) {
      arr = currentSettings.provider_keys[provider].map(copyKeyObj);
      editedKeys[provider] = arr.map(copyKeyObj);
    }

    keysList.innerHTML = '';
    arr.forEach((obj, idx) => addKeyRow(obj));
    if (arr.length === 0) addKeyRow({ value: '', active: true, last_error_at: null });

    // Provider-specific options
    retryDaysInput.value = Number(editedRetryDays[provider] || 0);
    autoChangeKeyOnError.checked = !!editedAutoChangeOnError[provider];
  }

  function addKeyRow(keyObj) {
    const value = (keyObj && keyObj.value) ? keyObj.value : '';
    const active = !!(keyObj && keyObj.active);
    const vpnConfig = (keyObj && keyObj.vpn_config) || '';
    const lastUsage = (keyObj && keyObj.last_usage) || null;
    const lastError = (keyObj && keyObj.last_error) || null;
    const lastErrorAt = (keyObj && keyObj.last_error_at) || null;
    
    const row = document.createElement('div');
    row.className = 'key-item';
    row.dataset.lastUsage = lastUsage || '';
    row.dataset.lastError = lastError || '';
    row.dataset.lastErrorAt = lastErrorAt || '';
    
    // Build VPN config options
    const provider = providerSelect.value;
    const usedConfigs = getUsedVpnConfigs(provider, value);
    const vpnOptions = availableVpnConfigs.map(cfg => {
      const isUsed = usedConfigs.includes(cfg) && cfg !== vpnConfig;
      const display = isUsed ? `${cfg} (used)` : cfg;
      const disabled = isUsed ? 'disabled' : '';
      const selected = cfg === vpnConfig ? 'selected' : '';
      return `<option value="${escapeHtml(cfg)}" ${disabled} ${selected}>${escapeHtml(display)}</option>`;
    }).join('');
    
    const infoHtml = [];
    if (lastUsage) infoHtml.push(`<div class="small muted">Last used: ${escapeHtml(lastUsage)}</div>`);
    if (lastError) infoHtml.push(`<div class="small muted" style="color:#c33;">Error: ${escapeHtml(lastError)}</div>`);
    if (lastErrorAt) infoHtml.push(`<div class="small muted" style="color:#c33;">Error at: ${escapeHtml(lastErrorAt)}</div>`);
    
    const hasError = lastError || lastErrorAt || lastUsage;
    const clearErrorBtnHtml = hasError ? `<button type="button" class="clear-error-btn" style="font-size:11px; padding:2px 6px; margin-top:4px; cursor:pointer;">Clear Error/Usage</button>` : '';
    
    row.innerHTML = `
      <div>
        <input type="text" class="key-input" placeholder="API key" value="${escapeHtml(value)}" style="margin-bottom:4px;"/>
        <label style="font-size:12px; color:#555;">VPN Config:</label>
        <select class="vpn-select" style="width:100%; padding:4px; font-size:12px;">
          <option value="">-- Select VPN Config --</option>
          ${vpnOptions}
        </select>
        ${infoHtml.join('')}
        ${clearErrorBtnHtml}
      </div>
      <label class="inline"><input type="radio" name="activeKey" class="active-radio" ${active ? 'checked' : ''}/> Active</label>
      <button type="button" class="btn remove" title="Remove"><i class="fas fa-times"></i></button>
    `;
    
    // radio behavior
    const radio = row.querySelector('.active-radio');
    radio.addEventListener('change', () => {
      if (radio.checked) {
        // uncheck all others
        document.querySelectorAll('#keysList .active-radio').forEach(r => { if (r !== radio) r.checked = false; });
      }
    });
    
    // VPN select change handler
    const vpnSelect = row.querySelector('.vpn-select');
    vpnSelect.addEventListener('change', () => {
      // Re-render to update disabled states
      persistCurrentProviderKeys();
      renderKeysSection();
    });
    
    // Clear error button handler
    const clearErrorBtn = row.querySelector('.clear-error-btn');
    if (clearErrorBtn) {
      clearErrorBtn.addEventListener('click', () => {
        // Clear error and usage data
        row.dataset.lastUsage = '';
        row.dataset.lastError = '';
        row.dataset.lastErrorAt = '';
        // Re-render to update UI
        persistCurrentProviderKeys();
        renderKeysSection();
      });
    }
    
    row.querySelector('.remove').addEventListener('click', () => {
      row.remove();
      ensureAtLeastOneActiveRadio();
      // Re-render to update VPN config availability
      persistCurrentProviderKeys();
      renderKeysSection();
    });
    keysList.appendChild(row);
  }
  
  function getUsedVpnConfigs(provider, excludeKeyValue) {
    // Get list of VPN configs already assigned to keys in current provider
    const keys = editedKeys[provider] || [];
    return keys
      .filter(k => k.value !== excludeKeyValue && k.vpn_config)
      .map(k => k.vpn_config);
  }

  function escapeHtml(unsafe) {
    return unsafe
      .toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function enforceSingleActive(arr){
    let found = false;
    arr.forEach(k => {
      if (k.active && !found) { found = true; }
      else { k.active = false; }
    });
    if (!found && arr.length > 0) arr[0].active = true;
  }

  function ensureAtLeastOneActiveRadio(){
    const radios = Array.from(document.querySelectorAll('#keysList .active-radio'));
    if (radios.length === 0) return;
    if (!radios.some(r => r.checked)) radios[0].checked = true;
  }

  providerSelect.addEventListener('change', () => {
    // Before switching, store current provider's keys into cache
    persistCurrentProviderKeys();
    // When switching to a provider, always seed cache from saved settings to avoid bleed-through
    const next = providerSelect.value;
    // update wait ms input for selected provider
    waitMsInput.value = Number((currentSettings.wait_ms && currentSettings.wait_ms[next]) || editedWaitMs[next] || 0);
    if (maxCharsPerRequestInput) {
      maxCharsPerRequestInput.value = Number((currentSettings.max_chars_per_request && currentSettings.max_chars_per_request[next]) || editedMaxCharsPerRequest[next] || 0);
    }
    if (providerSupportsKeys(next)) {
      const saved = currentSettings?.provider_keys?.[next];
      editedKeys[next] = Array.isArray(saved) ? saved.map(copyKeyObj) : [];
      // load provider-specific options
      retryDaysInput.value = Number((currentSettings.retry_after_days && currentSettings.retry_after_days[next]) || 0);
      autoChangeKeyOnError.checked = !!(currentSettings.auto_change_key_on_error && currentSettings.auto_change_key_on_error[next]);
    }
    renderKeysSection();
  });

  addKeyBtn.addEventListener('click', () => {
    addKeyRow({ value: '', active: false, last_error_at: null });
    ensureAtLeastOneActiveRadio();
  });

  saveBtn.addEventListener('click', async () => {
    // Persist current visible keys into cache
    persistCurrentProviderKeys();
    // Persist wait_ms for current selected provider only
    editedWaitMs[providerSelect.value] = Number(waitMsInput.value || 0);
    if (maxCharsPerRequestInput) {
      editedMaxCharsPerRequest[providerSelect.value] = Number(maxCharsPerRequestInput.value || 0);
    }
    // Persist provider-specific advanced options
    const p = providerSelect.value;
    if (providerSupportsKeys(p)) {
      editedRetryDays[p] = Number(retryDaysInput.value || 0);
      editedAutoChangeOnError[p] = !!autoChangeKeyOnError.checked;
    }
    // Always send the full map for all providers that support keys and options
    // Validate VPN config assignments (1:1 relation)
    const validationError = validateVpnAssignments();
    if (validationError) {
      setStatus(validationError, 5000);
      return;
    }
    
    const subLangs = parseSubtitleSearchLanguages();
    
    const payload = {
      provider: providerSelect.value,
      auto_switch_on_error: !!autoSwitchOnError.checked,
      root_dir: (rootDirInput.value || '').trim(),
      mullvad_vpn_config_dir: (vpnConfigDirInput.value || '').trim(),
      excluded_file_types: (excludedFileTypesInput.value || '').trim(),
      max_parallel_jobs: currentSettings.max_parallel_jobs || 2, // Keep existing value (field is disabled)
      azure_endpoint: (azureEndpointInput.value || '').trim(),
      azure_region: (azureRegionInput.value || '').trim(),
      deepl_endpoint: (deeplEndpointInput ? ((deeplEndpointInput.value || '').trim() || 'https://api-free.deepl.com/v2/translate') : 'https://api-free.deepl.com/v2/translate'),
      gemini_model: (geminiModelInput ? ((geminiModelInput.value || '').trim() || 'gemini-2.0-flash') : 'gemini-2.0-flash'),
      translation_target_language: (translationTargetLanguageInput.value || '').trim(),
      ocr_source_language: (ocrSourceLanguageInput ? ocrSourceLanguageInput.value : '').trim() || 'eng',
      extraction_source_language: (extractionSourceLanguageInput ? extractionSourceLanguageInput.value : '').trim() || 'eng',
      subtitle_search_languages: Array.isArray(subLangs) && subLangs.length > 0 ? subLangs : ['en'],
      // subtitle_max_downloads is read-only in UI; keep existing value
      subtitle_max_downloads: Number(currentSettings.subtitle_max_downloads || 1),
      subtitle_providers: {
        opensubtitles: {
          enabled: !!opensubtitlesEnabled.checked,
          username: (opensubtitlesUsername.value || '').trim(),
          password: (opensubtitlesPassword.value || '').trim()
        },
        addic7ed: {
          enabled: !!addic7edEnabled.checked,
          username: (addic7edUsername.value || '').trim(),
          password: (addic7edPassword.value || '').trim()
        },
        subdl: {
          enabled: !!(subdlEnabled && subdlEnabled.checked),
          api_key: (subdlApiKey ? (subdlApiKey.value || '').trim() : ''),
          upload_token: (subdlUploadToken ? (subdlUploadToken.value || '').trim() : '')
        }
      },
      wait_ms: editedWaitMs,
      max_chars_per_request: editedMaxCharsPerRequest,
      retry_after_days: editedRetryDays,
      auto_change_key_on_error: editedAutoChangeOnError,
      provider_keys: {
        DeepL: editedKeys.DeepL.map(copyKeyObj),
        Azure: editedKeys.Azure.map(copyKeyObj),
        Gemini: editedKeys.Gemini.map(copyKeyObj)
      },
      sync_dont_fix_framerate: !!(syncDontFixFramerate && syncDontFixFramerate.checked),
      sync_use_golden_section: !!(syncUseGoldenSection && syncUseGoldenSection.checked),
      sync_vad: (syncVadSelect ? (syncVadSelect.value || 'default') : 'default')
    };
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.ok) {
        currentSettings = data.settings;
        // Sync cache with saved settings
        editedKeys = {
          DeepL: Array.isArray(currentSettings?.provider_keys?.DeepL) ? currentSettings.provider_keys.DeepL.map(copyKeyObj) : [],
          Azure: Array.isArray(currentSettings?.provider_keys?.Azure) ? currentSettings.provider_keys.Azure.map(copyKeyObj) : [],
          Gemini: Array.isArray(currentSettings?.provider_keys?.Gemini) ? currentSettings.provider_keys.Gemini.map(copyKeyObj) : []
        };
        editedWaitMs = Object.assign({}, currentSettings.wait_ms || {});
        editedMaxCharsPerRequest = Object.assign({}, currentSettings.max_chars_per_request || {});
        editedRetryDays = Object.assign({ DeepL: 0, Azure: 0, Gemini: 0 }, currentSettings.retry_after_days || {});
        editedAutoChangeOnError = Object.assign({ DeepL: false, Azure: false, Gemini: false }, currentSettings.auto_change_key_on_error || {});
        autoSwitchOnError.checked = !!currentSettings.auto_switch_on_error;
        // refresh wait ms input for currently selected provider
        waitMsInput.value = Number((currentSettings.wait_ms && currentSettings.wait_ms[providerSelect.value]) || 0);
        if (maxCharsPerRequestInput) {
          maxCharsPerRequestInput.value = Number((currentSettings.max_chars_per_request && currentSettings.max_chars_per_request[providerSelect.value]) || 0);
        }
        vpnConfigDirInput.value = currentSettings.mullvad_vpn_config_dir || '';
        if (ocrSourceLanguageInput) {
          ocrSourceLanguageInput.value = currentSettings.ocr_source_language || 'eng';
        }
        if (extractionSourceLanguageInput) {
          extractionSourceLanguageInput.value = currentSettings.extraction_source_language || 'eng';
        }
        azureEndpointInput.value = currentSettings.azure_endpoint || 'https://api.cognitive.microsofttranslator.com';
        azureRegionInput.value = currentSettings.azure_region || 'eastus';
        if (deeplEndpointInput) {
          deeplEndpointInput.value = currentSettings.deepl_endpoint || 'https://api-free.deepl.com/v2/translate';
        }
        if (geminiModelInput) {
          geminiModelInput.value = currentSettings.gemini_model || 'gemini-2.0-flash';
        }
        if (syncDontFixFramerate) syncDontFixFramerate.checked = !!currentSettings.sync_dont_fix_framerate;
        if (syncUseGoldenSection) syncUseGoldenSection.checked = !!currentSettings.sync_use_golden_section;
        if (syncVadSelect) syncVadSelect.value = currentSettings.sync_vad || 'default';
        await loadVpnConfigs();
        renderKeysSection();
        setStatus('Saved');
      } else {
        setStatus(data.error || 'Save failed', 3000);
      }
    } catch (e) {
      console.error(e);
      setStatus('Save failed', 3000);
    }
  });

  // Subtitle provider checkbox toggles
  opensubtitlesEnabled.addEventListener('change', () => {
    opensubtitlesConfig.style.display = opensubtitlesEnabled.checked ? 'block' : 'none';
  });
  
  addic7edEnabled.addEventListener('change', () => {
    addic7edConfig.style.display = addic7edEnabled.checked ? 'block' : 'none';
  });

  if (subdlEnabled) {
    subdlEnabled.addEventListener('change', () => {
      if (subdlConfig) subdlConfig.style.display = subdlEnabled.checked ? 'block' : 'none';
    });
  }

  function renderGlobals(){
    autoSwitchOnError.checked = !!currentSettings.auto_switch_on_error;
  }
  
  function validateVpnAssignments() {
    // Check that each VPN config is assigned to at most one API key across all providers
    const allAssignments = {};
    for (const provider of ['DeepL', 'Azure', 'Gemini']) {
      const keys = editedKeys[provider] || [];
      for (const key of keys) {
        if (key.vpn_config) {
          if (allAssignments[key.vpn_config]) {
            return `VPN config "${key.vpn_config}" is assigned to multiple API keys. Each config must be unique (1:1 relation).`;
          }
          allAssignments[key.vpn_config] = {provider, key: key.value};
        }
      }
    }
    return null; // No error
  }

  // Export settings
  const exportSettingsBtn = document.getElementById('exportSettingsBtn');
  if (exportSettingsBtn) {
    exportSettingsBtn.addEventListener('click', async () => {
      try {
        const response = await fetch('/api/settings/export');
        if (!response.ok) {
          const data = await response.json();
          alert('Export failed: ' + (data.error || 'Unknown error'));
          return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'settings.json';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        setStatus('Settings exported successfully', 3000);
      } catch (e) {
        console.error('Export error:', e);
        alert('Export failed: ' + e.message);
      }
    });
  }

  // Import settings
  const importSettingsBtn = document.getElementById('importSettingsBtn');
  const importSettingsFile = document.getElementById('importSettingsFile');
  const importStatusMsg = document.getElementById('importStatusMsg');
  
  if (importSettingsBtn && importSettingsFile) {
    importSettingsBtn.addEventListener('click', () => {
      importSettingsFile.click();
    });
    
    importSettingsFile.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      
      if (!confirm('Import settings from this file? This will replace your current settings. A backup will be created.')) {
        importSettingsFile.value = '';
        return;
      }
      
      try {
        importStatusMsg.textContent = 'Importing...';
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch('/api/settings/import', {
          method: 'POST',
          body: formData
        });
        
        const data = await response.json();
        
        if (!response.ok || data.error) {
          importStatusMsg.textContent = 'Import failed: ' + (data.error || 'Unknown error');
          setTimeout(() => { importStatusMsg.textContent = ''; }, 5000);
          importSettingsFile.value = '';
          return;
        }
        
        importStatusMsg.textContent = 'Settings imported successfully! Reloading...';
        setTimeout(() => {
          location.reload();
        }, 1000);
        
      } catch (e) {
        console.error('Import error:', e);
        importStatusMsg.textContent = 'Import failed: ' + e.message;
        setTimeout(() => { importStatusMsg.textContent = ''; }, 5000);
      }
      
      importSettingsFile.value = '';
    });
  }

  loadSettings();
});
