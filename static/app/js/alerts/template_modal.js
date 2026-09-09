/* Fetch current settings; shared modal transport owns submit and feedback. */
(function () {
    'use strict';
    document.addEventListener('show.bs.modal', function (event) {
        const modal = event.target;
        if (modal.id !== 'alertTemplateModal') return;
        window.AppModalTransport?.navigate(modal, modal.dataset.templateSettingsUrl);
    });
}());
