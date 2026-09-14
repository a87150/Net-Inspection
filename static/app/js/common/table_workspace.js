(function (factory) {
    'use strict';

    const selection = typeof module === 'object' && module.exports
        ? require('./table_selection.js')
        : window.AppTableSelection;
    const controller = factory(selection);
    if (typeof window !== 'undefined') window.AppTableWorkspace = controller;
    if (typeof module === 'object' && module.exports) {
        module.exports = controller;
    }
    if (typeof document !== 'undefined') {
        document.addEventListener('DOMContentLoaded', () => {
            let storage = null;
            try {
                storage = window.localStorage;
            } catch (_error) {
                storage = null;
            }
            controller.initializeAll(document, storage, window.location);
            controller.bindPartialTableNavigation(document, storage, window);
            controller.openAutoOpenImportModal(document, window.bootstrap);
        });
    }
}(function (selection) {
    'use strict';

    const STORAGE_PREFIX = 'inspection-table:v1:';
    const {
        configurationDownloadHref,
        configurationDownloadState,
        downloadConfiguration,
        handleConfigurationDownload,
        renderConfigurationFeedback,
        updateConfigurationDownload,
        updateTargetSelection,
    } = selection;

    function storageKey(tableKey) {
        return `${STORAGE_PREFIX}${tableKey}`;
    }

    function checkedKeys(toggles, keyName) {
        return toggles
            .filter((toggle) => toggle.checked)
            .map((toggle) => toggle.dataset[keyName]);
    }

    function intersect(values, allowedKeys) {
        if (!Array.isArray(values)) return null;
        const allowed = new Set(allowedKeys);
        return values.filter((value) => typeof value === 'string' && allowed.has(value));
    }

    function readPreferences(storage, key, defaults, available) {
        if (!storage) return null;
        try {
            const raw = storage.getItem(key);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            const visibleFields = intersect(parsed.visibleFields, available.columnKeys);
            const filterFields = intersect(parsed.filterFields, available.filterKeys);
            const pageSize = Number(parsed.pageSize);
            return {
                visibleFields: visibleFields === null ? defaults.visibleFields : visibleFields,
                filterFields: filterFields === null ? defaults.filterFields : filterFields,
                pageSize: available.pageSizes.includes(pageSize) ? pageSize : defaults.pageSize,
            };
        } catch (_error) {
            return null;
        }
    }

    function applyPreferences(elements, preferences) {
        const visibleFields = new Set(preferences.visibleFields);
        const filterFields = new Set(preferences.filterFields);

        elements.columnToggles.forEach((toggle) => {
            toggle.checked = visibleFields.has(toggle.dataset.columnKey);
        });
        elements.columnCells.forEach((cell) => {
            cell.hidden = !visibleFields.has(cell.dataset.columnKey);
        });
        elements.filterToggles.forEach((toggle) => {
            toggle.checked = filterFields.has(toggle.dataset.filterKey);
        });
        elements.filterFields.forEach((field) => {
            field.hidden = !filterFields.has(field.dataset.filterKey);
        });
        if (elements.moreFilters) {
            elements.moreFilters.open = elements.moreFilterFields.some(
                (field) => !field.hidden,
            );
        }
        if (elements.pageSize) {
            elements.pageSize.value = String(preferences.pageSize);
        }
    }

    function activeFilterKeys(elements) {
        return elements.filterToggles
            .filter((toggle) => toggle.dataset.filterActive !== undefined)
            .map((toggle) => toggle.dataset.filterKey);
    }

    function keepActiveFiltersVisible(preferences, elements) {
        const visible = new Set(preferences.filterFields);
        activeFilterKeys(elements).forEach((key) => visible.add(key));
        return {...preferences, filterFields: Array.from(visible)};
    }

    function filterParameters(source) {
        return String(source?.dataset?.filterParameters || '').split(/\s+/).filter(Boolean);
    }

    function activeFilterDescriptors(elements) {
        return (elements.activeFilterSources || [])
            .filter((source) => source.dataset.filterActive !== undefined)
            .map((source) => ({
                key: source.dataset.filterKey,
                label: source.dataset.filterLabel || source.dataset.filterKey,
                parameters: filterParameters(source),
                source,
            }));
    }

    function clearActiveFilterSource(elements, source, location) {
        if (!source || source.dataset.filterActive === undefined) return false;
        const controls = Array.from(source.querySelectorAll?.('[name]') || []);
        const parameters = filterParameters(source);
        const focusTarget = source.querySelector?.('[name]:not([type="hidden"])') || controls[0];
        focusTarget?.focus?.();
        controls.forEach((control) => {
            control.value = '';
            control.disabled = true;
            if (control.name && !parameters.includes(control.name)) parameters.push(control.name);
        });
        delete source.dataset.filterActive;
        elements.exportLinks.forEach((link) => {
            if (!link.href) return;
            const exportUrl = new URL(link.href, location?.href);
            parameters.forEach((parameter) => exportUrl.searchParams.delete(parameter));
            exportUrl.searchParams.delete('page');
            link.href = exportUrl.toString();
        });
        if (!location?.href || typeof location.replace !== 'function') return true;
        const pageUrl = new URL(location.href);
        parameters.forEach((parameter) => pageUrl.searchParams.delete(parameter));
        pageUrl.searchParams.delete('page');
        location.replace(pageUrl.toString());
        return true;
    }

    function renderActiveFilterChips(elements, documentRoot, location) {
        const container = elements.activeFilterList;
        if (!container || typeof documentRoot?.createElement !== 'function') return [];
        const descriptors = activeFilterDescriptors(elements);
        container.replaceChildren?.();
        descriptors.forEach((descriptor) => {
            const button = documentRoot.createElement('button');
            button.type = 'button';
            button.className = 'table-filter-chip';
            button.textContent = `${descriptor.label} ×`;
            button.setAttribute('aria-label', `清除筛选：${descriptor.label}`);
            button.addEventListener('click', () => {
                const toggle = elements.filterToggles.find(
                    (candidate) => candidate.dataset.filterKey === descriptor.key,
                );
                if (toggle) {
                    toggle.checked = false;
                    delete toggle.dataset.filterActive;
                }
                clearActiveFilterSource(elements, descriptor.source, location);
            });
            container.append?.(button);
        });
        container.hidden = descriptors.length === 0;
        return descriptors;
    }

    function clearActiveFilter(elements, toggle, location) {
        const key = toggle.dataset.filterKey;
        const sources = elements.activeFilterSources?.length ? elements.activeFilterSources : elements.filterFields;
        const source = sources.find(
            (candidate) => candidate.dataset.filterKey === key,
        );
        if (!source || source.dataset.filterActive === undefined) return;
        delete toggle.dataset.filterActive;
        clearActiveFilterSource(elements, source, location);
    }
    function currentPreferences(elements, defaults) {
        return {
            visibleFields: checkedKeys(elements.columnToggles, 'columnKey'),
            filterFields: checkedKeys(elements.filterToggles, 'filterKey'),
            pageSize: elements.pageSize ? Number(elements.pageSize.value) : defaults.pageSize,
        };
    }

    function writePreferences(storage, key, preferences) {
        if (!storage) return;
        try {
            storage.setItem(key, JSON.stringify(preferences));
        } catch (_error) {
            // Storage can be unavailable in private or restricted browser contexts.
        }
    }

    function removePreferences(storage, key) {
        if (!storage) return;
        try {
            storage.removeItem(key);
        } catch (_error) {
            // Reset still applies the server defaults when storage is unavailable.
        }
    }

    function replacePageSizeQuery(location, pageSize, value) {
        if (!location?.href || typeof location.replace !== 'function' || !pageSize) return;
        const parameter = pageSize.name || 'page_size';
        const url = new URL(location.href);
        if (url.searchParams.get(parameter) === String(value)) return;
        const pageParameter = parameter === 'page_size'
            ? 'page'
            : parameter.replace(/_page_size$/, '_page');
        url.searchParams.set(parameter, String(value));
        url.searchParams.delete(pageParameter);
        location.replace(url.toString());
    }

    function submitPageSize(pageSize) {
        if (typeof pageSize?.form?.requestSubmit === 'function') {
            pageSize.form.requestSubmit();
            return true;
        }
        if (typeof pageSize?.form?.submit === 'function') {
            pageSize.form.submit();
            return true;
        }
        return false;
    }

    function initializeWorkspace(workspace, storage, location = null) {
        const tableKey = workspace.dataset.tableKey;
        if (!tableKey) return;

        const elements = {
            columnToggles: Array.from(workspace.querySelectorAll('[data-column-toggle]')),
            columnCells: Array.from(workspace.querySelectorAll('th[data-column-key], td[data-column-key]')),
            filterToggles: Array.from(workspace.querySelectorAll('[data-filter-toggle]')),
            filterFields: Array.from(workspace.querySelectorAll('[data-filter-field][data-filter-key]')),
            pageSize: workspace.querySelector('[data-page-size]'),
            reset: workspace.querySelector('[data-table-reset]'),
            moreFilters: workspace.querySelector('[data-more-filters]'),
            exportLinks: Array.from(workspace.querySelectorAll('[data-filtered-export]')),
            configurationExport: workspace.querySelector('[data-selected-config-export]'),
            configurationTargets: Array.from(workspace.querySelectorAll('[data-configuration-target]')),
            selectionTargets: Array.from(workspace.querySelectorAll('[data-device-selection-target]')),
            selectionAll: workspace.querySelector('[data-device-select-all]'),
            selectionInvert: workspace.querySelector('[data-device-select-invert]'),
            configurationFeedback: workspace.querySelector('[data-configuration-export-feedback]'),
            suggestionSelects: Array.from(workspace.querySelectorAll('[data-filter-suggestion-select]')),
            activeFilterSources: Array.from(workspace.querySelectorAll('[data-active-filter-source]')),
            activeFilterList: workspace.querySelector('[data-active-filter-list]'),
        };
        elements.moreFilterFields = elements.moreFilters
            ? Array.from(elements.moreFilters.querySelectorAll(
                '[data-filter-field][data-filter-key]',
            ))
            : [];
        const defaults = {
            visibleFields: elements.columnToggles
                .filter((toggle) => toggle.defaultChecked)
                .map((toggle) => toggle.dataset.columnKey),
            filterFields: elements.filterToggles
                .filter((toggle) => toggle.defaultChecked)
                .map((toggle) => toggle.dataset.filterKey),
            pageSize: Number(elements.pageSize?.dataset.defaultPageSize || elements.pageSize?.value || 0),
        };
        const available = {
            columnKeys: elements.columnToggles.map((toggle) => toggle.dataset.columnKey),
            filterKeys: elements.filterToggles.map((toggle) => toggle.dataset.filterKey),
            pageSizes: elements.pageSize
                ? Array.from(elements.pageSize.options).map((option) => Number(option.value))
                : [defaults.pageSize],
        };
        const current = currentPreferences(elements, defaults);
        const key = storageKey(tableKey);
        const storedPreferences = readPreferences(storage, key, defaults, available);
        const preferences = keepActiveFiltersVisible(
            storedPreferences || current,
            elements,
        );
        applyPreferences(elements, preferences);
        renderActiveFilterChips(elements, workspace.ownerDocument, location);
        updateConfigurationDownload(elements.configurationExport, elements.configurationTargets);
        elements.configurationTargets.forEach((target) => {
            target.addEventListener('change', () => {
                updateConfigurationDownload(elements.configurationExport, elements.configurationTargets);
                renderConfigurationFeedback(elements.configurationFeedback, '');
            });
        });
        const applyTargetSelection = (action) => {
            updateTargetSelection(elements.selectionTargets, action);
            updateConfigurationDownload(elements.configurationExport, elements.configurationTargets);
            renderConfigurationFeedback(elements.configurationFeedback, '');
        };
        elements.selectionAll?.addEventListener('click', () => applyTargetSelection('all'));
        elements.selectionInvert?.addEventListener('click', () => applyTargetSelection('invert'));
        elements.configurationExport?.addEventListener('click', async (event) => {
            const browserWindow = workspace.ownerDocument?.defaultView;
            await handleConfigurationDownload(
                event,
                elements.configurationExport,
                elements.configurationFeedback,
                browserWindow,
            );
        });
        if (storedPreferences) {
            replacePageSizeQuery(location, elements.pageSize, preferences.pageSize);
        }

        const save = () => writePreferences(storage, key, currentPreferences(elements, defaults));
        elements.columnToggles.forEach((toggle) => {
            toggle.addEventListener('change', () => {
                applyPreferences(elements, currentPreferences(elements, defaults));
                save();
            });
        });
        elements.filterToggles.forEach((toggle) => {
            toggle.addEventListener('change', () => {
                if (!toggle.checked) {
                    clearActiveFilter(elements, toggle, location);
                }
                applyPreferences(elements, currentPreferences(elements, defaults));
                save();
            });
        });
        elements.suggestionSelects.forEach((select) => {
            select.addEventListener('change', () => {
                const inputId = select.dataset.filterInput;
                const input = inputId ? workspace.querySelector(`#${inputId}`) : null;
                if (!input || !select.value) return;
                input.value = select.value;
                select.value = '';
                input.focus?.();
            });
        });
        elements.pageSize?.addEventListener('change', () => {
            save();
            if (!submitPageSize(elements.pageSize)) {
                replacePageSizeQuery(location, elements.pageSize, elements.pageSize.value);
            }
        });
        elements.reset?.addEventListener('click', () => {
            removePreferences(storage, key);
            applyPreferences(elements, defaults);
            if (!submitPageSize(elements.pageSize)) {
                replacePageSizeQuery(location, elements.pageSize, defaults.pageSize);
            }
        });
    }

    function initializeAll(documentRoot, storage, location = null) {
        documentRoot.querySelectorAll('[data-table-workspace]').forEach((workspace) => {
            initializeWorkspace(workspace, storage, location);
        });
    }

    function announceTableUpdate(documentRoot, replacements) {
        const liveRegion = documentRoot.getElementById?.('app-live-region');
        if (!liveRegion) return;
        const summary = replacements
            .map(replacement => replacement.querySelector('[data-result-summary]')?.textContent?.trim())
            .find(Boolean);
        liveRegion.textContent = summary ? `列表已更新。${summary}` : '列表已更新。';
    }

    async function refreshTableWorkspaces(documentRoot, href, storage, browserWindow, options = {}) {
        const currentWorkspaces = Array.from(documentRoot.querySelectorAll('[data-table-workspace]'));
        currentWorkspaces.forEach((workspace) => workspace.setAttribute('aria-busy', 'true'));
        try {
            const response = await browserWindow.fetch(href, {
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
            });
            if (!response.ok) throw new Error(`Table request failed: ${response.status}`);
            const parsed = new browserWindow.DOMParser().parseFromString(
                await response.text(),
                'text/html',
            );
            const replacements = Array.from(parsed.querySelectorAll('[data-table-workspace]'));
            const pairs = currentWorkspaces.map((workspace) => {
                const replacement = replacements.find(
                    (candidate) => candidate.dataset.tableKey === workspace.dataset.tableKey,
                );
                if (!replacement) throw new Error(`Missing table workspace: ${workspace.dataset.tableKey}`);
                return {workspace, replacement};
            });
            const finalUrl = response.url || href;
            if (options.updateHistory !== false) {
                browserWindow.history.pushState({}, '', finalUrl);
            }
            pairs.forEach(({workspace, replacement}) => {
                workspace.replaceWith(replacement);
                initializeWorkspace(replacement, storage, browserWindow.location);
            });
            announceTableUpdate(documentRoot, pairs.map(({replacement}) => replacement));
            if (options.focusTableKey && options.focusSortKey) {
                const target = pairs.find(
                    ({replacement}) => replacement.dataset.tableKey === options.focusTableKey,
                )?.replacement;
                target?.querySelector(`[data-sort-key="${options.focusSortKey}"]`)?.focus();
            }
            return pairs.map(({replacement}) => replacement);
        } catch (_error) {
            currentWorkspaces.forEach((workspace) => workspace.removeAttribute('aria-busy'));
            browserWindow.location.assign(href);
            return [];
        }
    }

    function bindPartialTableNavigation(documentRoot, storage, browserWindow) {
        if (documentRoot.__partialTableNavigation) return documentRoot.__partialTableNavigation;

        const supported = typeof browserWindow?.fetch === 'function'
            && typeof browserWindow?.DOMParser === 'function';
        const handleClick = async (event) => {
            const link = event.target?.closest?.('.table-sort-link, [data-query-reset]');
            if (!supported || !link || event.defaultPrevented || event.button !== 0
                || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
                || link.target || link.hasAttribute?.('download')) return;
            const url = new URL(link.href, browserWindow.location.href);
            if (url.origin !== browserWindow.location.origin) return;
            const workspace = link.closest('[data-table-workspace]');
            if (!workspace || workspace.getAttribute('aria-busy') === 'true') return;
            event.preventDefault();
            return refreshTableWorkspaces(
                documentRoot, url.toString(), storage, browserWindow,
                {
                    focusTableKey: workspace.dataset.tableKey,
                    focusSortKey: link.dataset.sortKey || '',
                },
            );
        };
        const handleSubmit = async (event) => {
            const form = event.target;
            if (!supported || event.defaultPrevented
                || !form?.matches?.('[data-table-query-form]')
                || String(form.method || 'get').toLowerCase() !== 'get') return;
            const workspace = form.closest('[data-table-workspace]');
            if (!workspace || workspace.getAttribute('aria-busy') === 'true') return;
            const url = new URL(form.action || browserWindow.location.href, browserWindow.location.href);
            const formData = new browserWindow.FormData(form);
            url.search = new URLSearchParams(formData.entries()).toString();
            url.searchParams.delete('page');
            event.preventDefault();
            return refreshTableWorkspaces(documentRoot, url.toString(), storage, browserWindow);
        };
        const handlePopState = () => refreshTableWorkspaces(
            documentRoot,
            browserWindow.location.href,
            storage,
            browserWindow,
            {updateHistory: false},
        );
        documentRoot.addEventListener('click', handleClick);
        documentRoot.addEventListener('submit', handleSubmit);
        browserWindow.addEventListener('popstate', handlePopState);
        const binding = {handleClick, handleSubmit, handlePopState};
        documentRoot.__partialTableNavigation = binding;
        return binding;
    }
    function openAutoOpenImportModal(documentRoot, bootstrapApi) {
        const modalElement = documentRoot.querySelector(
            '#importModal[data-auto-open="true"]',
        );
        if (!modalElement || typeof bootstrapApi?.Modal?.getOrCreateInstance !== 'function') {
            return;
        }
        bootstrapApi.Modal.getOrCreateInstance(modalElement).show();
    }

    return {activeFilterDescriptors, announceTableUpdate, bindPartialTableNavigation, clearActiveFilterSource, configurationDownloadHref, configurationDownloadState, downloadConfiguration, handleConfigurationDownload, initializeWorkspace, initializeAll, openAutoOpenImportModal, refreshTableWorkspaces, renderActiveFilterChips, storageKey, updateTargetSelection};
}));
