/* Shared in-dialog navigation. Writes remain CSRF-protected POSTs. */
(function () {
    'use strict';
    const modalFeedback = typeof module === 'object' && module.exports
        ? require('./modal_feedback.js')
        : window.AppModalFeedback;
    const identities = new Set(['profile_id', 'channel_id', 'channel_type', 'scope', 'provider', 'source_id', 'policy_id']);
    const switchParameters = ['alert_modal', 'task_modal', 'bulk_mode'];

    function formKey(form) {
        return JSON.stringify([form.id, form.getAttribute('action') || '',
            Array.from(form.elements).filter(field => (field.type === 'hidden' || field.tagName === 'SELECT') && identities.has(field.name))
                .map(field => [field.name, field.tagName === 'SELECT'
                    ? Array.from(field.options).find(option => option.defaultSelected)?.value ?? field.value
                    : field.value])]);
    }
    function contentKey(content) {
        return JSON.stringify(Array.from(content.querySelectorAll('form')).map(formKey));
    }
    function restoreProfileChoice(root) {
        root.querySelectorAll('#task-profile, #config-profile, [data-pc-profile-switch]').forEach(select => {
            const original = Array.from(select.options).find(option => option.defaultSelected);
            if (original) select.value = original.value;
        });
    }
    function buildRequest(form, submitter, win) {
        const url = new URL(submitter?.getAttribute('formaction') || form.getAttribute('action') || win.location.href, win.location.href);
        const method = (submitter?.getAttribute('formmethod') || form.method || 'get').toUpperCase();
        const body = new win.FormData(form);
        if (submitter?.name) body.append(submitter.name, submitter.value);
        const options = {method, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}};
        if (method === 'GET') {
            url.search = new URLSearchParams(body).toString();
            url.searchParams.delete('csrfmiddlewaretoken');
        } else options.body = body;
        return {url: url.toString(), options};
    }

    function installModalTransport(doc, win) {
        const states = new WeakMap();
        const stateFor = modal => {
            if (!states.has(modal)) states.set(modal, {drafts: new Map(), busy: false});
            return states.get(modal);
        };
        function feedback(modal, text, kind = 'danger') {
            modalFeedback.showModalFeedback(doc, modal, text, kind);
        }
        function rebind(modal) {
            win.AppConditionalFields?.bindScheduleFields(modal);
            win.AppTaskUI?.bind(modal);
            win.AppPCSource?.bind(modal);
            win.AppFormAccessibility?.enhanceFormErrors(modal, doc);
            doc.dispatchEvent(new win.CustomEvent('app:modal-updated', {detail: {modal}}));
        }
        async function updateTables(page, response, modal) {
            if (!doc.querySelector('[data-table-workspace]') || !page.querySelector('[data-table-workspace]')) return;
            const tableScope = href => {
                const url = new URL(href, win.location.href);
                const query = Array.from(url.searchParams).filter(([key]) => /^(filter_|sort|direction|page$|page_size$|q$|status$|target$|search$)/.test(key));
                return JSON.stringify([url.pathname, query.sort()]);
            };
            if (tableScope(response.url || win.location.href) !== tableScope(win.location.href)) {
                try {
                    const refreshed = await win.fetch(win.location.href, {credentials: 'same-origin',
                        headers: {'X-Requested-With': 'XMLHttpRequest'}});
                    if (!refreshed.ok || refreshed.redirected) throw new Error('Table refresh unavailable');
                    page = new win.DOMParser().parseFromString(await refreshed.text(), 'text/html');
                } catch (_) {
                    feedback(modal, '操作结果已返回，但列表更新失败；可稍后重新查询列表。', 'warning');
                    return;
                }
            }
            const replacements = page.querySelectorAll('[data-table-workspace]');
            let storage = null;
            try { storage = win.localStorage; } catch (_) { /* Optional preferences. */ }
            doc.querySelectorAll('[data-table-workspace]').forEach(current => {
                if (current.closest('.modal')) return;
                const replacement = Array.from(replacements).find(item => item.dataset.tableKey === current.dataset.tableKey);
                if (replacement) {
                    const updated = replacement.cloneNode(true);
                    current.replaceWith(updated);
                    win.AppTableWorkspace?.initializeWorkspace(updated, storage);
                }
            });
            win.AppTaskUI?.bind(doc);
        }
        async function render(modal, page, response, submittedForm) {
            const state = stateFor(modal);
            const current = modal.querySelector('.modal-content');
            const incoming = page.getElementById(modal.id)?.querySelector('.modal-content');
            const messages = Array.from(page.querySelectorAll('[data-flash-message]'));
            if (page.querySelector('input[name="username"]') && /\/login\//.test(new URL(response.url || win.location.href).pathname)) {
                throw new Error('登录状态已失效，请重新登录后再操作。');
            }
            if (incoming) {
                let replacement = incoming.cloneNode(true);
                if (!submittedForm) {
                    // Only configuration editors have reusable drafts. Counts,
                    // batch previews and execution scopes must always be fresh.
                    if (['alertChannelModal', 'profileConfigModal'].includes(modal.id)) {
                        restoreProfileChoice(current);
                        state.drafts.set(contentKey(current), current);
                        replacement = state.drafts.get(contentKey(incoming)) || replacement;
                    }
                } else {
                    state.drafts.clear();
                    // PC source and analysis settings are independent forms.
                    const untouched = Array.from(current.querySelectorAll('form')).filter(form => form !== submittedForm);
                    replacement.querySelectorAll('form').forEach(form => {
                        const original = untouched.find(other => formKey(other) === formKey(form)
                            || (form.id && other.id === form.id && other.getAttribute('action') === form.getAttribute('action')));
                        if (original) form.replaceWith(original);
                    });
                }
                const activeTab = current.querySelector('[data-bs-toggle="tab"].active')?.id;
                current.replaceWith(replacement);
                if (replacement.querySelector('[data-config-saved]')) state.reloadOnClose = true;
                // Hidden forms outside the dialog are used by PC test/preview buttons.
                page.querySelectorAll('form[data-modal-owner]').forEach(form => {
                    if (form.dataset.modalOwner !== modal.id || form.closest('.modal')) return;
                    const old = doc.getElementById(form.id);
                    if (old) old.replaceWith(form.cloneNode(true));
                    else modal.after(form.cloneNode(true));
                });
                modal.querySelectorAll('[data-flash-message], [data-modal-feedback], [data-modal-result]').forEach(item => item.remove());
                const body = modal.querySelector('.modal-body');
                messages.forEach(message => body.prepend(message.cloneNode(true)));
                rebind(modal);
                if (activeTab) {
                    const tab = doc.getElementById(activeTab);
                    if (tab && modal.contains(tab)) win.bootstrap?.Tab.getOrCreateInstance(tab).show();
                }
            } else if (submittedForm) {
                const errorForm = !response.ok && Array.from(page.querySelectorAll('form')).find(form =>
                    new URL(form.getAttribute('action') || response.url, win.location.href).pathname
                        === new URL(submittedForm.getAttribute('action') || win.location.href, win.location.href).pathname);
                if (errorForm && modal.contains(submittedForm)) {
                    const replacement = errorForm.cloneNode(true);
                    replacement.id = submittedForm.id;
                    submittedForm.replaceWith(replacement);
                    modal.querySelector('[data-modal-feedback]')?.remove();
                    messages.forEach(message => modal.querySelector('.modal-body').prepend(message.cloneNode(true)));
                    rebind(modal);
                    win.bootstrap?.Modal.getInstance(modal)?.handleUpdate();
                    return;
                }
                // Task creation and source preview return a result page, not a form.
                const main = page.querySelector('#main-content');
                if (!main) throw new Error('未收到有效结果，请刷新页面确认登录状态。');
                modal.querySelector('[data-modal-feedback]')?.remove();
                modal.querySelector('[data-modal-result]')?.remove();
                const result = doc.createElement('section');
                result.dataset.modalResult = '';
                result.className = 'border rounded p-3 mb-3';
                messages.forEach(message => result.append(message.cloneNode(true)));
                const url = new URL(response.url || win.location.href);
                if (/\/tasks\/|\/operations\//.test(url.pathname)) {
                    const message = doc.createElement('p');
                    message.textContent = '操作已提交，可关闭此窗口，稍后查看任务进度与结果。';
                    result.append(message);
                    const link = doc.createElement('a');
                    link.href = url.toString();
                    link.textContent = '查看任务详情';
                    result.append(link);
                } else {
                    const contents = main.cloneNode(true);
                    contents.querySelectorAll('script, .modal, [data-flash-message], form').forEach(item => item.remove());
                    contents.removeAttribute('id');
                    result.append(contents);
                }
                modal.querySelector('.modal-body').prepend(result);
            } else throw new Error('未找到需要切换的表单，请刷新后重试。');
            modal.dataset.loadedUrl = response.url || win.location.href;
            if (submittedForm && response.ok) await updateTables(page, response, modal);
            win.bootstrap?.Modal.getInstance(modal)?.handleUpdate();
        }

        async function request(modal, url, options = {}, submittedForm = null) {
            const state = stateFor(modal);
            if (state.busy) return;
            if (new URL(url, win.location.href).origin !== win.location.origin) return;
            state.busy = true;
            const buttons = Array.from(modal.querySelectorAll('button:not([data-bs-dismiss])'));
            const disabled = buttons.map(button => button.disabled);
            const controls = Array.from(modal.querySelectorAll('select'));
            const disabledSelects = controls.map(select => select.disabled);
            buttons.forEach(button => { button.disabled = true; });
            // Prevent another profile change from submitting old profile values.
            controls.forEach(select => { select.disabled = true; });
            modal.setAttribute('aria-busy', 'true');
            feedback(modal, submittedForm ? '正在提交，请稍候…' : '正在加载表单…', 'info');
            try {
                const response = await win.fetch(url, {credentials: 'same-origin',
                    headers: {'X-Requested-With': 'XMLHttpRequest'}, ...options});
                if (!response.ok && ![400, 422].includes(response.status)) {
                    throw new Error(`操作未完成（HTTP ${response.status}），请检查后重试；不要重复提交正在执行的任务。`);
                }
                const page = new win.DOMParser().parseFromString(await response.text(), 'text/html');
                // Restore before caching detached DOM drafts.
                buttons.forEach((button, i) => { button.disabled = disabled[i]; });
                controls.forEach((select, i) => { select.disabled = disabledSelects[i]; });
                await render(modal, page, response, submittedForm);
            } catch (error) {
                if (!submittedForm) restoreProfileChoice(modal);
                feedback(modal, error.message || '连接中断，请先确认操作结果再重试。');
            } finally {
                buttons.forEach((button, i) => { button.disabled = disabled[i]; });
                controls.forEach((select, i) => { select.disabled = disabledSelects[i]; });
                state.busy = false;
                modal.removeAttribute('aria-busy');
            }
        }
        function navigate(modal, url) { return request(modal, url); }
        doc.addEventListener('submit', event => {
            if (event.defaultPrevented) return;
            const form = event.target;
            const modal = form.closest('.modal') || event.submitter?.closest('.modal')
                || doc.getElementById(form.dataset.modalOwner || '');
            if (!modal || form.dataset.nativeSubmit !== undefined) return;
            const req = buildRequest(form, event.submitter, win);
            if (new URL(req.url).origin !== win.location.origin
                || new URL(req.url).pathname.startsWith('/integrations/people/')) return;
            event.preventDefault();
            return request(modal, req.url, req.options, req.options.method === 'POST' ? form : null);
        });
        doc.addEventListener('click', event => {
            if (event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            const link = event.target.closest('a[href]');
            if (!link || link.hasAttribute('download') || link.target || link.hasAttribute('data-bs-toggle')) return;
            const href = link.getAttribute('href');
            if (href.startsWith('#')) return;
            let modal = link.closest('.modal');
            const url = new URL(href, modal?.dataset.loadedUrl || win.location.href);
            if (url.origin !== win.location.origin || !(switchParameters.some(key => url.searchParams.has(key))
                || (modal && link.hasAttribute('data-modal-navigate')) || link.hasAttribute('data-modal-load'))) return;
            if (!modal && link.hasAttribute('data-modal-load')) {
                modal = doc.getElementById(link.dataset.modalLoad);
                if (modal) win.bootstrap?.Modal.getOrCreateInstance(modal).show();
            }
            if (!modal && link.hasAttribute('data-single-device-run')) {
                modal = doc.getElementById('runTaskModal');
                if (modal) win.bootstrap?.Modal.getOrCreateInstance(modal).show();
            }
            if (!modal && url.searchParams.has('bulk_mode')) {
                modal = doc.getElementById('bulkAnalysisModal');
                if (modal) win.bootstrap?.Modal.getOrCreateInstance(modal).show();
            }
            if (!modal) return;
            event.preventDefault();
            return navigate(modal, url.toString());
        });
        doc.addEventListener('hidden.bs.modal', event => {
            const state = states.get(event.target);
            if (state) state.drafts.clear(); // Credentials never enter browser storage.
            if (state?.reloadOnClose) win.location.reload();
        });
        return {navigate};
    }
    if (typeof module !== 'undefined') module.exports = {buildRequest, installModalTransport};
    if (typeof document !== 'undefined') window.AppModalTransport = installModalTransport(document, window);
}());
