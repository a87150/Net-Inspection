const test = require('node:test');
const assert = require('node:assert/strict');
const {initAnalysisProblems} = require('../../static/app/js/inspections/analysis_problems.js');

function fixture(fetcher) {
    const events = {};
    const body = {textContent: '', innerHTML: ''};
    const title = {textContent: ''};
    const modal = {
        querySelector: selector => selector === '[data-problem-body]' ? body : title,
        addEventListener: (event, handler) => { events[event] = handler; },
    };
    initAnalysisProblems(modal, fetcher);
    return {events, body, title, open: category => events['show.bs.modal']({
        relatedTarget: {dataset: {problemUrl: `/problems/?category=${category}`, problemLabel: category}},
    })};
}

test('opening and pagination load content inside the same modal', async () => {
    const f = fixture(async url => ({ok: true, text: async () => `rows:${url}`}));
    await f.open('software');
    assert.equal(f.title.textContent, 'software问题明细');
    assert.equal(f.body.innerHTML, 'rows:/problems/?category=software');
    let prevented = false;
    await f.events.click({preventDefault() { prevented = true; }, target: {
        closest: () => ({getAttribute: () => '/problems/?category=software&page=2'}),
    }});
    assert.equal(prevented, true);
    assert.equal(f.body.innerHTML, 'rows:/problems/?category=software&page=2');
});

test('slow previous response never overwrites a newer category', async () => {
    let resolveOld;
    const f = fixture(url => url.includes('old') ? new Promise(resolve => {resolveOld = resolve;})
        : Promise.resolve({ok: true, text: async () => 'new rows'}));
    const old = f.open('old');
    await f.open('new');
    resolveOld({ok: true, text: async () => 'old rows'});
    await old;
    assert.equal(f.body.innerHTML, 'new rows');
});

test('login redirects and server failures are messages, not injected pages', async () => {
    for (const response of [{ok: true, redirected: true}, {ok: false}]) {
        const f = fixture(async () => ({...response, text: async () => '<html>private</html>'}));
        await f.open('software');
        assert.equal(f.body.innerHTML, '');
        assert.match(f.body.textContent, /重新登录|加载失败/);
    }
});

test('closing modal ignores an outstanding response', async () => {
    let resolve;
    const f = fixture(() => new Promise(done => {resolve = done;}));
    const pending = f.open('software');
    f.events['hidden.bs.modal']();
    resolve({ok: true, text: async () => 'late rows'});
    await pending;
    assert.equal(f.body.innerHTML, '');
    assert.equal(f.body.textContent, '');
});
