'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

let controller = {};
try {
    controller = require('../../static/app/js/common/form_accessibility.js');
} catch (_error) {
    controller = {};
}

test('field error is linked to its control without discarding existing help text', () => {
    assert.equal(typeof controller.linkFieldError, 'function');
    const attributes = {'aria-describedby': 'field-help'};
    const control = {
        getAttribute: name => attributes[name] || '',
        setAttribute: (name, value) => { attributes[name] = value; },
    };
    const error = {id: ''};

    controller.linkFieldError(control, error, 3);

    assert.equal(attributes['aria-invalid'], 'true');
    assert.equal(error.id, 'field-error-3');
    assert.equal(attributes['aria-describedby'], 'field-help field-error-3');
});

test('linking the same error twice does not duplicate aria-describedby', () => {
    assert.equal(typeof controller.linkFieldError, 'function');
    const attributes = {};
    const control = {
        getAttribute: name => attributes[name] || '',
        setAttribute: (name, value) => { attributes[name] = value; },
    };
    const error = {id: 'known-error'};

    controller.linkFieldError(control, error, 1);
    controller.linkFieldError(control, error, 1);

    assert.equal(attributes['aria-describedby'], 'known-error');
});
