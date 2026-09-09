(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module === 'object' && module.exports) module.exports = controller;
    if (typeof window !== 'undefined') window.AppConditionalFields = controller;
    if (typeof document !== 'undefined') {
        document.addEventListener('DOMContentLoaded', () => controller.bindScheduleFields(document));
    }
}(function () {
    'use strict';

    function setGroupEnabled(group, enabled) {
        group.hidden = !enabled;
        Array.from(group.querySelectorAll('input, select, textarea, button')).forEach(control => {
            control.disabled = !enabled;
        });
    }

    function updateScheduleFields(select) {
        const form = select.closest('form');
        if (!form) return;
        const daily = select.value === 'daily';
        form.querySelectorAll('[data-schedule-interval]').forEach(group => setGroupEnabled(group, !daily));
        form.querySelectorAll('[data-schedule-daily]').forEach(group => setGroupEnabled(group, daily));
    }

    function updateServerFields(select) {
        select.closest('form')?.querySelectorAll('[data-server-fields]').forEach(group => {
            setGroupEnabled(group, group.dataset.serverFields === select.value);
        });
    }

    function bindScheduleFields(root) {
        root.querySelectorAll('[data-server-type]').forEach(select => {
            if (select.dataset.serverBound !== undefined) return;
            select.dataset.serverBound = '';
            select.addEventListener('change', () => updateServerFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored', () => updateServerFields(select));
            updateServerFields(select);
        });
        root.querySelectorAll('[data-schedule-kind]').forEach(select => {
            if (select.dataset.scheduleBound !== undefined) return;
            select.dataset.scheduleBound = '';
            select.addEventListener('change', () => updateScheduleFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored', () => updateScheduleFields(select));
            updateScheduleFields(select);
        });
    }

    return {updateServerFields, bindScheduleFields, setGroupEnabled, updateScheduleFields};
}));
