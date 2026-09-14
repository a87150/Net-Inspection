'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/common/table_workspace.js');
} catch (_error) {
    controller = {};
}

function element({dataset = {}, checked = false, value = '', hidden = false, options = [], name = ''} = {}) {
    const listeners = new Map();
    return {
        dataset,
        checked,
        defaultChecked: checked,
        value,
        name,
        disabled: false,
        hidden,
        options: options.map((optionValue) => ({value: String(optionValue)})),
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        dispatch(type) {
            listeners.get(type)?.({preventDefault() {}});
        },
        querySelectorAll(selector) {
            return selector === '[name]' && this.name ? [this] : [];
        },
    };
}

function workspaceFixture({selectedPageSize = '20'} = {}) {
    const columnToggles = [
        element({dataset: {columnKey: 'name'}, checked: true}),
        element({dataset: {columnKey: 'leader'}, checked: false}),
    ];
    const columnCells = [
        element({dataset: {columnKey: 'name'}}),
        element({dataset: {columnKey: 'name'}}),
        element({dataset: {columnKey: 'leader'}, hidden: true}),
        element({dataset: {columnKey: 'leader'}, hidden: true}),
    ];
    const filterToggles = [
        element({dataset: {filterKey: 'name'}, checked: true}),
        element({dataset: {filterKey: 'department'}, checked: true}),
    ];
    const filterFields = [
        element({dataset: {filterKey: 'name'}, value: '当前查询', name: 'filter_name'}),
        element({dataset: {filterKey: 'department'}, value: '技术部', name: 'filter_department'}),
    ];
    const suggestionSelect = element({
        dataset: {filterInput: 'people-department-filter-input'},
        options: ['', '研发部', '运维部'],
    });
    const pageSize = element({
        dataset: {defaultPageSize: '20'},
        value: selectedPageSize,
        options: [20, 50, 100],
    });
    pageSize.name = 'page_size';
    pageSize.form = {
        submissions: 0,
        requestSubmit() {
            this.submissions += 1;
        },
    };
    const reset = element();
    const exportLink = {
        href: 'https://example.test/tables/people/export/?filter_department=%E6%8A%80%E6%9C%AF%E9%83%A8&page=3&target=asset-1',
    };
    const configExportLink = {
        href: 'https://example.test/assets/networks/configurations.zip?filter_department=IT&page=3&target=asset-1',
    };
    const moreFilters = {
        open: false,
        querySelectorAll(selector) {
            assert.equal(selector, '[data-filter-field][data-filter-key]');
            return [filterFields[1]];
        },
    };
    const workspace = {
        dataset: {tableKey: 'people'},
        querySelectorAll(selector) {
            return {
                '[data-column-toggle]': columnToggles,
                'th[data-column-key], td[data-column-key]': columnCells,
                '[data-filter-toggle]': filterToggles,
                '[data-filter-field][data-filter-key]': filterFields,
                '[data-filtered-export]': [exportLink, configExportLink],
                '[data-filter-suggestion-select]': [suggestionSelect],
            }[selector] || [];
        },
        querySelector(selector) {
            return {
                '[data-page-size]': pageSize,
                '[data-table-reset]': reset,
                '[data-more-filters]': moreFilters,
                '[data-filtered-export]': exportLink,
                '#people-department-filter-input': filterFields[1],
            }[selector] || null;
        },
    };
    return {
        workspace, columnToggles, columnCells, filterToggles, filterFields,
        pageSize, reset, moreFilters, exportLink, configExportLink, suggestionSelect,
    };
}

function memoryStorage(initialValue = null) {
    return {
        value: initialValue,
        removed: [],
        getItem(key) {
            assert.equal(key, 'inspection-table:v1:people');
            return this.value;
        },
        setItem(key, value) {
            assert.equal(key, 'inspection-table:v1:people');
            this.value = value;
        },
        removeItem(key) {
            assert.equal(key, 'inspection-table:v1:people');
            this.removed.push(key);
            this.value = null;
        },
    };
}

test('selected configuration download uses raw file for one device and zip for many', () => {
    assert.equal(typeof controller.configurationDownloadHref, 'function');
    const zip = 'https://example.test/assets/networks/configurations.zip';

    assert.equal(typeof controller.configurationDownloadState, 'function');
    assert.deepEqual(controller.configurationDownloadState(zip, []), {
        href: '',
        disabled: true,
        label: '请先选择设备',
    });

    assert.equal(controller.configurationDownloadHref(zip, []), '');
    assert.equal(
        controller.configurationDownloadHref(zip, ['first-id']),
        'https://example.test/assets/networks/configurations.zip?target_ids=first-id',
    );
    assert.equal(
        controller.configurationDownloadHref(zip, ['first-id', 'second-id']),
        'https://example.test/assets/networks/configurations.zip?target_ids=first-id%2Csecond-id',
    );
});
test('device bulk selection selects and inverts the current page only', () => {
    assert.equal(typeof controller.updateTargetSelection, 'function');
    const targets = [
        {checked: false, disabled: false},
        {checked: true, disabled: false},
        {checked: false, disabled: true},
    ];

    controller.updateTargetSelection(targets, 'all');
    assert.deepEqual(targets.map((target) => target.checked), [true, true, false]);

    controller.updateTargetSelection(targets, 'invert');
    assert.deepEqual(targets.map((target) => target.checked), [false, false, false]);
});

test('failed configuration download stays on the page and returns inline feedback', async () => {
    assert.equal(typeof controller.downloadConfiguration, 'function');
    let assigned = false;
    const browserWindow = {
        fetch: async () => ({
            ok: false,
            status: 409,
            headers: {get: () => 'text/plain; charset=utf-8'},
            text: async () => '所选设备没有可下载的配置。',
        }),
        location: {assign() { assigned = true; }},
    };

    const result = await controller.downloadConfiguration(
        'https://example.test/assets/networks/configurations.zip?target_ids=one',
        browserWindow,
    );

    assert.deepEqual(result, {
        ok: false,
        message: '所选设备没有可下载的配置。',
    });
    assert.equal(assigned, false);
});

test('configuration download network errors become inline feedback', async () => {
    const result = await controller.downloadConfiguration('/configurations.zip', {
        fetch: async () => { throw new Error('offline'); },
    });

    assert.deepEqual(result, {ok: false, message: '配置下载失败，请稍后重试。'});
});

test('configuration download handler renders a same-layer error without navigation', async () => {
    let prevented = false;
    const attributes = new Map([['aria-disabled', 'false']]);
    const link = {
        href: '/assets/networks/configurations.zip?target_ids=one',
        getAttribute(name) { return attributes.get(name); },
        setAttribute(name, value) { attributes.set(name, value); },
        removeAttribute(name) { attributes.delete(name); },
    };
    const feedback = {hidden: true, textContent: ''};
    const browserWindow = {
        fetch: async () => ({
            ok: false,
            status: 404,
            headers: {get: () => 'text/plain'},
            text: async () => '所选设备尚无配置。',
        }),
    };

    const result = await controller.handleConfigurationDownload(
        {preventDefault() { prevented = true; }},
        link,
        feedback,
        browserWindow,
    );

    assert.equal(prevented, true);
    assert.equal(feedback.hidden, false);
    assert.equal(feedback.textContent, '所选设备尚无配置。');
    assert.deepEqual(result, {ok: false, message: '所选设备尚无配置。'});
    assert.equal(attributes.has('aria-busy'), false);
});

test('successful configuration request downloads the returned attachment', async () => {
    const blob = {type: 'application/zip'};
    const anchor = {
        clicked: false,
        removed: false,
        click() { this.clicked = true; },
        remove() { this.removed = true; },
    };
    let revoked = '';
    const browserWindow = {
        fetch: async () => ({
            ok: true,
            headers: {get(name) {
                return name === 'content-disposition'
                    ? "attachment; filename*=UTF-8''network-configurations.zip"
                    : 'application/zip';
            }},
            blob: async () => blob,
        }),
        URL: {
            createObjectURL(value) { assert.equal(value, blob); return 'blob:download'; },
            revokeObjectURL(value) { revoked = value; },
        },
        document: {
            createElement() { return anchor; },
            body: {appendChild(value) { assert.equal(value, anchor); }},
        },
    };

    const result = await controller.downloadConfiguration('/configurations.zip', browserWindow);

    assert.deepEqual(result, {ok: true, message: ''});
    assert.equal(anchor.download, 'network-configurations.zip');
    assert.equal(anchor.clicked, true);
    assert.equal(anchor.removed, true);
    assert.equal(revoked, 'blob:download');
});

test('applies intersected stored preferences without clearing filter values', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    const storage = memoryStorage(JSON.stringify({
        visibleFields: ['leader', 'removed-column'],
        filterFields: ['department', 'removed-filter'],
        pageSize: 50,
    }));

    controller.initializeWorkspace(fixture.workspace, storage);

    assert.deepEqual(fixture.columnToggles.map((toggle) => toggle.checked), [false, true]);
    assert.deepEqual(fixture.columnCells.map((cell) => cell.hidden), [true, true, false, false]);
    assert.deepEqual(fixture.filterToggles.map((toggle) => toggle.checked), [false, true]);
    assert.deepEqual(fixture.filterFields.map((field) => field.hidden), [true, false]);
    assert.deepEqual(fixture.filterFields.map((field) => field.value), ['当前查询', '技术部']);
    assert.equal(fixture.pageSize.value, '50');
});

test('preserves the server-selected page size without stored preferences', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture({selectedPageSize: '50'});
    const storage = memoryStorage();
    const location = {
        href: 'https://example.test/item/people/?page_size=50',
        replacements: [],
        replace(url) {
            this.replacements.push(url);
        },
    };

    controller.initializeWorkspace(fixture.workspace, storage, location);

    assert.equal(fixture.pageSize.value, '50');
    assert.deepEqual(location.replacements, []);
    fixture.reset.dispatch('click');
    assert.equal(fixture.pageSize.value, '20');
});

test('saves checkbox and page-size changes with the versioned schema', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    const storage = memoryStorage();
    controller.initializeWorkspace(fixture.workspace, storage);

    fixture.columnToggles[1].checked = true;
    fixture.columnToggles[1].dispatch('change');
    fixture.filterToggles[0].checked = false;
    fixture.filterToggles[0].dispatch('change');
    fixture.pageSize.value = '100';
    fixture.pageSize.dispatch('change');

    assert.deepEqual(JSON.parse(storage.value), {
        visibleFields: ['name', 'leader'],
        filterFields: ['department'],
        pageSize: 100,
    });
    assert.equal(fixture.pageSize.form.submissions, 1);
});

test('keeps compact filter preferences and suggestion values after reload', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    fixture.filterFields[1].value = '技术部';
    const storage = memoryStorage(JSON.stringify({
        visibleFields: ['name'],
        filterFields: ['department'],
        pageSize: 20,
    }));

    controller.initializeWorkspace(fixture.workspace, storage);

    assert.deepEqual(
        fixture.filterToggles.map((toggle) => toggle.checked),
        [false, true],
    );
    assert.equal(fixture.filterFields[1].value, '技术部');
    assert.equal(fixture.moreFilters.open, true);
});

test('copies a dropdown suggestion into the editable filter and keeps all choices reusable', () => {
    const fixture = workspaceFixture();
    controller.initializeWorkspace(fixture.workspace, memoryStorage());

    fixture.suggestionSelect.value = '研发部';
    fixture.suggestionSelect.dispatch('change');

    assert.equal(fixture.filterFields[1].value, '研发部');
    assert.equal(fixture.suggestionSelect.value, '');
    assert.deepEqual(
        fixture.suggestionSelect.options.map((option) => option.value),
        ['', '研发部', '运维部'],
    );

    fixture.filterFields[1].value = '自定义部门';
    assert.equal(fixture.filterFields[1].value, '自定义部门');
});

test('hiding an active filter clears it from controls URL and export state', () => {
    const fixture = workspaceFixture();
    fixture.filterToggles[1].dataset.filterActive = 'true';
    fixture.filterFields[1].dataset.filterActive = 'true';
    const storage = memoryStorage();
    const location = {
        href: 'https://example.test/assets/people/?filter_department=%E6%8A%80%E6%9C%AF%E9%83%A8&page=3&target=asset-1',
        replacements: [],
        replace(url) { this.replacements.push(url); },
    };

    controller.initializeWorkspace(fixture.workspace, storage, location);
    fixture.filterToggles[1].checked = false;
    fixture.filterToggles[1].dispatch('change');

    assert.equal(fixture.filterFields[1].value, '');
    assert.equal(fixture.filterFields[1].disabled, true);
    assert.equal(new URL(fixture.exportLink.href).searchParams.has('filter_department'), false);
    assert.equal(new URL(fixture.configExportLink.href).searchParams.has('filter_department'), false);
    assert.equal(new URL(fixture.configExportLink.href).searchParams.has('page'), false);
    assert.equal(new URL(fixture.configExportLink.href).pathname, '/assets/networks/configurations.zip');
    assert.equal(location.replacements.length, 1);
    const replacement = new URL(location.replacements[0]);
    assert.equal(replacement.searchParams.has('filter_department'), false);
    assert.equal(replacement.searchParams.has('page'), false);
    assert.equal(replacement.searchParams.get('target'), 'asset-1');
    assert.deepEqual(JSON.parse(storage.value).filterFields, ['name']);
});

test('stored page size replaces a mismatched query and drops the stale page', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    const storage = memoryStorage(JSON.stringify({
        visibleFields: ['name'],
        filterFields: ['name'],
        pageSize: 50,
    }));
    const location = {
        href: 'https://example.test/item/people/?q=%E5%BC%A0&page=3&page_size=20',
        replacements: [],
        replace(url) {
            this.replacements.push(url);
        },
    };

    controller.initializeWorkspace(fixture.workspace, storage, location);

    assert.equal(location.replacements.length, 1);
    const replacement = new URL(location.replacements[0]);
    assert.equal(replacement.searchParams.get('q'), '张');
    assert.equal(replacement.searchParams.get('page_size'), '50');
    assert.equal(replacement.searchParams.has('page'), false);
});

test('reset removes storage and restores DOM defaults', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    const storage = memoryStorage(JSON.stringify({
        visibleFields: ['leader'],
        filterFields: [],
        pageSize: 100,
    }));
    controller.initializeWorkspace(fixture.workspace, storage);

    fixture.reset.dispatch('click');

    assert.deepEqual(storage.removed, ['inspection-table:v1:people']);
    assert.deepEqual(fixture.columnToggles.map((toggle) => toggle.checked), [true, false]);
    assert.deepEqual(fixture.columnCells.map((cell) => cell.hidden), [false, false, true, true]);
    assert.deepEqual(fixture.filterToggles.map((toggle) => toggle.checked), [true, true]);
    assert.deepEqual(fixture.filterFields.map((field) => field.hidden), [false, false]);
    assert.equal(fixture.pageSize.value, '20');
    assert.equal(fixture.pageSize.form.submissions, 1);
});

test('keeps server defaults operational when storage access throws', () => {
    assert.equal(typeof controller.initializeWorkspace, 'function');
    const fixture = workspaceFixture();
    const storage = {
        getItem() { throw new Error('blocked'); },
        setItem() { throw new Error('blocked'); },
        removeItem() { throw new Error('blocked'); },
    };

    assert.doesNotThrow(() => controller.initializeWorkspace(fixture.workspace, storage));
    assert.deepEqual(fixture.columnCells.map((cell) => cell.hidden), [false, false, true, true]);
    assert.deepEqual(fixture.filterFields.map((field) => field.hidden), [false, false]);
    assert.doesNotThrow(() => fixture.columnToggles[0].dispatch('change'));
    assert.doesNotThrow(() => fixture.reset.dispatch('click'));
});

test('initializes every workspace found in the document', () => {
    assert.equal(typeof controller.initializeAll, 'function');
    const first = workspaceFixture();
    const second = workspaceFixture();
    second.workspace.dataset.tableKey = 'computers';
    const keys = [];
    const storage = {
        getItem(key) { keys.push(key); return null; },
        setItem() {},
        removeItem() {},
    };
    const document = {
        querySelectorAll(selector) {
            assert.equal(selector, '[data-table-workspace]');
            return [first.workspace, second.workspace];
        },
    };

    controller.initializeAll(document, storage);

    assert.deepEqual(keys, [
        'inspection-table:v1:people',
        'inspection-table:v1:computers',
    ]);
});

test('keeps global and per-project record preferences in separate storage keys', () => {
    const globalRecords = workspaceFixture();
    const networkRecords = workspaceFixture();
    globalRecords.workspace.dataset.tableKey = 'inspection_records-global';
    networkRecords.workspace.dataset.tableKey = 'inspection_records-networks';
    const values = new Map();
    const storage = {
        getItem(key) { return values.get(key) || null; },
        setItem(key, value) { values.set(key, value); },
        removeItem(key) { values.delete(key); },
    };
    const document = {
        querySelectorAll() {
            return [globalRecords.workspace, networkRecords.workspace];
        },
    };

    controller.initializeAll(document, storage);
    globalRecords.columnToggles[1].checked = true;
    globalRecords.columnToggles[1].dispatch('change');
    networkRecords.columnToggles[0].checked = false;
    networkRecords.columnToggles[0].dispatch('change');

    assert.deepEqual(
        JSON.parse(values.get('inspection-table:v1:inspection_records-global')).visibleFields,
        ['name', 'leader'],
    );
    assert.deepEqual(
        JSON.parse(values.get('inspection-table:v1:inspection_records-networks')).visibleFields,
        [],
    );
});

test('opens an import modal marked for one-time auto-open', () => {
    assert.equal(typeof controller.openAutoOpenImportModal, 'function');
    const modalElement = {dataset: {autoOpen: 'true'}};
    const modalInstance = {
        showCount: 0,
        show() { this.showCount += 1; },
    };
    const document = {
        querySelector(selector) {
            assert.equal(selector, '#importModal[data-auto-open="true"]');
            return modalElement;
        },
    };
    const bootstrap = {
        Modal: {
            getOrCreateInstance(element) {
                assert.equal(element, modalElement);
                return modalInstance;
            },
        },
    };

    controller.openAutoOpenImportModal(document, bootstrap);

    assert.equal(modalInstance.showCount, 1);
});
test('describes every active filter for compact chips', () => {
    assert.equal(typeof controller.activeFilterDescriptors, 'function');
    const source = element({dataset: {
        filterKey: 'department',
        filterLabel: 'Department',
        filterParameters: 'filter_department',
        filterActive: 'true',
    }});

    assert.deepEqual(controller.activeFilterDescriptors({activeFilterSources: [source]}), [{
        key: 'department',
        label: 'Department',
        parameters: ['filter_department'],
        source,
    }]);
});

test('clearing one filter preserves unrelated query state and focuses its control', () => {
    assert.equal(typeof controller.clearActiveFilterSource, 'function');
    const control = element({name: 'filter_department', value: 'IT'});
    control.focused = false;
    control.focus = () => { control.focused = true; };
    const source = element({dataset: {
        filterKey: 'department', filterParameters: 'filter_department', filterActive: 'true',
    }});
    source.querySelectorAll = selector => selector === '[name]' ? [control] : [];
    source.querySelector = () => control;
    const location = {
        href: 'https://example.test/assets/people/?filter_department=IT&target=asset-1&page=4',
        replacements: [],
        replace(url) { this.replacements.push(url); },
    };

    controller.clearActiveFilterSource({exportLinks: []}, source, location);

    const replacement = new URL(location.replacements[0]);
    assert.equal(replacement.searchParams.has('filter_department'), false);
    assert.equal(replacement.searchParams.has('page'), false);
    assert.equal(replacement.searchParams.get('target'), 'asset-1');
    assert.equal(control.focused, true);
});
function partialNavigationFixture({fetchError = null} = {}) {
    const replacementFixture = workspaceFixture();
    const replacement = replacementFixture.workspace;
    const originalQuerySelector = replacement.querySelector.bind(replacement);
    const focusedSortLink = {focused: false, focus() { this.focused = true; }};
    const resultSummary = {textContent: '共 18 条，当前显示第 1–18 条'};
    const liveRegion = {textContent: ''};
    replacement.querySelector = (selector) => (
        selector === '[data-sort-key="name"]' ? focusedSortLink
            : selector === '[data-result-summary]' ? resultSummary
                : originalQuerySelector(selector)
    );

    const current = {
        dataset: {tableKey: 'people'},
        attributes: {},
        replacedWith: null,
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        getAttribute(name) { return this.attributes[name] || null; },
        replaceWith(value) { this.replacedWith = value; },
    };
    const documentListeners = new Map();
    const documentRoot = {
        addEventListener(type, listener) { documentListeners.set(type, listener); },
        getElementById(id) { return id === 'app-live-region' ? liveRegion : null; },
        querySelectorAll(selector) {
            assert.equal(selector, '[data-table-workspace]');
            return [current];
        },
    };
    const windowListeners = new Map();
    const requests = [];
    const history = {pushed: [], pushState(_state, _title, url) { this.pushed.push(url); }};
    const location = {
        href: 'https://example.test/assets/people/?sort=name&order=asc',
        origin: 'https://example.test',
        assigned: [],
        assign(url) { this.assigned.push(url); },
        replace() {},
    };
    const browserWindow = {
        location,
        history,
        addEventListener(type, listener) { windowListeners.set(type, listener); },
        async fetch(url, options) {
            requests.push({url, options});
            if (fetchError) throw fetchError;
            return {
                ok: true,
                url,
                async text() { return '<html>updated table</html>'; },
            };
        },
        DOMParser: class {
            parseFromString(html, type) {
                assert.equal(html, '<html>updated table</html>');
                assert.equal(type, 'text/html');
                return {
                    querySelectorAll(selector) {
                        assert.equal(selector, '[data-table-workspace]');
                        return [replacement];
                    },
                };
            }
        },
        FormData: class {
            constructor(form) { this.form = form; }
            *entries() { yield* this.form.entries; }
        },
    };
    return {
        browserWindow, current, documentListeners, documentRoot, focusedSortLink, liveRegion,
        history, location, replacement, requests, windowListeners,
    };
}

test('sort click fetches the server-sorted page and replaces the table workspace', async () => {
    assert.equal(typeof controller.bindPartialTableNavigation, 'function');
    const fixture = partialNavigationFixture();
    const binding = controller.bindPartialTableNavigation(
        fixture.documentRoot,
        memoryStorage(),
        fixture.browserWindow,
    );
    const link = {
        href: 'https://example.test/assets/people/?sort=name&order=desc&page_size=20',
        target: '',
        dataset: {sortKey: 'name'},
        closest(selector) {
            return selector === '[data-table-workspace]' ? fixture.current : null;
        },
    };
    const event = {
        button: 0, defaultPrevented: false,
        metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
        prevented: false,
        preventDefault() { this.prevented = true; },
        target: {closest(selector) { return selector === '.table-sort-link, [data-query-reset]' ? link : null; }},
    };

    await binding.handleClick(event);

    assert.equal(event.prevented, true);
    assert.equal(fixture.requests.length, 1);
    assert.equal(fixture.requests[0].url, link.href);
    assert.equal(fixture.requests[0].options.headers['X-Requested-With'], 'XMLHttpRequest');
    assert.equal(fixture.current.replacedWith, fixture.replacement);
    assert.deepEqual(fixture.history.pushed, [link.href]);
    assert.equal(fixture.focusedSortLink.focused, true);
    assert.equal(fixture.liveRegion.textContent, '列表已更新。共 18 条，当前显示第 1–18 条');
    assert.deepEqual(fixture.location.assigned, []);
});

test('filter submit serializes all current controls and updates without a page reload', async () => {
    const fixture = partialNavigationFixture();
    const binding = controller.bindPartialTableNavigation(
        fixture.documentRoot,
        memoryStorage(),
        fixture.browserWindow,
    );
    const form = {
        method: 'get',
        action: 'https://example.test/assets/people/',
        entries: [['q', '张 三'], ['filter_department', '运维部'], ['page_size', '50']],
        matches(selector) { return selector === '[data-table-query-form]'; },
        closest(selector) {
            return selector === '[data-table-workspace]' ? fixture.current : null;
        },
    };
    const event = {defaultPrevented: false, prevented: false, target: form, preventDefault() { this.prevented = true; }};

    await binding.handleSubmit(event);

    assert.equal(event.prevented, true);
    assert.equal(fixture.requests.length, 1);
    const requested = new URL(fixture.requests[0].url);
    assert.equal(requested.pathname, '/assets/people/');
    assert.equal(requested.searchParams.get('q'), '张 三');
    assert.equal(requested.searchParams.get('filter_department'), '运维部');
    assert.equal(requested.searchParams.get('page_size'), '50');
    assert.equal(requested.searchParams.has('page'), false);
    assert.equal(fixture.current.replacedWith, fixture.replacement);
    assert.equal(fixture.history.pushed.length, 1);
    assert.deepEqual(fixture.location.assigned, []);
});

test('partial table request failure falls back to normal navigation', async () => {
    const fixture = partialNavigationFixture({fetchError: new Error('offline')});
    const targetUrl = 'https://example.test/assets/people/?sort=name&order=desc';

    await controller.refreshTableWorkspaces(
        fixture.documentRoot,
        targetUrl,
        memoryStorage(),
        fixture.browserWindow,
    );

    assert.deepEqual(fixture.location.assigned, [targetUrl]);
    assert.equal(fixture.current.getAttribute('aria-busy'), null);
});

test('browser history refreshes table content without adding another history entry', async () => {
    const fixture = partialNavigationFixture();
    const binding = controller.bindPartialTableNavigation(
        fixture.documentRoot,
        memoryStorage(),
        fixture.browserWindow,
    );

    await binding.handlePopState();

    assert.equal(fixture.requests[0].url, fixture.location.href);
    assert.equal(fixture.current.replacedWith, fixture.replacement);
    assert.deepEqual(fixture.history.pushed, []);
});
