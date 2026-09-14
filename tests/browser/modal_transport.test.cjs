const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const path = require('node:path');
let browser;
before(async () => { browser = await chromium.launch({channel: 'msedge', headless: true}); });
after(async () => { await browser?.close(); });

function markup(type = 'feishu', extra = '') {
    return `<div id="outside">page stays here</div><div class="modal show" id="alertChannelModal">
    <div class="modal-content"><div class="modal-body">
    <a id="email" href="?alert_modal=channel&alert_channel_type=email">Email</a>
    <a id="feishu" href="?alert_modal=channel&alert_channel_type=feishu">Feishu</a>
    <form id="config" method="post" action="/save/">
      <input name="csrfmiddlewaretoken" value="csrf"><input type="hidden" name="channel_type" value="${type}">
      <input name="name" value="${type}"><input type="password" name="password">
      <button type="submit" name="action" value="test">Test</button>
    </form>${extra}</div></div></div>`;
}
async function setup(t, handler, initial = markup()) {
    const page = await browser.newPage();
    page.setDefaultTimeout(5000);
    t.after(() => page.close());
    await page.route('http://modal.test/**', async route => {
        const request = route.request();
        if (request.isNavigationRequest()) return route.fulfill({contentType: 'text/html', body: initial});
        return handler(route, request);
    });
    await page.goto('http://modal.test/alerts/');
    await page.addScriptTag({path: path.resolve('static/app/js/common/modal_transport.js')});
    return page;
}

test('channel switches use partial GET and retain each unfinished draft only in memory', async t => {
    const page = await setup(t, (route, request) => route.fulfill({contentType: 'text/html',
        body: markup(new URL(request.url()).searchParams.get('alert_channel_type'))}));
    await page.locator('[name=name]').fill('unfinished');
    await page.locator('[name=password]').fill('temporary-secret');
    await page.locator('#email').click();
    await page.waitForFunction(() => document.querySelector('[name=channel_type]').value === 'email');
    await page.locator('#feishu').click();
    await page.waitForFunction(() => document.querySelector('[name=channel_type]').value === 'feishu');
    assert.equal(await page.locator('[name=name]').inputValue(), 'unfinished');
    assert.equal(await page.locator('[name=password]').inputValue(), 'temporary-secret');
    assert.equal(page.url(), 'http://modal.test/alerts/');
    assert.equal(await page.locator('#outside').textContent(), 'page stays here');
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
});

test('POST keeps clicked action and CSRF; success stays inside modal', async t => {
    let received;
    const page = await setup(t, (route, request) => {
        received = {method: request.method(), body: request.postData()};
        return route.fulfill({contentType: 'text/html', body: markup('feishu',
            '<div data-flash-message class="alert alert-success">Saved</div>')});
    });
    await page.locator('button').click();
    await page.waitForFunction(() => document.querySelector('.modal').textContent.includes('Saved'));
    assert.equal(received.method, 'POST');
    assert.match(received.body, /name="action"\r\n\r\ntest/);
    assert.match(received.body, /name="csrfmiddlewaretoken"\r\n\r\ncsrf/);
    assert.equal(page.url(), 'http://modal.test/alerts/');
});

test('external submit buttons and other unsaved forms survive response replacement', async t => {
    const extra = '<form id="other" method="post" action="/other/"><input name="other-value" value="initial"></form>';
    const initial = markup('feishu', extra).replace('</div></div></div>',
        '<button id="external" form="config" type="submit" name="action" value="save">Save</button></div></div></div>');
    let received;
    const page = await setup(t, (route, request) => {
        received = request.postData();
        return route.fulfill({contentType: 'text/html', body: markup('feishu', extra + '<p data-flash-message>Done</p>')});
    }, initial);
    await page.locator('[name=other-value]').fill('keep me');
    await page.locator('#external').click();
    await page.waitForFunction(() => document.querySelector('.modal').textContent.includes('Done'));
    assert.match(received, /name="action"\r\n\r\nsave/);
    assert.equal(await page.locator('[name=other-value]').inputValue(), 'keep me');
});

test('500 keeps input and shows inline error without navigation', async t => {
    const page = await setup(t, route => route.fulfill({status: 500, body: 'failed'}));
    await page.locator('[name=name]').fill('keep on error');
    await page.locator('button').click();
    await page.waitForFunction(() => document.querySelector('[data-modal-feedback]')?.textContent.includes('500'));
    assert.equal(await page.locator('[name=name]').inputValue(), 'keep on error');
    assert.equal(page.url(), 'http://modal.test/alerts/');
});

test('bulk preview always loads fresh mode and counts instead of a cached form', async t => {
    const initial = markup().replace('alertChannelModal', 'bulkAnalysisModal').replace(
        'id="email" href="?alert_modal=channel&alert_channel_type=email"', 'id="email" href="?bulk_mode=latest"');
    const page = await setup(t, route => route.fulfill({contentType: 'text/html',
        body: initial.replace('Email</a>', 'LATEST-COUNT-17</a>')}), initial);
    await page.locator('#email').click();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.match(await page.locator('.modal').textContent(), /LATEST-COUNT-17/);
});

test('file upload uses clicked formaction and is not retried on double submit', async t => {
    let calls = 0;
    let received;
    let finish;
    const initial = markup().replace('<button type="submit"', '<input type="file" name="file"><button formaction="/upload/" type="submit"');
    const page = await setup(t, async (route, request) => {
        calls++;
        received = {url: request.url(), body: request.postData()};
        await new Promise(resolve => { finish = resolve; });
        return route.fulfill({contentType: 'text/html', body: markup()});
    }, initial);
    await page.locator('[name=file]').setInputFiles({name: 'people.csv', mimeType: 'text/csv', buffer: Buffer.from('name,id\nAlice,001')});
    await page.locator('button').click();
    await page.waitForFunction(() => document.querySelector('.modal').getAttribute('aria-busy') === 'true');
    await page.locator('form').evaluate(form => form.requestSubmit());
    assert.equal(calls, 1);
    assert.equal(received.url, 'http://modal.test/upload/');
    assert.match(received.body, /filename="people.csv"/);
    assert.match(received.body, /Alice,001/);
    finish();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
});

test('validation response keeps the dialog open and renders returned field errors', async t => {
    const page = await setup(t, route => route.fulfill({status: 400, contentType: 'text/html',
        body: markup('feishu', '<ul class="errorlist"><li>Name is required</li></ul>')}));
    await page.locator('button').click();
    await page.waitForFunction(() => !!document.querySelector('.errorlist'));
    assert.equal(page.url(), 'http://modal.test/alerts/');
    assert.equal(await page.locator('.modal.show').count(), 1);
});

test('save refreshes the visible table using its original filter and page scope', async t => {
    const table = text => `<div data-table-workspace data-table-key="devices">${text}</div>`;
    let readUrl;
    const page = await setup(t, (route, request) => {
        if (request.method() === 'GET') readUrl = request.url();
        return route.fulfill({contentType: 'text/html', body: markup() + table(
            request.method() === 'GET' ? 'FILTERED-NEW' : 'UNFILTERED')});
    }, markup() + table('FILTERED-OLD'));
    await page.evaluate(() => history.replaceState({}, '', '/alerts/?filter_name=core&page_size=50'));
    await page.locator('button').click();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.equal(await page.locator('[data-table-workspace]').textContent(), 'FILTERED-NEW');
    assert.equal(new URL(readUrl).searchParams.get('filter_name'), 'core');
    assert.equal(new URL(readUrl).searchParams.get('page_size'), '50');
});

test('returning to a profile draft restores the matching selector and hidden identity', async t => {
    const profile = id => markup().replace('alertChannelModal', 'profileConfigModal').replace('<button type="submit"',
        `<input type="hidden" name="profile_id" value="${id}"><select id="config-profile"><option value="a" ${id === 'a' ? 'selected' : ''}>A</option><option value="b" ${id === 'b' ? 'selected' : ''}>B</option></select><button type="submit"`);
    const page = await setup(t, (route, request) => route.fulfill({contentType: 'text/html',
        body: profile(new URL(request.url()).searchParams.get('task_profile'))}), profile('a'));
    await page.addScriptTag({path: path.resolve('static/app/js/inspections/task_ui.js')});
    await page.evaluate(() => window.AppTaskUI.bind(document));
    await page.locator('[name=name]').fill('profile A draft');
    await page.locator('#config-profile').selectOption('b');
    await page.waitForFunction(() => document.querySelector('[name=profile_id]').value === 'b');
    await page.locator('#config-profile').selectOption('a');
    await page.waitForFunction(() => document.querySelector('[name=profile_id]').value === 'a');
    assert.equal(await page.locator('#config-profile').inputValue(), 'a');
    assert.equal(await page.locator('[name=name]').inputValue(), 'profile A draft');
});

test('select-owned profile identity never reuses another profiles configuration', async t => {
    const profile = id => markup().replace('alertChannelModal', 'profileConfigModal').replace(
        '<button type="submit"', `<select name="profile_id" id="config-profile"><option value="a" ${id === 'a' ? 'selected' : ''}>A</option><option value="b" ${id === 'b' ? 'selected' : ''}>B</option></select><input name="cpu" value="${id === 'a' ? '50' : '90'}"><button type="submit"`);
    const page = await setup(t, (route, request) => route.fulfill({contentType: 'text/html',
        body: profile(new URL(request.url()).searchParams.get('task_profile'))}), profile('a'));
    await page.addScriptTag({path: path.resolve('static/app/js/inspections/task_ui.js')});
    await page.evaluate(() => window.AppTaskUI.bind(document));
    await page.locator('#config-profile').selectOption('b');
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.equal(await page.locator('[name=cpu]').inputValue(), '90');
    assert.equal(await page.locator('[name=profile_id]').inputValue(), 'b');
});

test('standalone source validation errors replace only their own form', async t => {
    const extra = '<form id="other" method="post" action="/other/"><input name="other-value" value="draft"></form>';
    const page = await setup(t, route => route.fulfill({status: 400, contentType: 'text/html',
        body: '<main id="main-content"><h1>Not saved</h1><form method="post" action="/save/"><input name="name" value="bad"><ul class="errorlist"><li>Invalid shared folder</li></ul><button>Retry</button></form></main>'}), markup('feishu', extra));
    await page.locator('#config button').click();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.match(await page.locator('.modal').textContent(), /Invalid shared folder/);
    assert.equal(await page.locator('#config [name=name]').inputValue(), 'bad');
    assert.equal(await page.locator('[name=other-value]').inputValue(), 'draft');
});

test('saving source preserves a different nondefault analysis profile and its file', async t => {
    const extra = id => `<form id="pc-analysis-config-form" method="post" action="/analysis/save/"><input type="hidden" name="profile_id" value="${id}"><input name="analysis-name" value="initial"><input name="policy" type="file"></form>`;
    const page = await setup(t, route => route.fulfill({contentType: 'text/html', body: markup('feishu', extra('a'))}), markup('feishu', extra('b')));
    await page.locator('[name=analysis-name]').fill('B draft');
    await page.locator('[name=policy]').setInputFiles({name: 'policy.ini', mimeType: 'text/plain', buffer: Buffer.from('policy')});
    await page.locator('#config button').click();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.equal(await page.locator('[name=profile_id]').inputValue(), 'b');
    assert.equal(await page.locator('[name=analysis-name]').inputValue(), 'B draft');
    assert.equal(await page.locator('[name=policy]').evaluate(input => input.files[0]?.name), 'policy.ini');
});

test('queued task response stays in dialog with a task detail link', async t => {
    const page = await setup(t, async route => {
        return route.fulfill({contentType: 'text/html', body: '<main id="main-content"><h1>Task</h1><p data-flash-message>后台运行</p></main>'});
    });
    // Supply the final fetch-response URL without allowing a fake hostname's
    // redirect to escape Playwright's first-request-only route interception.
    await page.evaluate(() => {
        const fetchResponse = window.fetch;
        window.fetch = async (...args) => {
            const response = await fetchResponse(...args);
            Object.defineProperty(response, 'url', {value: 'http://modal.test/tasks/abc/'});
            return response;
        };
    });
    await page.locator('button').click();
    await page.waitForFunction(() => !document.querySelector('.modal').hasAttribute('aria-busy'));
    assert.equal(await page.locator('[data-modal-result] a[href="http://modal.test/tasks/abc/"]').count(), 1,
        await page.locator('.modal').textContent());
    assert.equal(page.url(), 'http://modal.test/alerts/');
});
