const test = require('node:test');
const assert = require('node:assert/strict');
const {submitPeopleModal} = require('../../static/app/js/people/modal.js');

test('submits POST without navigating and displays validation HTML in current modal', async () => {
    let result;
    await submitPeopleModal({action: 'http://localhost/integrations/people/preview/'}, {
        body: 'provider=feishu',
        fetchImpl: async (url, options) => {
            assert.equal(options.method, 'POST');
            assert.equal(options.body, 'provider=feishu');
            return {status: 400, ok: false, text: async () => '<div>预览失效</div>'};
        },
        render: html => { result = html; },
    });
    assert.equal(result, '<div>预览失效</div>');
});

test('server errors are not rendered as a replacement page', async () => {
    let rendered = false;
    await assert.rejects(submitPeopleModal({action: '/preview/'}, {
        body: '', fetchImpl: async () => ({status: 405, ok: false}),
        render: () => { rendered = true; },
    }));
    assert.equal(rendered, false);
});
