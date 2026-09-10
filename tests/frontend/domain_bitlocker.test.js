const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = fs.readFileSync('static/app/js/domain/bitlocker.js', 'utf8');
function setup(fetch) {
  const element = () => ({textContent: '', disabled: false, children: [], listeners: {},
    addEventListener(name, cb) {this.listeners[name] = cb;},
    appendChild(child) {this.children.push(child);}, replaceChildren() {this.children = [];}});
  const modal = element(), form = element(), status = element(), results = element(), submit = element(), name = element();
  modal.querySelector = selector => ({'[data-bitlocker-form]': form, '[data-bitlocker-status]': status,
    '[data-bitlocker-results]': results, '[data-bitlocker-submit]': submit, '[data-bitlocker-computer]': name})[selector];
  vm.runInNewContext(code, {document: {getElementById: () => modal, createElement: element},
    window: {addEventListener() {}}, FormData: class {}, AbortController, fetch});
  const open = url => modal.listeners['show.bs.modal']({relatedTarget: {dataset: {bitlockerUrl: url, computerName: 'PC'}}});
  const send = () => form.listeners.submit({preventDefault() {}});
  return {modal, form, status, results, submit, open, send};
}
test('POST uses the selected computer and renders secrets as text only, then clears on close', async () => {
  let requested;
  const ui = setup(async (url, options) => {requested = {url, options}; return {ok: true, json: async () => ({
    message: 'ok', records: [{key_id: '<script>', password: 'secret', created_at: ''}]})};});
  ui.open('/computer/one'); await ui.send();
  assert.equal(requested.url, '/computer/one'); assert.equal(requested.options.method, 'POST');
  assert.equal(requested.options.cache, 'no-store');
  assert.equal(ui.results.children[0].children[0].textContent, '密钥 ID：<script>');
  ui.modal.listeners['hide.bs.modal'](); assert.equal(ui.results.children.length, 0);
});
test('late JSON from a closed modal cannot reveal a previous computer key', async () => {
  let resolveJson;
  const ui = setup(async () => ({ok: true, json: () => new Promise(resolve => {resolveJson = resolve;})}));
  ui.open('/computer/old'); const pending = ui.send(); await new Promise(setImmediate);
  ui.modal.listeners['hide.bs.modal'](); ui.open('/computer/new');
  resolveJson({message: 'old', records: [{key_id: 'old', password: 'secret'}]}); await pending;
  assert.equal(ui.results.children.length, 0); assert.equal(ui.status.textContent, '');
});
test('lookup failure keeps modal available for retry without rendering secrets', async () => {
  const ui = setup(async () => ({ok: false, status: 400, json: async () => ({message: 'LDAP 权限不足'})}));
  ui.open('/computer/one'); await ui.send();
  assert.equal(ui.status.textContent, 'LDAP 权限不足'); assert.equal(ui.submit.disabled, false);
  assert.equal(ui.results.children.length, 0);
});
