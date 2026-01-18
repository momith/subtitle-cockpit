document.addEventListener('DOMContentLoaded', function() {
    const fileList = document.getElementById('fileList');
    const breadcrumb = document.getElementById('breadcrumb');
    const selectAll = document.getElementById('selectAll');
    const refreshBtn = document.getElementById('refreshBtn');
    const renameBtn = document.getElementById('renameBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const downloadBtn = document.getElementById('downloadBtn');
    const uploadBtn = document.getElementById('uploadBtn');
    const uploadFileInput = document.getElementById('uploadFileInput');
    const extractBtn = document.getElementById('extractBtn');
    const translateBtn = document.getElementById('translateBtn');
    const supToSrtBtn = document.getElementById('supToSrtBtn');
    const searchSubtitlesBtn = document.getElementById('searchSubtitlesBtn');
    const filterVideoBtn = document.getElementById('filterVideoBtn');
    const filterSubtitleBtn = document.getElementById('filterSubtitleBtn');
    const filterAllBtn = document.getElementById('filterAllBtn');
    const renameModal = document.getElementById('renameModal');
    const renameInput = document.getElementById('renameInput');
    const renameError = document.getElementById('renameError');
    const renameConfirmBtn = document.getElementById('renameConfirmBtn');
    const renameCancelBtn = document.getElementById('renameCancelBtn');
    const renameModalClose = document.getElementById('renameModalClose');
    let currentPath = '';
    let selectedFiles = new Set();
    let currentItems = [];
    let currentFilter = 'all'; // 'all', 'video', 'subtitle'
    let excludedFileTypes = new Set();
    let appSettings = {};
    let uploadInProgress = false;

    // Toast notification system
    function showToast(message, type = 'info', duration = 5000) {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };
        
        const titles = {
            success: 'Success',
            error: 'Error',
            warning: 'Warning',
            info: 'Info'
        };
        
        toast.innerHTML = `
            <i class="fas ${icons[type]} toast-icon"></i>
            <div class="toast-content">
                <div class="toast-title">${titles[type]}</div>
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close">&times;</button>
        `;
        
        container.appendChild(toast);
        
        // Close button
        toast.querySelector('.toast-close').addEventListener('click', () => {
            removeToast(toast);
        });
        
        // Auto-dismiss
        setTimeout(() => {
            removeToast(toast);
        }, duration);
    }
    
    function removeToast(toast) {
        toast.classList.add('hiding');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }

    const videoExts = new Set(['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpeg', '.mpg']);
    function getExt(p){
        const i = p.lastIndexOf('.');
        return i >= 0 ? p.slice(i).toLowerCase() : '';
    }
    function isVideo(path){ return videoExts.has(getExt(path)); }
    function isSrt(path){ return getExt(path) === '.srt'; }
    function isSup(path){ return getExt(path) === '.sup'; }
    function isSub(path){ return getExt(path) === '.sub'; }
    function isIdx(path){ return getExt(path) === '.idx'; }
    function isSubtitle(path){ return isSrt(path) || isSup(path) || isSub(path); }

    // Load app settings to get excluded file types
    async function loadSettings() {
        try {
            const res = await fetch('/api/settings');
            const data = await res.json();
            appSettings = data.settings || {};
            const excluded = (appSettings.excluded_file_types || '').split(',').map(e => e.trim().toLowerCase()).filter(e => e);
            excludedFileTypes = new Set(excluded);
        } catch (e) {
            console.error('Failed to load settings:', e);
        }
    }

    // Filter items based on current filter and exclusions
    function filterItems(items) {
        return items.filter(item => {
            // Always show directories
            if (item.is_dir) return true;
            
            // Check exclusions
            const ext = getExt(item.name);
            if (excludedFileTypes.has(ext)) return false;
            
            // Apply current filter
            if (currentFilter === 'video') return isVideo(item.name);
            if (currentFilter === 'subtitle') return isSubtitle(item.name);
            return true; // 'all'
        });
    }

    // Cache the current directory in sessionStorage
    function cacheCurrentDirectory(path) {
        try {
            sessionStorage.setItem('lastVisitedDirectory', path);
        } catch (e) {
            console.warn('Failed to cache directory:', e);
        }
    }
    
    // Get cached directory from sessionStorage
    function getCachedDirectory() {
        try {
            return sessionStorage.getItem('lastVisitedDirectory');
        } catch (e) {
            console.warn('Failed to retrieve cached directory:', e);
            return null;
        }
    }

    // Initialize the file explorer
    function loadDirectory(path = '') {
        fetch(`/api/list?path=${encodeURIComponent(path)}`)
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    console.error('Error:', data.error);
                    return;
                }
                
                currentPath = data.path;
                // Cache the current directory
                cacheCurrentDirectory(currentPath);
                // Clear selection when changing directories
                selectedFiles.clear();
                updateBreadcrumb(currentPath);
                currentItems = data.items || [];
                const filteredItems = filterItems(currentItems);
                renderFileList(filteredItems, data.parent);
                updateActionButton();
                updateSelectAllState();
            })
            .catch(error => console.error('Error loading directory:', error));
    }
    
    // Try to load cached directory, fallback to home if not found
    function loadCachedOrHomeDirectory() {
        const cached = getCachedDirectory();
        
        if (cached) {
            // Try to load cached directory
            fetch(`/api/list?path=${encodeURIComponent(cached)}`)
                .then(response => response.json())
                .then(data => {
                    if (data.error) {
                        // Cached directory not found, load home
                        loadDirectory('');
                    } else {
                        // Successfully loaded cached directory
                        currentPath = data.path;
                        cacheCurrentDirectory(currentPath);
                        selectedFiles.clear();
                        updateBreadcrumb(currentPath);
                        currentItems = data.items || [];
                        const filteredItems = filterItems(currentItems);
                        renderFileList(filteredItems, data.parent);
                        updateActionButton();
                        updateSelectAllState();
                    }
                })
                .catch(error => {
                    console.error('Error loading cached directory:', error);
                    loadDirectory('');
                });
        } else {
            // No cached directory, load home
            loadDirectory('');
        }
    }

    // Render the file list
    function renderFileList(items, parentPath) {
        fileList.innerHTML = '';
        
        // Add parent directory link if not at root
        if (currentPath) {
            const parentItem = document.createElement('div');
            parentItem.className = 'file-item';
            parentItem.innerHTML = `
                <div class="file-icon"><i class="fas fa-folder"></i></div>
                <div class="file-name">..</div>
                <div class="file-size"></div>
            `;
            parentItem.addEventListener('click', (e) => {
                e.stopPropagation();
                loadDirectory(parentPath || '');
            });
            fileList.appendChild(parentItem);
        }
        
        // Add files and directories
        items.forEach(item => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-item';
            fileItem.dataset.path = item.path;
            fileItem.dataset.isDir = item.is_dir;
            
            // Determine icon
            let icon = 'fa-file';
            if (item.is_dir) {
                icon = 'fa-folder';
            } else if (isVideo(item.name)) {
                icon = 'fa-film';
                fileItem.classList.add('video-file'); // Add class for highlighting
            } else if (isSrt(item.name) || isSup(item.name) || isSub(item.name) || isIdx(item.name)) {
                icon = 'fa-closed-captioning';
            }
            
            const size = item.is_dir ? '' : formatFileSize(item.size);
            
            fileItem.innerHTML = `
                <div class="checkbox-container">
                    <input type="checkbox" class="file-checkbox" data-path="${item.path}">
                </div>
                <div class="file-icon"><i class="fas ${icon}"></i></div>
                <div class="file-name">${escapeHtml(item.name)}</div>
                <div class="file-size">${size}</div>
            `;
            
            // Handle folder click - navigate
            if (item.is_dir) {
                fileItem.addEventListener('click', (e) => {
                    if (!e.target.classList.contains('file-checkbox')) {
                        loadDirectory(item.path);
                    }
                });
            } else {
                // Handle file click - toggle selection (entire row clickable)
                fileItem.addEventListener('click', (e) => {
                    if (!e.target.classList.contains('file-checkbox')) {
                        const checkbox = fileItem.querySelector('.file-checkbox');
                        checkbox.checked = !checkbox.checked;
                        toggleFileSelection(item.path, checkbox.checked);
                        updateSelectAllState();
                    }
                });
            }
            
            // Handle checkbox click
            const checkbox = fileItem.querySelector('.file-checkbox');
            checkbox.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleFileSelection(item.path, checkbox.checked);
                updateSelectAllState();
            });
            
            // Update checkbox state if file was previously selected
            if (selectedFiles.has(item.path)) {
                checkbox.checked = true;
                fileItem.classList.add('selected');
            }
            
            fileList.appendChild(fileItem);
        });
        
        updateActionButton();
        updateSelectAllState();
    }
    
    // Update breadcrumb navigation
    function updateBreadcrumb(path) {
        if (!path) {
            breadcrumb.innerHTML = '<a href="#" data-path="">Home</a>';
            return;
        }
        
        const parts = path.split('/').filter(Boolean);
        let breadcrumbHtml = '<a href="#" data-path="">Home</a>';
        let currentPath = '';
        
        parts.forEach((part, index) => {
            currentPath += (currentPath ? '/' : '') + part;
            const isLast = index === parts.length - 1;
            
            breadcrumbHtml += ` <span class="breadcrumb-separator">/</span> `;
            
            if (isLast) {
                breadcrumbHtml += `<span>${escapeHtml(part)}</span>`;
            } else {
                breadcrumbHtml += `<a href="#" data-path="${currentPath}">${escapeHtml(part)}</a>`;
            }
        });
        
        breadcrumb.innerHTML = breadcrumbHtml;
        
        // Add click handlers to breadcrumb links
        breadcrumb.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                loadDirectory(link.dataset.path);
            });
        });
    }
    
    // Toggle file selection
    function toggleFileSelection(path, isSelected) {
        const fileItem = document.querySelector(`.file-item[data-path="${path}"]`);
        
        if (isSelected) {
            selectedFiles.add(path);
            if (fileItem) fileItem.classList.add('selected');
        } else {
            selectedFiles.delete(path);
            if (fileItem) fileItem.classList.remove('selected');
        }
        
        updateActionButton();
    }
    
    // Update action button state
    function updateActionButton() {
        const anySelected = selectedFiles.size > 0;
        const oneSelected = selectedFiles.size === 1;
        deleteBtn.disabled = !anySelected;
        if (renameBtn) {
            renameBtn.disabled = !oneSelected;
        }
        if (downloadBtn) {
            downloadBtn.disabled = !oneSelected;
        }
        if (extractBtn) {
            const arr = Array.from(selectedFiles);
            const allVideo = anySelected && arr.every(isVideo);
            extractBtn.disabled = !allVideo;
        }
        if (translateBtn) {
            const arr = Array.from(selectedFiles);
            const allSrt = anySelected && arr.every(isSrt);
            translateBtn.disabled = !allSrt;
        }
        if (supToSrtBtn) {
            const arr = Array.from(selectedFiles);
            const allSupOrSub = anySelected && arr.every(f => isSup(f) || isSub(f));
            supToSrtBtn.disabled = !allSupOrSub;
        }
        if (searchSubtitlesBtn) {
            const arr = Array.from(selectedFiles);
            const allVideo = anySelected && arr.every(isVideo);
            searchSubtitlesBtn.disabled = !allVideo;
        }
    }

    // Update Select All checkbox state
    function updateSelectAllState() {
        if (!selectAll) return;
        const fileCheckboxes = Array.from(document.querySelectorAll('.file-checkbox'))
            .filter(cb => (document.querySelector(`.file-item[data-path="${cb.dataset.path}"]`)?.dataset.isDir === 'false'));
        if (fileCheckboxes.length === 0) {
            selectAll.checked = false;
            selectAll.indeterminate = false;
            return;
        }
        const checkedCount = fileCheckboxes.filter(cb => cb.checked).length;
        selectAll.checked = checkedCount === fileCheckboxes.length;
        selectAll.indeterminate = checkedCount > 0 && checkedCount < fileCheckboxes.length;
    }

    // Format file size
    function formatFileSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }
    
    // Helper to escape HTML
    function escapeHtml(unsafe) {
        return unsafe
            .toString()
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
    
    // no-op: removed Process Selected button
    
    // Handle Select All toggle (only files, not folders)
    if (selectAll) {
        selectAll.addEventListener('change', () => {
            const select = selectAll.checked;
            currentItems.forEach(item => {
                if (!item.is_dir) {
                    const cb = document.querySelector(`input.file-checkbox[data-path="${item.path}"]`);
                    if (cb) {
                        cb.checked = select;
                        toggleFileSelection(item.path, select);
                    }
                }
            });
            updateSelectAllState();
        });
    }

    // Handle Refresh
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            loadDirectory(currentPath || '');
        });
    }

    // Handle Rename/Move
    if (renameBtn) {
        renameBtn.addEventListener('click', () => {
            if (selectedFiles.size !== 1) return;
            const selectedPath = Array.from(selectedFiles)[0];
            
            // Get base directory from settings
            const baseDir = appSettings.root_dir || '/media/';
            const fullPath = baseDir + (baseDir.endsWith('/') ? '' : '/') + selectedPath;
            
            // Show modal
            renameInput.value = fullPath;
            renameError.style.display = 'none';
            renameModal.style.display = 'flex';
            renameInput.focus();
            renameInput.select();
        });
    }

    // Handle rename modal close
    if (renameModalClose) {
        renameModalClose.addEventListener('click', () => {
            renameModal.style.display = 'none';
        });
    }

    if (renameCancelBtn) {
        renameCancelBtn.addEventListener('click', () => {
            renameModal.style.display = 'none';
        });
    }

    // Handle rename confirm
    if (renameConfirmBtn) {
        renameConfirmBtn.addEventListener('click', async () => {
            if (selectedFiles.size !== 1) return;
            
            const selectedPath = Array.from(selectedFiles)[0];
            const baseDir = appSettings.root_dir || '/media/';
            const oldFullPath = baseDir + (baseDir.endsWith('/') ? '' : '/') + selectedPath;
            const newFullPath = renameInput.value.trim();
            
            // Validation
            if (!newFullPath) {
                renameError.textContent = 'Path cannot be empty.';
                renameError.style.display = 'block';
                return;
            }
            
            if (newFullPath === oldFullPath) {
                renameError.textContent = 'New path is the same as the old path.';
                renameError.style.display = 'block';
                return;
            }
            
            // Call API
            try {
                const res = await fetch('/api/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        old_path: oldFullPath,
                        new_path: newFullPath
                    })
                });
                
                const data = await res.json();
                
                if (!res.ok || data.error) {
                    renameError.textContent = data.error || 'Rename/move failed.';
                    renameError.style.display = 'block';
                    return;
                }
                
                // Success
                showToast(data.message || 'File renamed/moved successfully.', 'success');
                renameModal.style.display = 'none';
                selectedFiles.clear();
                loadDirectory(currentPath || '');
                
            } catch (e) {
                console.error('Rename error', e);
                renameError.textContent = 'Network error. Please try again.';
                renameError.style.display = 'block';
            }
        });
    }

    // Close modal on ESC key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && renameModal.style.display === 'flex') {
            renameModal.style.display = 'none';
        }
    });

    // Handle Delete
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async () => {
            if (selectedFiles.size === 0) return;
            const toDelete = Array.from(selectedFiles);
            const confirmDelete = confirm(`Delete ${toDelete.length} selected item(s)? Files only; folders will be skipped.`);
            if (!confirmDelete) return;
            try {
                const res = await fetch('/api/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paths: toDelete })
                });
                const data = await res.json();
                console.log('Delete results', data);
                // Refresh view
                selectedFiles.clear();
                loadDirectory(currentPath || '');
            } catch (e) {
                console.error('Delete error', e);
                showToast('Failed to delete some items. Check console for details.', 'error');
            }
        });
    }

    // New toolbar actions (placeholder handlers)
    if (extractBtn) {
        extractBtn.addEventListener('click', () => {
            if (extractBtn.disabled) return;
            const vids = Array.from(selectedFiles);
            (async () => {
                try {
                    const res = await fetch('/api/extract_subtitles', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paths: vids })
                    });
                    const data = await res.json();
                    if (data.error) {
                        showToast(`Extraction failed: ${data.error}`, 'error', 7000);
                        return;
                    }
                    const jobCount = data.jobs ? data.jobs.length : 0;
                    showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');
                    // Optionally refresh directory
                    setTimeout(() => loadDirectory(currentPath || ''), 1000);
                } catch (e) {
                    console.error('extract error', e);
                    showToast('Extraction failed. See console for details.', 'error');
                }
            })();
        });
    }
    if (translateBtn) {
        translateBtn.addEventListener('click', () => {
            if (translateBtn.disabled) return;
            const srts = Array.from(selectedFiles);
            (async () => {
                try {
                    const res = await fetch('/api/translate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paths: srts })
                    });
                    const data = await res.json();
                    if (data.error) {
                        showToast(`Translation failed: ${data.error}`, 'error', 7000);
                        return;
                    }
                    const jobCount = data.jobs ? data.jobs.length : 0;
                    showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');
                    // Optionally refresh directory
                    setTimeout(() => loadDirectory(currentPath || ''), 1000);
                } catch (e) {
                    console.error('translate error', e);
                    showToast('Translation failed. See console for details.', 'error');
                }
            })();
        });
    }

    if (supToSrtBtn) {
        supToSrtBtn.addEventListener('click', () => {
            if (supToSrtBtn.disabled) return;
            const sups = Array.from(selectedFiles);
            (async () => {
                try {
                    const res = await fetch('/api/sup_to_srt', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paths: sups })
                    });
                    const data = await res.json();
                    if (data.error) {
                        showToast(`SUP/SUB->SRT failed: ${data.error}`, 'error', 7000);
                        return;
                    }
                    const jobCount = data.jobs ? data.jobs.length : 0;
                    showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');
                    
                    // Show warnings if any .sub files were skipped due to missing .idx
                    if (data.warnings && data.warnings.length > 0) {
                        data.warnings.forEach(warn => {
                            showToast(`${warn.path}: ${warn.message}`, 'warning', 7000);
                        });
                    }
                    
                    // Optionally refresh directory
                    setTimeout(() => loadDirectory(currentPath || ''), 1000);
                } catch (e) {
                    console.error('sup_to_srt error', e);
                    showToast('SUP->SRT failed. See console for details.', 'error');
                }
            })();
        });
    }

    if (searchSubtitlesBtn) {
        searchSubtitlesBtn.addEventListener('click', () => {
            if (searchSubtitlesBtn.disabled) return;
            const videos = Array.from(selectedFiles);
            (async () => {
                try {
                    const res = await fetch('/api/search_subtitles', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paths: videos })
                    });
                    const data = await res.json();
                    if (data.error) {
                        showToast(`Subtitle search failed: ${data.error}`, 'error', 7000);
                        return;
                    }
                    const jobCount = data.jobs ? data.jobs.length : 0;
                    showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');

                    // Refresh directory after a short delay to show any new subtitles
                    setTimeout(() => loadDirectory(currentPath || ''), 2000);
                } catch (e) {
                    console.error('subtitle search error', e);
                    showToast('Subtitle search failed. See console for details.', 'error');
                }
            })();
        });
    }

    // Filter button handlers
    if (filterAllBtn) {
        filterAllBtn.addEventListener('click', () => {
            currentFilter = 'all';
            filterAllBtn.classList.add('active');
            filterVideoBtn?.classList.remove('active');
            filterSubtitleBtn?.classList.remove('active');
            const filteredItems = filterItems(currentItems);
            renderFileList(filteredItems, '');
        });
    }
    
    if (filterVideoBtn) {
        filterVideoBtn.addEventListener('click', () => {
            currentFilter = 'video';
            filterVideoBtn.classList.add('active');
            filterAllBtn?.classList.remove('active');
            filterSubtitleBtn?.classList.remove('active');
            const filteredItems = filterItems(currentItems);
            renderFileList(filteredItems, '');
        });
    }
    
    if (filterSubtitleBtn) {
        filterSubtitleBtn.addEventListener('click', () => {
            currentFilter = 'subtitle';
            filterSubtitleBtn.classList.add('active');
            filterAllBtn?.classList.remove('active');
            filterVideoBtn?.classList.remove('active');
            const filteredItems = filterItems(currentItems);
            renderFileList(filteredItems, '');
        });
    }

    // Download button handler
    if (downloadBtn) {
        downloadBtn.addEventListener('click', async () => {
            if (downloadBtn.disabled || selectedFiles.size !== 1) return;
            
            const filePath = Array.from(selectedFiles)[0];
            const fileName = filePath.split('/').pop();
            
            try {
                showToast(`Downloading ${fileName}...`, 'info', 3000);
                
                const response = await fetch('/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: filePath })
                });
                
                if (!response.ok) {
                    const data = await response.json();
                    showToast(`Download failed: ${data.error || 'Unknown error'}`, 'error', 5000);
                    return;
                }
                
                // Create blob and download
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                
                showToast(`Downloaded ${fileName} successfully`, 'success', 3000);
                
            } catch (e) {
                console.error('Download error:', e);
                showToast(`Download failed: ${e.message}`, 'error', 5000);
            }
        });
    }

    // Upload button handler
    if (uploadBtn && uploadFileInput) {
        uploadBtn.addEventListener('click', () => {
            if (uploadInProgress) {
                showToast('Upload already in progress', 'warning', 3000);
                return;
            }
            uploadFileInput.click();
        });
        
        uploadFileInput.addEventListener('change', async (e) => {
            const files = Array.from(e.target.files);
            if (files.length === 0) return;
            
            if (uploadInProgress) {
                showToast('Upload already in progress', 'warning', 3000);
                uploadFileInput.value = '';
                return;
            }
            
            try {
                uploadInProgress = true;
                uploadBtn.disabled = true;
                uploadBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading...';
                
                const totalFiles = files.length;
                let successCount = 0;
                let failCount = 0;
                const errors = [];
                
                showToast(`Uploading ${totalFiles} file(s)...Do not leave this page!`, 'info', 6000);
                
                // Upload files sequentially
                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    
                    try {
                        uploadBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Uploading ${i + 1}/${totalFiles}...`;
                        
                        const formData = new FormData();
                        formData.append('file', file);
                        formData.append('path', currentPath);
                        
                        const response = await fetch('/api/upload', {
                            method: 'POST',
                            body: formData
                        });
                        
                        const data = await response.json();
                        
                        if (!response.ok || data.error) {
                            failCount++;
                            errors.push(`${file.name}: ${data.error || 'Unknown error'}`);
                        } else {
                            successCount++;
                        }
                        
                    } catch (err) {
                        failCount++;
                        errors.push(`${file.name}: ${err.message}`);
                    }
                }
                
                // Show summary
                if (failCount === 0) {
                    showToast(`Successfully uploaded ${successCount} file(s)`, 'success', 3000);
                } else if (successCount === 0) {
                    showToast(`Failed to upload all ${failCount} file(s)`, 'error', 5000);
                    console.error('Upload errors:', errors);
                } else {
                    showToast(`Uploaded ${successCount} file(s), ${failCount} failed`, 'warning', 5000);
                    console.error('Upload errors:', errors);
                }
                
                // Refresh directory to show new files
                if (successCount > 0) {
                    setTimeout(() => {
                        loadDirectory(currentPath || '');
                    }, 500);
                }
                
            } catch (e) {
                console.error('Upload error:', e);
                showToast(`Upload failed: ${e.message}`, 'error', 5000);
            } finally {
                uploadInProgress = false;
                uploadBtn.disabled = false;
                uploadBtn.innerHTML = '<i class="fas fa-upload"></i> Upload Files';
                uploadFileInput.value = '';
            }
        });
    }

    // Initialize: load settings then try to restore last visited directory
    loadSettings().then(() => {
        loadCachedOrHomeDirectory();
    });
});
