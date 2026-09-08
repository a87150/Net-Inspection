const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context = {module: {exports: {}}, document: {querySelectorAll: () => []}};
vm.runInNewContext(fs.readFileSync(require.resolve('../../static/app/js/pc_source.js'), 'utf8'), context);

function formFixture() {
    const controls = Object.fromEntries([
        'source_type', 'file_time_mode', 'port', 'shared_path', 'host', 'ftp_directory',
        'domain', 'ftp_passive', 'ftp_use_tls', 'recent_days', 'range_start_date', 'range_end_date',
        'smb_auth_mode', 'username', 'password',
    ].map(name => [name, {value: '', disabled: false, addEventListener(event, fn) { this[event] = fn; }}]));
    controls.source_type.value = 'smb';
    controls.smb_auth_mode.value = 'system';
    controls.file_time_mode.value = 'recent_days';
    controls.port.value = '445';
    const groups = Object.fromEntries(Object.keys(controls).map(name => [name, {
        hidden: false, querySelectorAll: () => [controls[name]],
    }]));
    const form = {
        querySelector(selector) {
            const name = selector.match(/="([^"]+)"/)?.[1];
            return selector.startsWith('[name=') ? controls[name] : groups[name];
        },
        addEventListener(event, fn) { this[event] = fn; },
    };
    return {form, controls, groups};
}

test('shared-folder mode hides and disables FTP fields without clearing values', () => {
    assert.equal(typeof context.module.exports.initSourceForm, 'function');
    const {form, controls, groups} = formFixture();
    controls.host.value = 'saved-ftp.test';
    context.module.exports.initSourceForm(form);
    assert.equal(groups.host.hidden, true);
    assert.equal(controls.host.disabled, true);
    assert.equal(controls.host.value, 'saved-ftp.test');
    assert.equal(controls.shared_path.disabled, false);
    assert.equal(controls.range_start_date.disabled, true);
});

test('protocol and date switches submit only applicable fields and keep custom ports', () => {
    assert.equal(typeof context.module.exports.initSourceForm, 'function');
    const {form, controls, groups} = formFixture();
    context.module.exports.initSourceForm(form);
    controls.source_type.value = 'ftp';
    controls.source_type.change();
    assert.equal(controls.port.value, '21');
    assert.equal(groups.host.hidden, false);
    assert.equal(controls.shared_path.disabled, true);
    assert.equal(controls.ftp_use_tls.disabled, false);
    controls.port.value = '2121';
    controls.source_type.change();
    assert.equal(controls.port.value, '2121');
    controls.file_time_mode.value = 'date_range';
    controls.file_time_mode.change();
    assert.equal(controls.recent_days.disabled, true);
    assert.equal(controls.range_start_date.disabled, false);
});

test('invalid advanced input opens its collapsed settings so it can be corrected', () => {
    assert.equal(typeof context.module.exports.initSourceForm, 'function');
    const {form} = formFixture();
    context.module.exports.initSourceForm(form);
    const details = {open: false};
    form.invalid({target: {closest: () => details}});
    assert.equal(details.open, true);
});

test('system identity hides credentials until manual identity is explicitly selected', () => {
    const {form, controls, groups} = formFixture();
    context.module.exports.initSourceForm(form);
    assert.equal(groups.password.hidden, true);
    assert.equal(controls.username.disabled, true);
    controls.smb_auth_mode.value = 'credentials';
    controls.smb_auth_mode.change();
    assert.equal(groups.password.hidden, false);
    assert.equal(controls.username.disabled, false);
    assert.equal(groups.domain.hidden, false);
});
