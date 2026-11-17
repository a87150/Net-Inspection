import test from 'node:test';
import assert from 'node:assert';

import { ApiError, apiRequest, buildUrl, csrfToken } from '../../static/app/vue/api.mjs';

const ORIGIN = 'http://127.0.0.1:8000';

function fakeDocument({inputValue = '', cookie = ''} = {}) {
    return {
        cookie,
        querySelector: (selector) => (
            selector === 'input[name="csrfmiddlewaretoken"]' && inputValue
                ? {value: inputValue} : null
        ),
    };
}

function stubGlobals(documentStub) {
    globalThis.document = documentStub;
}

function fakeResponse({status = 200, body = null, contentType = 'application/json'} = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: {get: () => contentType},
        json: async () => body,
    };
}

test('buildUrl keeps only the parameters that carry a value', () => {
    const url = buildUrl('/api/v1/x/', {state: 'active', page: 2, empty: '', missing: null}, ORIGIN);
    assert.equal(url.pathname, '/api/v1/x/');
    assert.equal(url.searchParams.get('state'), 'active');
    assert.equal(url.searchParams.get('page'), '2');
    assert.equal(url.searchParams.has('empty'), false);
    assert.equal(url.searchParams.has('missing'), false);
});

test('csrfToken prefers the rendered hidden input over the cookie', () => {
    stubGlobals(fakeDocument({inputValue: 'from-input', cookie: 'csrftoken=from-cookie'}));
    assert.equal(csrfToken(), 'from-input');
});

test('csrfToken falls back to the cookie when no input is rendered', () => {
    stubGlobals(fakeDocument({cookie: 'a=1; csrftoken=cookie-token'}));
    assert.equal(csrfToken(), 'cookie-token');
});

test('GET sends the CSRF-free accept header and no body', async () => {
    stubGlobals(fakeDocument({inputValue: 'token'}));
    let seen = null;
    globalThis.fetch = async (url, init) => {
        seen = {url: String(url), init};
        return fakeResponse({body: {count: 0, results: []}});
    };
    const payload = await apiRequest('/api/v1/x/', {params: {state: 'active'}, origin: ORIGIN});
    assert.deepEqual(payload, {count: 0, results: []});
    assert.equal(seen.init.method, 'GET');
    assert.equal(seen.init.body, undefined);
    assert.equal(seen.init.headers['X-CSRFToken'], undefined);
    assert.match(seen.url, /state=active/);
});

test('writes carry the CSRF token and a JSON body', async () => {
    stubGlobals(fakeDocument({inputValue: 'token-123'}));
    let seen = null;
    globalThis.fetch = async (url, init) => {
        seen = {url: String(url), init};
        return fakeResponse({status: 201, body: {created: 2}});
    };
    const payload = await apiRequest('/api/v1/x/', {
        method: 'POST', body: {target_ids: ['a']}, origin: ORIGIN,
    });
    assert.deepEqual(payload, {created: 2});
    assert.equal(seen.init.method, 'POST');
    assert.equal(seen.init.headers['X-CSRFToken'], 'token-123');
    assert.equal(seen.init.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(seen.init.body), {target_ids: ['a']});
});

test('a failed request surfaces the server message as ApiError', async () => {
    stubGlobals(fakeDocument({inputValue: 'token'}));
    globalThis.fetch = async () => fakeResponse({
        status: 400, body: {message: '结束时间必须晚于开始时间。', errors: {}},
    });
    await assert.rejects(
        () => apiRequest('/api/v1/x/', {method: 'POST', body: {}, origin: ORIGIN}),
        (error) => {
            assert.ok(error instanceof ApiError);
            assert.equal(error.status, 400);
            assert.equal(error.message, '结束时间必须晚于开始时间。');
            return true;
        },
    );
});

test('an empty body answers a fallback message', async () => {
    stubGlobals(fakeDocument({inputValue: 'token'}));
    globalThis.fetch = async () => fakeResponse({status: 403, contentType: 'text/html'});
    await assert.rejects(
        () => apiRequest('/api/v1/x/', {origin: ORIGIN}),
        (error) => error.message.includes('403'),
    );
});

test('204 resolves to null instead of trying to parse JSON', async () => {
    stubGlobals(fakeDocument({inputValue: 'token'}));
    globalThis.fetch = async () => fakeResponse({status: 204, body: null});
    assert.equal(await apiRequest('/api/v1/x/', {method: 'DELETE', origin: ORIGIN}), null);
});
