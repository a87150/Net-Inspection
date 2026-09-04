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

function filterTargetDeviceChoices(choices, query, visibility, vendor = '', deviceType = '') {
    const normalizedQuery = (query || '').trim().toLocaleLowerCase();
    choices.forEach(choice => {
        const matchesQuery = !normalizedQuery || choice.label.toLocaleLowerCase().includes(normalizedQuery);
        const matchesVisibility = visibility !== 'selected' || choice.input.checked;
        const matchesVendor = !vendor || choice.vendor === vendor;
        const matchesType = !deviceType || choice.deviceType === deviceType;
        choice.hidden = !(matchesQuery && matchesVisibility && matchesVendor && matchesType);
    });
}

function updateTargetDeviceSelection(choices, action) {
    choices.filter(choice => !choice.hidden).forEach(choice => {
        choice.input.checked = action === 'invert' ? !choice.input.checked : action === 'all';
    });
}

function updateCheckboxSelection(inputs, action) {
    inputs.filter(input => !input.disabled).forEach(input => {
        input.checked = action === 'invert' ? !input.checked : action === 'all';
    });
}

function bindTargetDevicePicker(picker) {
    const search = picker.querySelector('[data-target-device-search]');
    const vendor = picker.querySelector('[data-target-device-vendor]');
    const deviceType = picker.querySelector('[data-target-device-type]');
    const visibility = picker.querySelector('[data-target-device-visibility]');
    const count = picker.querySelector('[data-target-device-count]');
    const mode = picker.closest('.inspection-config-section').querySelector('[data-target-device-mode]');
    const choices = Array.from(picker.querySelectorAll('[data-target-device-option]'), option => ({
        get hidden() { return option.hidden; },
        set hidden(value) { option.hidden = value; },
        label: option.dataset.targetDeviceLabel || option.textContent,
        vendor: option.dataset.targetDeviceVendor || '',
        deviceType: option.dataset.targetDeviceType || '',
        input: option.querySelector('input[type="checkbox"]'),
    }));
    const refresh = () => {
        filterTargetDeviceChoices(choices, search.value, visibility.value, vendor.value, deviceType.value);
        const selectedCount = choices.filter(choice => choice.input.checked).length;
        mode.value = selectedCount ? 'selected' : 'all';
        count.textContent = `已选 ${selectedCount} / ${choices.length} 台`;
    };
    search.addEventListener('input', refresh);
    vendor.addEventListener('change', refresh);
    deviceType.addEventListener('change', refresh);
    visibility.addEventListener('change', refresh);
    picker.querySelector('[data-target-device-select-all]').addEventListener('click', () => {
        updateTargetDeviceSelection(choices, 'all');
        refresh();
    });
    picker.querySelector('[data-target-device-invert]').addEventListener('click', () => {
        updateTargetDeviceSelection(choices, 'invert');
        refresh();
    });
    choices.forEach(choice => choice.input.addEventListener('change', refresh));
    refresh();
}

function bindBulkChoiceGroup(group) {
    const inputs = () => Array.from(group.querySelectorAll('[data-bulk-choice]'));
    group.querySelector('[data-bulk-select-all]').addEventListener('click', () => updateCheckboxSelection(inputs(), 'all'));
    group.querySelector('[data-bulk-invert]').addEventListener('click', () => updateCheckboxSelection(inputs(), 'invert'));
}

if (typeof module !== 'undefined') module.exports = {switchProfile, selectRow, filterTargetDeviceChoices, updateTargetDeviceSelection, updateCheckboxSelection};

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#task-profile, #config-profile').forEach(select => {
        select.addEventListener('change', () => switchProfile(select, window.location, document));
    });
    document.querySelectorAll('[data-task-target]').forEach(button => {
        button.addEventListener('click', () => selectRow(document, button.dataset.taskTarget));
    });
    document.querySelectorAll('[data-target-device-picker]').forEach(bindTargetDevicePicker);
    document.querySelectorAll('[data-bulk-choice-group]').forEach(bindBulkChoiceGroup);
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
