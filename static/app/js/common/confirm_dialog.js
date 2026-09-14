(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module === 'object' && module.exports) module.exports = controller;
    if (typeof document !== 'undefined') {
        document.addEventListener('DOMContentLoaded', () => {
            controller.installConfirmDialog(document, window.bootstrap);
        });
    }
}(function () {
    'use strict';

    function installConfirmDialog(documentRoot, bootstrapApi) {
        const modal = documentRoot.getElementById('appConfirmModal');
        const message = modal?.querySelector('[data-confirm-text]');
        const confirmButton = modal?.querySelector('[data-confirm-submit]');
        if (!modal || !message || !confirmButton || !bootstrapApi?.Modal) return null;

        const modalController = bootstrapApi.Modal.getOrCreateInstance(modal);
        const bypass = new WeakSet();
        let pending = null;

        documentRoot.addEventListener('submit', (event) => {
            const form = event.target;
            if (!form?.dataset?.confirmMessage || bypass.has(form)) {
                if (form) bypass.delete(form);
                return;
            }
            event.preventDefault();
            pending = {form, submitter: event.submitter};
            message.textContent = form.dataset.confirmMessage;
            modalController.show();
        });

        confirmButton.addEventListener('click', () => {
            if (!pending) return;
            const {form, submitter} = pending;
            pending = null;
            bypass.add(form);
            modalController.hide();
            form.requestSubmit(submitter || undefined);
        });
        modal.addEventListener?.('hidden.bs.modal', () => { pending = null; });
        return modalController;
    }

    return {installConfirmDialog};
}));
