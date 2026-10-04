/* 测试回显解析：就地显示结果，不再整页刷新。
   无 JS 时仍走原来的整页提交路径（服务端 preview_item 分支保持不变）。 */
(function () {
    'use strict';

    document.addEventListener('submit', async function (event) {
        const submitter = event.submitter;
        if (!submitter || submitter.name !== 'preview_item' || !submitter.value) return;
        const form = event.target;
        if (!form || form.id !== 'collectionSettingsForm') return;
        event.preventDefault();

        const scope = submitter.closest('[data-rule-editor]') || form;
        const result = scope.querySelector('[data-preview-result]');
        const error = scope.querySelector('[data-preview-error]');
        const clear = (node) => { if (node) { node.textContent = ''; node.hidden = true; } };
        const show = (node, text) => { if (node) { node.textContent = text; node.hidden = false; } };

        clear(result);
        clear(error);
        submitter.disabled = true;
        try {
            const response = await fetch(form.action, {
                method: 'POST', body: new FormData(form, submitter),
                credentials: 'same-origin', cache: 'no-store',
                headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}
            });
            if (response.redirected || response.status === 403) throw new Error('登录已失效或无管理员权限，请重新登录。');
            const data = await response.json().catch(() => null);
            if (!data) throw new Error('服务端未返回解析结果，请先补全必填项后重试。');
            if (!response.ok) throw new Error(data.message || '解析失败，请稍后重试。');
            show(result, data.result || '(解析结果为空)');
        } catch (failure) {
            show(error, failure.message || '解析失败，请稍后重试。');
        } finally {
            submitter.disabled = false;
        }
    });
}());
