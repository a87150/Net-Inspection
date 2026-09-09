const {test} = require('node:test');
const assert = require('node:assert/strict');

test('saved modal response updates day labels and both object counts without navigation', () => {
    const {updateStatistics} = require('../../static/app/js/domain/statistics.js');
    const days = [{textContent: '60'}, {textContent: '60'}, {textContent: '60'}];
    const counter = () => ({textContent: '9', classes: {}, classList: {toggle(name, value) {this.owner.classes[name] = value;}}});
    const account = counter(), computer = counter();
    account.classList.owner = account;
    computer.classList.owner = computer;
    const doc = {
        querySelectorAll: () => days,
        querySelector: selector => selector.includes('account') ? account : computer,
    };
    const modal = {id: 'domainStatisticsModal', querySelector: () => ({dataset: {days: '30', account: '2', computer: '0'}})};
    updateStatistics(doc, modal);
    assert.deepEqual(days.map(item => item.textContent), ['30', '30', '30']);
    assert.equal(account.textContent, '2');
    assert.equal(computer.textContent, '0');
    assert.equal(account.classes['text-danger'], true);
    assert.equal(computer.classes['text-danger'], false);
});
