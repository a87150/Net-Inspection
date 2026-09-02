function switchProfile(select, location, doc) {
    const url = new URL(location.href);
    url.searchParams.set('task_profile', select.value);
    url.searchParams.set('task_modal', select.closest('.modal').id === 'runTaskModal' ? 'run' : 'profile');
    url.searchParams.set('task_mode', doc.querySelector('[name="target_mode"]:checked')?.value || 'all');
    url.searchParams.set('task_targets', Array.from(doc.querySelectorAll('[name="target_ids"]:checked'), box => box.value).join(','));
    // Never let a changed profile ID submit the old profile's rendered values.
    select.closest('form').querySelectorAll('[type="submit"]').forEach(button => { button.disabled = true; });
    location.assign(url.toString());
}

function selectRow(doc, id) {
    doc.querySelectorAll('[name="target_ids"]').forEach(box => { box.checked = box.value === id; });
    doc.querySelectorAll('[name="target_mode"]').forEach(mode => { mode.checked = mode.value === 'selected'; });
}

if (typeof module !== 'undefined') module.exports = {switchProfile, selectRow};

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#task-profile, #config-profile').forEach(select => {
        select.addEventListener('change', () => switchProfile(select, window.location, document));
    });
    document.querySelectorAll('[data-task-target]').forEach(button => {
        button.addEventListener('click', () => selectRow(document, button.dataset.taskTarget));
    });
    document.querySelectorAll('.modal[data-auto-open="true"]').forEach((modal) => {
        if (window.bootstrap?.Modal) window.bootstrap.Modal.getOrCreateInstance(modal).show();
    });

    const updateScheduleFields = (select) => {
        const form = select.closest('form');
        const daily = select.value === 'daily';
        form.querySelectorAll('[data-schedule-interval]').forEach((field) => { field.hidden = daily; });
        form.querySelectorAll('[data-schedule-daily]').forEach((field) => { field.hidden = !daily; });
    };
    document.querySelectorAll('[data-schedule-kind]').forEach((select) => {
        updateScheduleFields(select);
        select.addEventListener('change', () => updateScheduleFields(select));
    });

    const updateFileTimeFields = (select) => {
        const form = select.closest('form');
        const dateRange = select.value === 'date_range';
        form.querySelectorAll('[data-recent-days]').forEach((field) => { field.hidden = dateRange; });
        form.querySelectorAll('[data-date-range]').forEach((field) => { field.hidden = !dateRange; });
    };
    document.querySelectorAll('[data-file-time-mode]').forEach((select) => {
        updateFileTimeFields(select);
        select.addEventListener('change', () => updateFileTimeFields(select));
    });
});
