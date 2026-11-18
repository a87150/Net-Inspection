/* 模态框表单的 fetch 提交。
   失败时把错误留在框里，成功时整页刷新让 flash 消息出现。
   普通表单（没有 data-form-ajax）不受影响。 */
(function () {
    'use strict';

    function errorBox(form) {
        var body = form.querySelector('.modal-body') || form;
        var box = body.querySelector('[data-form-ajax-errors]');
        if (!box) {
            box = document.createElement('div');
            box.className = 'alert alert-danger';
            box.setAttribute('role', 'alert');
            box.setAttribute('data-form-ajax-errors', '');
            body.insertBefore(box, body.firstChild);
        }
        return box;
    }

    function showErrors(form, title, errors) {
        var box = errorBox(form);
        box.textContent = '';
        var heading = document.createElement('strong');
        heading.textContent = title;
        box.appendChild(heading);
        if (errors.length) {
            var list = document.createElement('ul');
            list.className = 'mb-0';
            errors.forEach(function (message) {
                var item = document.createElement('li');
                item.textContent = message;
                list.appendChild(item);
            });
            box.appendChild(list);
        }
        box.scrollIntoView({ block: 'nearest' });
    }

    function clearErrors(form) {
        var box = form.querySelector('[data-form-ajax-errors]');
        if (box) {
            box.remove();
        }
    }

    document.addEventListener('submit', function (event) {
        var form = event.target.closest('form[data-form-ajax]');
        if (!form) {
            return;
        }
        event.preventDefault();
        var submitter = form.querySelector('[type="submit"]');
        if (submitter) {
            if (submitter.disabled) {
                return;
            }
            submitter.disabled = true;
        }
        clearErrors(form);
        fetch(form.action, {
            method: 'POST',
            body: new FormData(form),
            headers: { 'X-Form-Ajax': '1' },
            credentials: 'same-origin',
        }).then(function (response) {
            var type = response.headers.get('content-type') || '';
            if (type.indexOf('application/json') === -1) {
                /* 成功：服务端已经重定向到带 flash 消息的页面。 */
                window.location.assign(response.url || form.action);
                return;
            }
            return response.json().then(function (payload) {
                if (payload.ok) {
                    window.location.assign(payload.redirect || response.url || form.action);
                    return;
                }
                showErrors(form, '保存失败', payload.errors || []);
            });
        }).catch(function () {
            showErrors(form, '保存失败', ['网络异常，请重试。']);
        }).then(function () {
            if (submitter) {
                submitter.disabled = false;
            }
        });
    });
})();
