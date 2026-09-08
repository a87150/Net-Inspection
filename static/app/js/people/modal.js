/* Keep the multi-step personnel workflow inside the open dialog. */
async function submitPeopleModal(form, {body, fetchImpl = fetch, render}) {
    const response = await fetchImpl(form.action, {
        method: 'POST', body, credentials: 'same-origin',
    });
    if (!response.ok && response.status !== 400) {
        throw new Error(`操作未完成（HTTP ${response.status}），请检查服务后重试。`);
    }
    render(await response.text());
}

function installPeopleModal(document, window) {
    let activeModal = null;
    function render(html, modal) {
        const page = new window.DOMParser().parseFromString(html, 'text/html');
        const incoming = page.querySelector('#peoplePreviewModal') || page.querySelector('#importModal');
        if (!incoming) throw new Error('未收到有效结果，请重新登录或检查服务。');
        const content = incoming.querySelector('.modal-content').cloneNode(true);
        // Direct result URLs also contain a hidden import dialog. Avoid
        // duplicate tab IDs when returning to settings inside the visible one.
        if (incoming.id === 'importModal' && modal.id !== 'importModal') {
            const hiddenImport = document.getElementById('importModal');
            if (hiddenImport) {
                window.bootstrap?.Modal.getInstance(hiddenImport)?.dispose();
                hiddenImport.remove();
            }
            modal.id = 'importModal';
        }
        // Messages normally live outside the modal; carry them into the result.
        page.querySelectorAll('[data-flash-message]').forEach(message => {
            content.querySelector('.modal-body').prepend(message.cloneNode(true));
        });
        modal.querySelector('.modal-content').replaceWith(content);
        modal.setAttribute('aria-labelledby', incoming.getAttribute('aria-labelledby'));
        window.AppConditionalFields?.bindScheduleFields(modal);
        activeModal = modal;
        document.dispatchEvent(new window.Event('people:operation-submitted'));
    }
    function showError(modal, error) {
        const alert = document.createElement('div');
        alert.className = 'alert alert-danger';
        alert.setAttribute('role', 'alert');
        alert.textContent = error.message || '操作失败，请重试。';
        modal.querySelector('.modal-body').prepend(alert);
    }
    document.addEventListener('submit', async event => {
        const form = event.target;
        const modal = form.closest('.modal');
        if (!modal || !new URL(form.action, window.location.href).pathname.startsWith('/integrations/people/')) return;
        event.preventDefault();
        if (form.dataset.submitting) return;
        const body = new window.FormData(form);
        form.dataset.submitting = 'true';
        const buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        buttons.forEach(button => { button.disabled = true; });
        try {
            await submitPeopleModal(form, {body, render: html => render(html, modal)});
        } catch (error) {
            showError(modal, error);
        } finally {
            delete form.dataset.submitting;
            buttons.forEach(button => { button.disabled = false; });
        }
    });
    document.addEventListener('click', async event => {
        const link = event.target.closest('a');
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const path = new URL(link.href, window.location.href).pathname;
        const modal = link.closest('#importModal, #peoplePreviewModal') || (
            link.matches('[data-people-completion-jump]') && activeModal?.classList.contains('show') ? activeModal : null
        );
        if (!modal || !(path.startsWith('/integrations/people/operations/') ||
            (path === '/assets/people/' && new URL(link.href).searchParams.has('import')))) return;
        event.preventDefault();
        try {
            const response = await fetch(link.href, {credentials: 'same-origin'});
            if (!response.ok) throw new Error(`读取结果失败（HTTP ${response.status}）。`);
            render(await response.text(), modal);
            const completion = document.querySelector('#peopleTaskCompletionModal');
            if (completion?.classList.contains('show')) window.bootstrap.Modal.getInstance(completion)?.hide();
        } catch (error) { showError(modal, error); }
    });
}

if (typeof module !== 'undefined') module.exports = {submitPeopleModal, installPeopleModal};
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => installPeopleModal(document, window));
}
