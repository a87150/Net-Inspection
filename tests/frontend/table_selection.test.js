'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let selection = {};
try {
    selection = require('../../static/app/js/common/table_selection.js');
} catch (_error) {
    selection = {};
}

test('selection module builds one or many device configuration downloads', () => {
    assert.equal(typeof selection.configurationDownloadState, 'function');
    const href = 'https://example.test/assets/networks/configurations.zip';

    assert.deepEqual(selection.configurationDownloadState(href, []), {
        href: '',
        disabled: true,
        label: '请先选择设备',
    });
    assert.equal(
        selection.configurationDownloadState(href, ['a', 'b']).href,
        'https://example.test/assets/networks/configurations.zip?target_ids=a%2Cb',
    );
});

test('selection module inverts enabled choices only', () => {
    assert.equal(typeof selection.updateTargetSelection, 'function');
    const targets = [
        {checked: true, disabled: false},
        {checked: false, disabled: false},
        {checked: false, disabled: true},
    ];

    selection.updateTargetSelection(targets, 'invert');

    assert.deepEqual(targets.map(target => target.checked), [false, true, false]);
});

test('selection module keeps failed downloads in same-layer feedback', async () => {
    assert.equal(typeof selection.handleConfigurationDownload, 'function');
    const attributes = new Map([['aria-disabled', 'false']]);
    const feedback = {hidden: true, textContent: ''};
    const link = {
        href: '/configurations.zip?target_ids=a',
        getAttribute(name) { return attributes.get(name); },
        setAttribute(name, value) { attributes.set(name, value); },
        removeAttribute(name) { attributes.delete(name); },
    };
    const result = await selection.handleConfigurationDownload(
        {preventDefault() {}},
        link,
        feedback,
        {fetch: async () => ({
            ok: false,
            status: 409,
            headers: {get: () => 'text/plain'},
            text: async () => '没有可下载配置。',
        })},
    );

    assert.equal(result.ok, false);
    assert.equal(feedback.textContent, '没有可下载配置。');
    assert.equal(feedback.hidden, false);
});
