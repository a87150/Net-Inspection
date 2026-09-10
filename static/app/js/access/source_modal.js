/* Refresh platform choices only after the user closes a saved editor. */
(function () {
    'use strict';
    let saved = false;
    document.addEventListener('app:modal-updated', function (event) {
        const modal = event.detail?.modal;
        if (modal?.id === 'accessSourceModal' && modal.querySelector('[data-source-saved]')) saved = true;
    });
    document.addEventListener('hidden.bs.modal', function (event) {
        if (event.target.id === 'accessSourceModal' && saved) window.location.reload();
    });
}());
