const test = require('node:test');
const assert = require('node:assert/strict');

const {createPeopleTaskController} = require('../../static/app/js/people/import_tasks.js');

function fixture(payloads) {
    const posts = [];
    const state = {running: [], message: '', href: '', shows: 0};
    const root = {
        dataset: {statusUrl: '/status/', csrfToken: 'csrf'},
        querySelector(selector) {
            return {
                '[data-people-running-list]': {replaceChildren: (...items) => { state.running = items; }},
                '[data-people-completion-message]': {set textContent(value) { state.message = value; }},
                '[data-people-completion-jump]': {
                    set href(value) { state.href = value; }, addEventListener() {},
                },
                '[data-people-completion-close]': {addEventListener() {}},
                '#peopleTaskCompletionModal': {},
            }[selector];
        },
    };
    const document = {
        querySelector: () => root,
        createElement(tag) {
            return {
                tag, className: '', textContent: '', type: '', dataset: {}, children: [],
                append(...children) { this.children.push(...children); },
                addEventListener(_name, handler) { this.handler = handler; },
            };
        },
    };
    const window = {
        location: {assignCalls: [], assign(value) { this.assignCalls.push(value); }},
        bootstrap: {Modal: {getOrCreateInstance: () => ({show: () => { state.shows += 1; }})}},
        setTimeout: () => 1,
        clearTimeout() {},
    };
    const fetchImpl = async (url, options = {}) => {
        if (options.method === 'POST') {
            posts.push([url, options.body]);
            return {ok: true, json: async () => ({acknowledged: true})};
        }
        return {ok: true, json: async () => payloads.shift() || {tasks: []}};
    };
    return {controller: createPeopleTaskController({document, window, fetchImpl}), state, posts, window};
}

test('poll renders running notice without navigating or reloading', async () => {
    const task = {id: '1', message: '飞书预览正在后台运行。', status: 'running',
        show_running: true, show_terminal: false, ack_url: '/ack/1/', jump_url: '/task/1/'};
    const {controller, state, window} = fixture([{tasks: [task]}]);

    await controller.poll();

    assert.equal(state.running.length, 1);
    assert.match(state.running[0].textContent, /飞书预览正在后台运行/);
    assert.deepEqual(window.location.assignCalls, []);
});

test('running acknowledgement does not prevent one terminal popup', async () => {
    const running = {id: '2', message: '钉钉测试正在后台运行。', status: 'running',
        show_running: true, show_terminal: false, ack_url: '/ack/2/', jump_url: '/task/2/'};
    const done = {...running, message: '连接测试已完成。', status: 'success',
        show_running: false, show_terminal: true};
    const {controller, state, posts} = fixture([{tasks: [running]}, {tasks: [done]}, {tasks: [done]}]);

    await controller.poll();
    await controller.dismissRunning(running);
    await controller.poll();
    await controller.poll();

    assert.deepEqual(posts[0], ['/ack/2/', 'kind=running']);
    assert.equal(state.shows, 1);
    assert.equal(state.href, '/task/2/');
});

test('terminal acknowledgement posts terminal kind', async () => {
    const task = {id: '3', ack_url: '/ack/3/'};
    const {controller, posts} = fixture([]);

    await controller.acknowledgeTerminal(task);

    assert.deepEqual(posts[0], ['/ack/3/', 'kind=terminal']);
});
