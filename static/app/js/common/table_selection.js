(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof window !== 'undefined') window.AppTableSelection = controller;
    if (typeof module === 'object' && module.exports) module.exports = controller;
}(function () {
    'use strict';

    function configurationDownloadHref(zipHref, selectedIds) {
        if (!selectedIds.length) return '';
        const url = new URL(zipHref, typeof window !== 'undefined' ? window.location.href : undefined);
        url.search = '';
        url.searchParams.set('target_ids', selectedIds.join(','));
        return url.toString();
    }

    function configurationDownloadState(zipHref, selectedIds) {
        return {
            href: configurationDownloadHref(zipHref, selectedIds),
            disabled: selectedIds.length === 0,
            label: selectedIds.length ? `下载所选配置（${selectedIds.length}）` : '请先选择设备',
        };
    }

    function updateConfigurationDownload(button, targets) {
        if (!button) return;
        const ids = targets.filter(target => target.checked).map(target => target.value);
        const state = configurationDownloadState(button.href, ids);
        if (state.href) button.href = state.href;
        button.textContent = state.label;
        button.classList.toggle('disabled', state.disabled);
        button.setAttribute('aria-disabled', String(state.disabled));
        if (state.disabled) button.setAttribute('tabindex', '-1');
        else button.removeAttribute('tabindex');
    }

    function updateTargetSelection(targets, action) {
        targets.filter(target => !target.disabled).forEach(target => {
            target.checked = action === 'invert' ? !target.checked : action === 'all';
        });
    }

    function configurationFilename(disposition) {
        const encoded = String(disposition || '').match(/filename\*=UTF-8''([^;]+)/i)?.[1];
        if (encoded) {
            try {
                return decodeURIComponent(encoded);
            } catch (_error) {
                return encoded;
            }
        }
        return String(disposition || '').match(/filename="?([^";]+)"?/i)?.[1]
            || 'device-configurations.zip';
    }

    async function downloadConfiguration(href, browserWindow) {
        try {
            const response = await browserWindow.fetch(href, {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            if (!response.ok) {
                const contentType = String(response.headers?.get?.('content-type') || '').toLowerCase();
                const detail = contentType.includes('text/plain') ? String(await response.text()).trim() : '';
                return {ok: false, message: detail || `配置下载失败（HTTP ${response.status}）。`};
            }
            const disposition = response.headers?.get?.('content-disposition') || '';
            if (!/attachment/i.test(disposition)) {
                return {ok: false, message: '服务器未返回可下载的配置文件。'};
            }
            const blob = await response.blob();
            const objectUrl = browserWindow.URL.createObjectURL(blob);
            const anchor = browserWindow.document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = configurationFilename(disposition);
            anchor.hidden = true;
            browserWindow.document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            browserWindow.URL.revokeObjectURL(objectUrl);
            return {ok: true, message: ''};
        } catch (_error) {
            return {ok: false, message: '配置下载失败，请稍后重试。'};
        }
    }

    function renderConfigurationFeedback(feedback, message) {
        if (!feedback) return;
        feedback.textContent = message || '';
        feedback.hidden = !message;
    }

    async function handleConfigurationDownload(event, link, feedback, browserWindow) {
        if (link.getAttribute('aria-disabled') === 'true') {
            event.preventDefault();
            return {ok: false, message: ''};
        }
        if (typeof browserWindow?.fetch !== 'function') return null;
        event.preventDefault();
        renderConfigurationFeedback(feedback, '');
        link.setAttribute('aria-busy', 'true');
        const result = await downloadConfiguration(link.href, browserWindow);
        link.removeAttribute('aria-busy');
        if (!result.ok) renderConfigurationFeedback(feedback, result.message);
        return result;
    }

    return {
        configurationDownloadHref,
        configurationDownloadState,
        downloadConfiguration,
        handleConfigurationDownload,
        renderConfigurationFeedback,
        updateConfigurationDownload,
        updateTargetSelection,
    };
}));
