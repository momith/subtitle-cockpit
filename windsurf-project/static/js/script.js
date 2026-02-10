document.addEventListener('DOMContentLoaded', function() {
    const fileList = document.getElementById('fileList');
    const breadcrumb = document.getElementById('breadcrumb');
    const selectAll = document.getElementById('selectAll');
    const refreshBtn = document.getElementById('refreshBtn');
    const newFolderBtn = document.getElementById('newFolderBtn');
    const renameBtn = document.getElementById('renameBtn');
    const bulkEditBtn = document.getElementById('bulkEditBtn');
    const deleteBtn = document.getElementById('deleteBtn');
    const deleteFolderBtn = document.getElementById('deleteFolderBtn');
    const downloadBtn = document.getElementById('downloadBtn');
    const uploadBtn = document.getElementById('uploadBtn');
    const uploadFileInput = document.getElementById('uploadFileInput');
    const uploadProgress = document.getElementById('uploadProgress');
    const uploadProgressLabel = document.getElementById('uploadProgressLabel');
    const uploadProgressBar = document.getElementById('uploadProgressBar');
    const uploadProgressText = document.getElementById('uploadProgressText');
    const extractBtn = document.getElementById('extractBtn');
    const supToSrtBtn = document.getElementById('supToSrtBtn');
    const translateBtn = document.getElementById('translateBtn');
    const searchSubtitlesBtn = document.getElementById('searchSubtitlesBtn');
    const syncSubtitlesBtn = document.getElementById('syncSubtitlesBtn');
    const publishSubtitlesBtn = document.getElementById('publishSubtitlesBtn');
    const filterVideoBtn = document.getElementById('filterVideoBtn');
    const filterSubtitleBtn = document.getElementById('filterSubtitleBtn');
    const filterAllBtn = document.getElementById('filterAllBtn');
    const renameModal = document.getElementById('renameModal');
    const renameInput = document.getElementById('renameInput');
    const renameError = document.getElementById('renameError');
    const renameConfirmBtn = document.getElementById('renameConfirmBtn');
    const renameCancelBtn = document.getElementById('renameCancelBtn');
    const renameModalClose = document.getElementById('renameModalClose');

    const bulkRenameModal = document.getElementById('bulkRenameModal');
    const bulkRenameModalClose = document.getElementById('bulkRenameModalClose');
    const bulkRenameCancelBtn = document.getElementById('bulkRenameCancelBtn');
    const bulkRenameConfirmBtn = document.getElementById('bulkRenameConfirmBtn');
    const bulkRenameFind = document.getElementById('bulkRenameFind');
    const bulkRenameReplace = document.getElementById('bulkRenameReplace');
    const bulkRenamePreview = document.getElementById('bulkRenamePreview');
    const bulkRenameError = document.getElementById('bulkRenameError');

    const mkdirModal = document.getElementById('mkdirModal');
    const mkdirModalClose = document.getElementById('mkdirModalClose');
    const mkdirCancelBtn = document.getElementById('mkdirCancelBtn');
    const mkdirConfirmBtn = document.getElementById('mkdirConfirmBtn');
    const mkdirNameInput = document.getElementById('mkdirNameInput');
    const mkdirError = document.getElementById('mkdirError');

    const publishModal = document.getElementById('publishModal');
    const publishModalClose = document.getElementById('publishModalClose');
    const publishCancelBtn = document.getElementById('publishCancelBtn');
    const publishConfirmBtn = document.getElementById('publishConfirmBtn');
    const imdbSearchInput = document.getElementById('imdbSearchInput');
    const imdbSearchResults = document.getElementById('imdbSearchResults');
    const publishTypeSelect = document.getElementById('publishTypeSelect');
    const publishTmdbId = document.getElementById('publishTmdbId');
    const publishImdbId = document.getElementById('publishImdbId');
    const publishTitle = document.getElementById('publishTitle');
    const publishComment = document.getElementById('publishComment');
    const publishTags = document.getElementById('publishTags');
    const publishError = document.getElementById('publishError');
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

    function showBulkRenameError(msg) {
        if (!bulkRenameError) return;
        if (!msg) {
            bulkRenameError.style.display = 'none';
            bulkRenameError.textContent = '';
            return;
        }
        bulkRenameError.textContent = msg;
        bulkRenameError.style.display = 'block';
    }

    function computeBulkRenamePreview(find, replace) {
        const findStr = (find || '');
        const replaceStr = (replace || '');
        const selected = Array.from(selectedFiles);

        if (!bulkRenamePreview) return;
        if (!selected.length) {
            bulkRenamePreview.innerHTML = '';
            return;
        }

        const lines = selected.slice(0, 100).map(p => {
            const parts = (p || '').split('/');
            const oldName = parts[parts.length - 1] || '';
            const newName = findStr ? oldName.split(findStr).join(replaceStr) : oldName;
            if (!findStr) {
                return `${oldName}`;
            }
            return `${oldName} -> ${newName}`;
        });

        let extra = '';
        if (selected.length > 100) {
            extra = `\n... and ${selected.length - 100} more`;
        }

        bulkRenamePreview.textContent = lines.join('\n') + extra;
    }

    function openBulkRenameModal() {
        if (!bulkRenameModal) return;
        showBulkRenameError('');
        if (bulkRenameFind) bulkRenameFind.value = '';
        if (bulkRenameReplace) bulkRenameReplace.value = '';
        computeBulkRenamePreview('', '');
        bulkRenameModal.style.display = 'flex';
        setTimeout(() => bulkRenameFind && bulkRenameFind.focus(), 0);
    }
    
    function removeToast(toast) {
        toast.classList.add('hiding');
        setTimeout(() => {
            toast.remove();
        }, 300);
    }

    function setUploadProgressVisible(visible) {
        if (!uploadProgress) return;
        uploadProgress.style.display = visible ? '' : 'none';
    }

    function updateUploadProgress(label, percent) {
        if (!uploadProgress) return;
        if (uploadProgressLabel) uploadProgressLabel.textContent = label || '';
        const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));
        if (uploadProgressBar) uploadProgressBar.style.width = `${pct}%`;
        if (uploadProgressText) uploadProgressText.textContent = `${pct}%`;
    }

    function uploadFileWithProgress(file, targetPath, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            const url = `/api/upload_raw?path=${encodeURIComponent(targetPath || '')}&filename=${encodeURIComponent(file.name)}`;
            xhr.open('POST', url, true);

            xhr.upload.onprogress = (evt) => {
                if (!evt || !evt.lengthComputable) {
                    onProgress && onProgress(null);
                    return;
                }
                const percent = (evt.loaded / evt.total) * 100;
                onProgress && onProgress(percent);
            };

            xhr.onload = () => {
                let data = null;
                try {
                    data = JSON.parse(xhr.responseText || '{}');
                } catch (e) {
                    data = { error: 'Invalid JSON response from server' };
                }

                resolve({
                    ok: xhr.status >= 200 && xhr.status < 300,
                    status: xhr.status,
                    data
                });
            };

            xhr.onerror = () => reject(new Error('Network error during upload'));
            xhr.onabort = () => reject(new Error('Upload aborted'));

            xhr.setRequestHeader('Content-Type', 'application/octet-stream');
            xhr.send(file);
        });
    }

    if (syncSubtitlesBtn) {
        syncSubtitlesBtn.addEventListener('click', () => {
            if (syncSubtitlesBtn.disabled) return;
            const subtitles = Array.from(selectedFiles);
            (async () => {
                try {
                    const res = await fetch('/api/sync_subtitles', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ paths: subtitles })
                    });
                    const data = await res.json();
                    if (data.error) {
                        showToast(`Sync failed: ${data.error}`, 'error', 7000);
                        return;
                    }
                    showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');
                    setTimeout(() => loadDirectory(currentPath || ''), 2000);
                } catch (e) {
                    console.error('sync subtitles error', e);
                    showToast('Sync failed. See console for details.', 'error');
                }
            })();
        });
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
    function isAss(path){ return getExt(path) === '.ass'; }
    function isSsa(path){ return getExt(path) === '.ssa'; }
    function isIdx(path){ return getExt(path) === '.idx'; }
    function isSubtitle(path){ return isSrt(path) || isSup(path) || isSub(path) || isAss(path) || isSsa(path); }
    function isPublishableSubtitle(path){
        const ext = getExt(path);
        return ext === '.srt' || ext === '.ass' || ext === '.ssa' || ext === '.sub';
    }

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
        const multiSelected = selectedFiles.size > 1;
        deleteBtn.disabled = !anySelected;
        if (renameBtn) {
            renameBtn.disabled = !oneSelected;
        }
        if (bulkEditBtn) {
            bulkEditBtn.disabled = !multiSelected;
        }
        if (deleteFolderBtn) {
            if (!oneSelected) {
                deleteFolderBtn.disabled = true;
            } else {
                const p = Array.from(selectedFiles)[0];
                const it = currentItems.find(x => x.path === p);
                deleteFolderBtn.disabled = !(it && it.is_dir);
            }
        }
        if (downloadBtn) {
            const arr = Array.from(selectedFiles);
            const allAreFiles = anySelected && arr.every(p => {
                const it = currentItems.find(x => x.path === p);
                return it && !it.is_dir;
            });
            downloadBtn.disabled = !allAreFiles;
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

        if (syncSubtitlesBtn) {
            const arr = Array.from(selectedFiles);
            const allSrt = anySelected && arr.every(isSrt);
            syncSubtitlesBtn.disabled = !allSrt;
        }

        if (publishSubtitlesBtn) {
            const arr = Array.from(selectedFiles);
            const allPublishable = anySelected && arr.every(isPublishableSubtitle);
            publishSubtitlesBtn.disabled = !allPublishable;
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
        if (e.key === 'Escape' && publishModal && publishModal.style.display === 'flex') {
            publishModal.style.display = 'none';
        }
        if (e.key === 'Escape' && bulkRenameModal && bulkRenameModal.style.display === 'flex') {
            bulkRenameModal.style.display = 'none';
        }
        if (e.key === 'Escape' && mkdirModal && mkdirModal.style.display === 'flex') {
            mkdirModal.style.display = 'none';
        }
    });

    if (bulkEditBtn) {
        bulkEditBtn.addEventListener('click', () => {
            if (bulkEditBtn.disabled) return;
            openBulkRenameModal();
        });
    }

    if (bulkRenameModalClose) {
        bulkRenameModalClose.addEventListener('click', () => {
            if (bulkRenameModal) bulkRenameModal.style.display = 'none';
        });
    }

    if (bulkRenameCancelBtn) {
        bulkRenameCancelBtn.addEventListener('click', () => {
            if (bulkRenameModal) bulkRenameModal.style.display = 'none';
        });
    }

    function onBulkRenameInputChange() {
        computeBulkRenamePreview(
            bulkRenameFind ? bulkRenameFind.value : '',
            bulkRenameReplace ? bulkRenameReplace.value : ''
        );
    }

    if (bulkRenameFind) bulkRenameFind.addEventListener('input', onBulkRenameInputChange);
    if (bulkRenameReplace) bulkRenameReplace.addEventListener('input', onBulkRenameInputChange);

    if (bulkRenameConfirmBtn) {
        bulkRenameConfirmBtn.addEventListener('click', async () => {
            if (!bulkRenameModal) return;
            const findStr = (bulkRenameFind ? bulkRenameFind.value : '').trim();
            const replaceStr = (bulkRenameReplace ? bulkRenameReplace.value : '');
            const selected = Array.from(selectedFiles);

            showBulkRenameError('');

            if (selected.length < 2) {
                showBulkRenameError('Select at least 2 files.');
                return;
            }
            if (!findStr) {
                showBulkRenameError('Find cannot be empty.');
                return;
            }

            try {
                const res = await fetch('/api/bulk_rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        paths: selected,
                        find: findStr,
                        replace: replaceStr
                    })
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    showBulkRenameError(data.error || 'Bulk rename failed.');
                    return;
                }

                const renamed = data.renamed || 0;
                const skipped = data.skipped || 0;
                const failed = data.failed || 0;

                showToast(`Bulk rename: renamed ${renamed}, skipped ${skipped}, failed ${failed}.`, failed ? 'warning' : 'success');
                bulkRenameModal.style.display = 'none';
                selectedFiles.clear();
                loadDirectory(currentPath || '');
            } catch (e) {
                console.error('Bulk rename error', e);
                showBulkRenameError('Network error. Please try again.');
            }
        });
    }

    function showPublishError(msg){
        if (!publishError) return;
        if (!msg) {
            publishError.style.display = 'none';
            publishError.textContent = '';
            return;
        }
        publishError.textContent = msg;
        publishError.style.display = 'block';
    }

    function openPublishModal(){
        if (!publishModal) return;
        showPublishError('');
        if (imdbSearchResults) {
            imdbSearchResults.style.display = 'none';
            imdbSearchResults.innerHTML = '';
        }
        if (imdbSearchInput) imdbSearchInput.value = '';
        if (publishTmdbId) publishTmdbId.value = '';
        if (publishImdbId) publishImdbId.value = '';
        if (publishTitle) publishTitle.value = '';
        if (publishComment) publishComment.value = '';
        if (publishTags) publishTags.value = '';
        if (publishTypeSelect) publishTypeSelect.value = 'movie';
        publishModal.style.display = 'flex';
        setTimeout(() => imdbSearchInput && imdbSearchInput.focus(), 0);
    }

    async function fetchImdbSuggest(q){
        const res = await fetch(`/api/imdb_suggest?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'IMDb suggest failed');
        return data.results || [];
    }

    function renderImdbResults(results){
        if (!imdbSearchResults) return;
        if (!Array.isArray(results) || results.length === 0) {
            imdbSearchResults.style.display = 'none';
            imdbSearchResults.innerHTML = '';
            return;
        }
        imdbSearchResults.innerHTML = '';
        results.slice(0, 15).forEach(r => {
            const row = document.createElement('div');
            row.style.padding = '8px 10px';
            row.style.cursor = 'pointer';
            row.style.borderBottom = '1px solid #e1e4e8';
            const year = r.year ? ` (${r.year})` : '';
            const kind = r.kind ? ` - ${r.kind}` : '';
            row.textContent = `${r.title}${year}${kind}`;
            row.addEventListener('mouseenter', () => { row.style.background = '#f0f4f8'; });
            row.addEventListener('mouseleave', () => { row.style.background = '#fff'; });
            row.addEventListener('click', () => {
                if (publishImdbId) publishImdbId.value = r.imdb_id || '';
                if (publishTitle) publishTitle.value = r.title || '';
                if (publishTypeSelect) {
                    const k = (r.kind || '').toLowerCase();
                    const isTv = k.includes('tv') || k.includes('series') || k.includes('episode');
                    publishTypeSelect.value = isTv ? 'tv' : 'movie';
                }
                imdbSearchResults.style.display = 'none';
            });
            imdbSearchResults.appendChild(row);
        });
        imdbSearchResults.style.display = 'block';
    }

    let imdbSuggestTimer = null;
    if (imdbSearchInput) {
        imdbSearchInput.addEventListener('input', () => {
            const q = (imdbSearchInput.value || '').trim();
            if (imdbSuggestTimer) clearTimeout(imdbSuggestTimer);
            if (q.length < 2) {
                renderImdbResults([]);
                return;
            }
            imdbSuggestTimer = setTimeout(async () => {
                try {
                    const results = await fetchImdbSuggest(q);
                    renderImdbResults(results);
                } catch (e) {
                    console.error('imdb suggest error', e);
                    renderImdbResults([]);
                }
            }, 250);
        });
    }

    if (publishModalClose) {
        publishModalClose.addEventListener('click', () => {
            if (publishModal) publishModal.style.display = 'none';
        });
    }
    if (publishCancelBtn) {
        publishCancelBtn.addEventListener('click', () => {
            if (publishModal) publishModal.style.display = 'none';
        });
    }

    if (publishSubtitlesBtn) {
        publishSubtitlesBtn.addEventListener('click', () => {
            if (publishSubtitlesBtn.disabled) return;
            openPublishModal();
        });
    }

    if (publishConfirmBtn) {
        publishConfirmBtn.addEventListener('click', async () => {
            if (!publishModal) return;
            const subtitles = Array.from(selectedFiles);
            const type = (publishTypeSelect ? publishTypeSelect.value : 'movie') || 'movie';
            const tmdbId = (publishTmdbId ? publishTmdbId.value : '').trim();
            const imdbId = (publishImdbId ? publishImdbId.value : '').trim();
            const title = (publishTitle ? publishTitle.value : '').trim();
            const comment = (publishComment ? publishComment.value : '').trim();
            const tagsRaw = (publishTags ? publishTags.value : '');
            const tags = (tagsRaw || '').split(',').map(s => s.trim()).filter(Boolean);

            if (!tmdbId && !imdbId) {
                showPublishError('Please provide TMDB ID or IMDb ID.');
                return;
            }
            showPublishError('');
            try {
                const res = await fetch('/api/publish_subtitles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        paths: subtitles,
                        target: { type, tmdb_id: tmdbId, imdb_id: imdbId, title },
                        comment,
                        tags
                    })
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    showPublishError(data.error || 'Publish request failed.');
                    return;
                }
                showToast(`${data.message || 'Jobs added to queue'}. View progress in the Job Queue page.`, 'success');
                publishModal.style.display = 'none';
            } catch (e) {
                console.error('publish subtitles error', e);
                showPublishError('Network error. Please try again.');
            }
        });
    }

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

    if (deleteFolderBtn) {
        deleteFolderBtn.addEventListener('click', async () => {
            if (deleteFolderBtn.disabled || selectedFiles.size !== 1) return;
            const folderPath = Array.from(selectedFiles)[0];
            const item = currentItems.find(x => x.path === folderPath);
            if (!item || !item.is_dir) return;

            const folderName = folderPath.split('/').filter(Boolean).pop() || folderPath;
            const ok = confirm(`Delete folder "${folderName}" and ALL of its contents?`);
            if (!ok) return;

            try {
                const res = await fetch('/api/delete_folder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: folderPath })
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    showToast(`Delete folder failed: ${data.error || 'Unknown error'}`, 'error', 6000);
                    return;
                }
                showToast(`Deleted folder: ${folderName}`, 'success', 3000);
                selectedFiles.clear();
                loadDirectory(currentPath || '');
            } catch (e) {
                console.error('Delete folder error', e);
                showToast('Failed to delete folder. See console for details.', 'error', 6000);
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
            const thresholdBytes = 500 * 1024;
            const largeSrts = srts
                .map(p => ({
                    path: p,
                    item: currentItems.find(it => it.path === p)
                }))
                .filter(x => x.item && typeof x.item.size === 'number' && x.item.size >= thresholdBytes);
            if (largeSrts.length > 0) {
                const list = largeSrts
                    .slice(0, 10)
                    .map(x => `- ${x.path} (${formatFileSize(x.item.size)})`)
                    .join('\n');
                const suffix = largeSrts.length > 10 ? `\n...and ${largeSrts.length - 10} more.` : '';
                const ok = confirm(
                    `One or more selected SRT files are large (>= ${formatFileSize(thresholdBytes)}).\n\n` +
                    `Large files may take a long time to translate and can consume a lot of API quota.\n\n` +
                    `${list}${suffix}\n\nProceed and add to the translation queue?`
                );
                if (!ok) return;
            }
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
            if (downloadBtn.disabled || selectedFiles.size === 0) return;

            const selected = Array.from(selectedFiles);
            const isBulk = selected.length > 1;
            const filePath = selected[0];
            const fileName = filePath.split('/').pop();
            const downloadName = isBulk ? 'download.zip' : fileName;
            
            try {
                showToast(`Downloading ${isBulk ? selected.length + ' files' : fileName}...`, 'info', 3000);

                const response = await fetch(isBulk ? '/api/download_bulk' : '/api/download', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(isBulk ? { paths: selected } : { path: filePath })
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
                a.download = downloadName;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                
                showToast(`Downloaded ${downloadName} successfully`, 'success', 3000);
                
            } catch (e) {
                console.error('Download error:', e);
                showToast(`Download failed: ${e.message}`, 'error', 5000);
            }
        });
    }

    function showMkdirError(msg) {
        if (!mkdirError) return;
        if (!msg) {
            mkdirError.style.display = 'none';
            mkdirError.textContent = '';
            return;
        }
        mkdirError.textContent = msg;
        mkdirError.style.display = 'block';
    }

    function openMkdirModal() {
        if (!mkdirModal) return;
        showMkdirError('');
        if (mkdirNameInput) mkdirNameInput.value = '';
        mkdirModal.style.display = 'flex';
        setTimeout(() => mkdirNameInput && mkdirNameInput.focus(), 0);
    }

    if (newFolderBtn) {
        newFolderBtn.addEventListener('click', () => {
            openMkdirModal();
        });
    }

    if (mkdirModalClose) {
        mkdirModalClose.addEventListener('click', () => {
            if (mkdirModal) mkdirModal.style.display = 'none';
        });
    }

    if (mkdirCancelBtn) {
        mkdirCancelBtn.addEventListener('click', () => {
            if (mkdirModal) mkdirModal.style.display = 'none';
        });
    }

    if (mkdirConfirmBtn) {
        mkdirConfirmBtn.addEventListener('click', async () => {
            const name = (mkdirNameInput ? mkdirNameInput.value : '').trim();
            showMkdirError('');
            if (!name) {
                showMkdirError('Folder name cannot be empty.');
                return;
            }
            try {
                const res = await fetch('/api/mkdir', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        path: currentPath || '',
                        name
                    })
                });
                const data = await res.json();
                if (!res.ok || data.error) {
                    showMkdirError(data.error || 'Failed to create folder.');
                    return;
                }
                showToast(`Created folder: ${name}`, 'success', 3000);
                if (mkdirModal) mkdirModal.style.display = 'none';
                loadDirectory(currentPath || '');
            } catch (e) {
                console.error('mkdir error', e);
                showMkdirError('Network error. Please try again.');
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

                setUploadProgressVisible(true);
                
                // Upload files sequentially
                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    
                    try {
                        uploadBtn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Uploading ${i + 1}/${totalFiles}...`;

                        updateUploadProgress(`Uploading ${file.name} (${i + 1}/${totalFiles})`, 0);

                        const result = await uploadFileWithProgress(file, currentPath, (pct) => {
                            if (pct === null) {
                                updateUploadProgress(`Uploading ${file.name} (${i + 1}/${totalFiles})`, 0);
                                return;
                            }
                            updateUploadProgress(`Uploading ${file.name} (${i + 1}/${totalFiles})`, pct);
                        });

                        const data = result.data;
                        const ok = result.ok;

                        updateUploadProgress(`Uploading ${file.name} (${i + 1}/${totalFiles})`, 100);

                        if (!ok || data.error) {
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
                setUploadProgressVisible(false);
                updateUploadProgress('', 0);
                uploadInProgress = false;
                uploadBtn.disabled = false;
                uploadBtn.innerHTML = '<i class="fas fa-upload"></i> Upload';
                uploadFileInput.value = '';
            }
        });
    }

    // Initialize: load settings then try to restore last visited directory
    loadSettings().then(() => {
        loadCachedOrHomeDirectory();
    });
});
