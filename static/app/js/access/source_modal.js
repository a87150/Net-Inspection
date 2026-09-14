(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module === 'object' && module.exports) module.exports = controller;
    if (typeof document !== 'undefined') controller.installSourceModalReload(document, window);
}(function () {
    'use strict';

    function installSourceModalReload(documentRoot, browserWindow) {
        let saved = false;
        documentRoot.addEventListener('app:modal-updated', event => {
            const modal = event.detail?.modal;
            if (modal?.id === 'accessSourceModal' && modal.querySelector('[data-source-saved]')) {
                saved = true;
            }
        });
        documentRoot.addEventListener('hidden.bs.modal', event => {
            if (event.target.id === 'accessSourceModal' && saved) browserWindow.location.reload();
        });
    }

    return {installSourceModalReload};
}));
