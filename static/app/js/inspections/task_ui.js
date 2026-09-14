function switchProfile(select, location, doc, transport = typeof window !== 'undefined' ? window.AppModalTransport : null) {
    const url = new URL(location.href);
    const single = select.closest('.modal').querySelector?.('[name="single_target_id"]')?.value;
    if (single) url.searchParams.set('task_single_target', single);
    else url.searchParams.delete('task_single_target');
    url.searchParams.set('task_profile', select.value);
    url.searchParams.set('task_modal', select.closest('.modal').id === 'runTaskModal' ? 'run' : 'profile');
    url.searchParams.set('task_mode', doc.querySelector('[name="target_mode"]:checked')?.value || 'all');
    url.searchParams.set('task_targets', Array.from(doc.querySelectorAll('[name="target_ids"]:checked'), box => box.value).join(','));
    return transport?.navigate(select.closest('.modal'), url.toString());
}

function selectRow(doc, id) {
    doc.querySelectorAll('[name="target_ids"]').forEach(box => { box.checked = box.value === id; box.dispatchEvent?.(new Event('change', {bubbles: true})); });
    doc.querySelectorAll('[name="target_mode"]').forEach(mode => { if (mode.type === 'hidden') mode.value = 'selected'; else mode.checked = mode.value === 'selected'; });
    const singleTarget = doc.querySelector('[name="single_target_id"]');
    if (singleTarget) singleTarget.value = id;
}

function clearRowTarget(doc) {
    doc.querySelectorAll('[name="target_ids"]').forEach(box => { box.checked = false; box.dispatchEvent?.(new Event('change', {bubbles: true})); });
    doc.querySelectorAll('[name="target_mode"]').forEach(mode => {
        if (mode.type === 'hidden') mode.value = '';
        else mode.checked = false;
    });
    const singleTarget = doc.querySelector('[name="single_target_id"]');
    if (singleTarget) singleTarget.value = '';
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

function applicableTargetItems(choices) {
    const selected = choices.filter(choice => choice.input.checked);
    return new Set((selected.length ? selected : choices).flatMap(choice => choice.items || []));
}

function bindTargetDevicePicker(picker) {
    const search = picker.querySelector('[data-target-device-search]');
    const vendor = picker.querySelector('[data-target-device-vendor]');
    const deviceType = picker.querySelector('[data-target-device-type]');
    const visibility = picker.querySelector('[data-target-device-visibility]');
    const count = picker.querySelector('[data-target-device-count]');
    const mode = picker.closest('.inspection-config-block').querySelector('[data-target-device-mode]');
    const choices = Array.from(picker.querySelectorAll('[data-target-device-option]'), option => ({
        get hidden() { return option.hidden; },
        set hidden(value) { option.hidden = value; },
        label: option.dataset.targetDeviceLabel || option.textContent,
        vendor: option.dataset.targetDeviceVendor || '',
        deviceType: option.dataset.targetDeviceType || '',
        items: (option.dataset.targetDeviceItems || '').split(',').filter(Boolean),
        input: option.querySelector('input[type="checkbox"]'),
    }));
    const refresh = () => {
        filterTargetDeviceChoices(choices, search.value, visibility.value, vendor?.value || '', deviceType.value);
        const selectedCount = choices.filter(choice => choice.input.checked).length;
        mode.value = selectedCount ? 'selected' : 'all';
        count.textContent = `已选 ${selectedCount} / ${choices.length} 台`;
        const allowed = applicableTargetItems(choices);
        if (choices.length) picker.closest('form').querySelectorAll('[name="selected_items"]').forEach(input => {
            input.disabled = !allowed.has(input.value);
            input.closest('label').hidden = input.disabled;
        });
    };
    search.addEventListener('input', refresh);
    vendor?.addEventListener('change', refresh);
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

if (typeof module !== 'undefined') module.exports = {applicableTargetItems, switchProfile, selectRow, clearRowTarget, filterTargetDeviceChoices, updateTargetDeviceSelection, updateCheckboxSelection};

const boundTaskControls = new WeakSet();
function bindInspectionRuleFilter(container) {
    const search = container.querySelector('[data-rule-search]');
    const scope = container.querySelector('[data-rule-scope]');
    const count = container.querySelector('[data-rule-count]');
    const rules = Array.from(container.querySelectorAll('[data-inspection-rule]'));
    const update = () => {
        const keyword = (search?.value || '').trim().toLocaleLowerCase();
        let visible = 0;
        rules.forEach(rule => {
            const mode = rule.querySelector('[data-rule-mode] select')?.value || 'inherit';
            const matches = (rule.dataset.ruleLabel || '').toLocaleLowerCase().includes(keyword)
                && (!scope?.value || scope.value === 'all' || mode === scope.value);
            rule.hidden = !matches;
            if (matches) visible += 1;
        });
        if (count) count.textContent = `显示 ${visible} / ${rules.length} 项；筛选只影响显示，保存仍包含全部项目。`;
    };
    container.addEventListener('invalid', event => {
        const rule = event.target.closest('[data-inspection-rule]');
        if (!rule) return;
        if (search) search.value = '';
        if (scope) scope.value = 'all';
        update();
        rule.open = true;
    }, true);
    container.addEventListener('input', update);
    container.addEventListener('change', update);
    container.closest('form')?.addEventListener('modal-draft-restored', update);
    update();
}

function bindTaskUI(root) {
    const once = (selector, bind) => root.querySelectorAll(selector).forEach(element => {
        if (boundTaskControls.has(element)) return;
        boundTaskControls.add(element);
        bind(element);
    });
    once('#task-profile, #config-profile', select => {
        select.addEventListener('change', () => switchProfile(select, window.location, document));
    });
    once('[data-task-target]', button => {
        button.addEventListener('click', () => selectRow(document, button.dataset.taskTarget));
    });    once('[data-bs-target="#runTaskModal"]', button => {
        if (!button.dataset.taskTarget) button.addEventListener('click', () => {
            clearRowTarget(document);
            const url = new URL(window.location.href);
            url.searchParams.delete('task_single_target');
            url.searchParams.set('task_modal', 'run');
            window.AppModalTransport?.navigate(document.getElementById('runTaskModal'), url.toString());
        });
    });
    once('[data-target-device-picker]', bindTargetDevicePicker);
    once('[data-rule-filter]', bindInspectionRuleFilter);
    once('[data-inspection-rule]', (rule) => {
        const method=rule.querySelector('[data-rule-method] select');
        const mode=rule.querySelector('[data-rule-mode] select');
        const update=()=>{
            const editor=rule.querySelector('[data-rule-editor]');
            if (editor) {
                editor.hidden=mode?.value==='inherit';
                editor.querySelectorAll('input, select, textarea, button').forEach(control=>{control.disabled=editor.hidden;});
            }
            rule.querySelectorAll('[data-rule-protocol]').forEach(section=>{
                section.hidden=!method || method.value!==section.dataset.ruleProtocol;
            });
        };
        method?.addEventListener('change',update);
        mode?.addEventListener('change',update);
        rule.closest('form')?.addEventListener('modal-draft-restored',update);
        update();
    });
    once('[data-template-parent] select', (select) => {
        select.addEventListener('change',()=>{
            const form=select.closest('form');
            const preview=form?.querySelector('button[name="preview_inheritance"]');
            if (preview)form.requestSubmit(preview);
        });
    });
    once('[data-bulk-choice-group]', bindBulkChoiceGroup);

    const updateFileTimeFields = (select) => {
        const form = select.closest('form');
        const dateRange = select.value === 'date_range';
        form.querySelectorAll('[data-recent-days]').forEach((field) => { field.hidden = dateRange; });
        form.querySelectorAll('[data-date-range]').forEach((field) => { field.hidden = !dateRange; });
    };
    once('[data-file-time-mode]', (select) => {
        updateFileTimeFields(select);
        select.addEventListener('change', () => updateFileTimeFields(select));
    });
}
if (typeof window !== 'undefined') window.AppTaskUI = {bind: bindTaskUI};
document.addEventListener('DOMContentLoaded', () => {
    bindTaskUI(document);
    document.querySelectorAll('.modal[data-auto-open="true"]').forEach((modal) => {
        if (window.bootstrap?.Modal) window.bootstrap.Modal.getOrCreateInstance(modal).show();
    });
});
