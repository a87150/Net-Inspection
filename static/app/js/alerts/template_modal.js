(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module === 'object' && module.exports) module.exports = controller;
    if (typeof document !== 'undefined') {
        controller.installTemplateModalLoader(document, window.AppModalTransport);
    }
}(function () {
    'use strict';

    function installTemplateModalLoader(documentRoot, modalTransport) {
        documentRoot.addEventListener('show.bs.modal', event => {
            const modal = event.target;
            if (modal.id !== 'alertTemplateModal') return;
            modalTransport?.navigate(modal, modal.dataset.templateSettingsUrl);
        });
    }

    return {installTemplateModalLoader};
}));
