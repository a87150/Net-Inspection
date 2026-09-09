(function () {
    'use strict';
    function updateStatistics(doc, modal) {
        if (modal?.id !== 'domainStatisticsModal') return;
        const values = modal.querySelector('[data-domain-statistics-values]')?.dataset;
        if (!values) return;
        doc.querySelectorAll('[data-domain-inactive-days]').forEach(item => { item.textContent = values.days; });
        ['account', 'computer'].forEach(type => {
            const item = doc.querySelector('[data-domain-stale="' + type + '"]');
            if (!item) return;
            item.textContent = values[type];
            item.classList.toggle('text-danger', Number(values[type]) > 0);
            item.classList.toggle('text-secondary', Number(values[type]) === 0);
        });
    }
    if (typeof module !== 'undefined' && module.exports) module.exports = {updateStatistics};
    if (typeof document !== 'undefined') {
        document.addEventListener('app:modal-updated', event => updateStatistics(document, event.detail?.modal));
        document.addEventListener('DOMContentLoaded', () => {
            const modal = document.getElementById('domainStatisticsModal');
            if (modal?.dataset.autoOpen === 'true') window.bootstrap?.Modal.getOrCreateInstance(modal).show();
        });
    }
})();
