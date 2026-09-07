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

    function bindScheduleFields(root) {
        root.querySelectorAll('[data-schedule-kind]').forEach(select => {
            if (select.dataset.scheduleBound !== undefined) return;
            select.dataset.scheduleBound = '';
            select.addEventListener('change', () => updateScheduleFields(select));
            updateScheduleFields(select);
        });
    }

    return {bindScheduleFields, setGroupEnabled, updateScheduleFields};
}));
