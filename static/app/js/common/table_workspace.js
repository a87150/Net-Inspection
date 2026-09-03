(function (factory) {
    'use strict';

    const controller = factory();
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
            controller.openAutoOpenImportModal(document, window.bootstrap);
        });
    }
}(function () {
    'use strict';

    const STORAGE_PREFIX = 'inspection-table:v1:';

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

    function clearActiveFilter(elements, toggle, location) {
        const key = toggle.dataset.filterKey;
        const field = elements.filterFields.find(
            (candidate) => candidate.dataset.filterKey === key,
        );
        if (!field || field.dataset.filterActive === undefined) return;

        const controls = Array.from(field.querySelectorAll('[name]'));
        const parameters = (field.dataset.filterParameters || '')
            .split(/\s+/)
            .filter(Boolean);
        controls.forEach((control) => {
            control.value = '';
            control.disabled = true;
            if (control.name && !parameters.includes(control.name)) {
                parameters.push(control.name);
            }
        });
        delete field.dataset.filterActive;
        delete toggle.dataset.filterActive;

        elements.exportLinks.forEach((link) => {
            if (!link.href) return;
            const exportUrl = new URL(link.href, location?.href);
            parameters.forEach((parameter) => exportUrl.searchParams.delete(parameter));
            exportUrl.searchParams.delete('page');
            link.href = exportUrl.toString();
        });
        if (!location?.href || typeof location.replace !== 'function') return;
        const pageUrl = new URL(location.href);
        parameters.forEach((parameter) => pageUrl.searchParams.delete(parameter));
        pageUrl.searchParams.delete('page');
        location.replace(pageUrl.toString());
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
            suggestionSelects: Array.from(workspace.querySelectorAll('[data-filter-suggestion-select]')),
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

    function openAutoOpenImportModal(documentRoot, bootstrapApi) {
        const modalElement = documentRoot.querySelector(
            '#importModal[data-auto-open="true"]',
        );
        if (!modalElement || typeof bootstrapApi?.Modal?.getOrCreateInstance !== 'function') {
            return;
        }
        bootstrapApi.Modal.getOrCreateInstance(modalElement).show();
    }

    return {initializeWorkspace, initializeAll, openAutoOpenImportModal, storageKey};
}));
