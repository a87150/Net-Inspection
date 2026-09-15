function refreshDomainOperationSummary(doc, actionSelect, form) {
    const action = actionSelect.value;
    const option = actionSelect.options[actionSelect.selectedIndex];
    form.querySelector('[data-domain-operation-action-value]').value = action;
    form.querySelector('[data-domain-confirm-action]').textContent = option ? option.textContent : action;
    form.querySelectorAll('[data-domain-parameter-for]').forEach((field) => {
        field.hidden = !field.dataset.domainParameterFor.split(' ').includes(action);
        field.querySelectorAll?.('input, select').forEach((input) => { input.disabled = field.hidden; });
    });
    const selected = doc.querySelectorAll('[data-domain-target-checkbox]:checked');
    form.querySelector('[data-domain-confirm-target-count]').textContent = String(
        action === 'create_user' ? 1 : selected.length,
    );

    const scope = {
        move_ou: ['destination_dn', '目标 OU'],
        add_group: ['group_dn', '安全组'],
    }[action];
    const scopeRow = form.querySelector('[data-domain-confirm-scope-row]');
    scopeRow.hidden = !scope;
    if (scope) {
        const input = Array.from(form.querySelectorAll('[data-domain-summary-input]')).find(
            (item) => item.dataset.domainSummaryInput === scope[0],
        );
        form.querySelector('[data-domain-confirm-scope-label]').textContent = scope[1];
        form.querySelector('[data-domain-confirm-scope]').textContent = input?.value || '—';
    }
}

const boundDomainForms = new WeakSet();
function bindDomainOperationModal(doc) {
    const actionSelect = doc.querySelector('[data-domain-operation-action]');
    const form = doc.querySelector('[data-domain-operation-form]');
    if (!actionSelect || !form) return;
    if (boundDomainForms.has(form)) return;
    boundDomainForms.add(form);
    const refresh = () => refreshDomainOperationSummary(doc, actionSelect, form);

    actionSelect.addEventListener('change', refresh);
    form.querySelectorAll('[data-domain-summary-input]').forEach((input) => {
        input.addEventListener('input', refresh);
        input.addEventListener('change', refresh);
    });
    doc.querySelectorAll('[data-domain-target-checkbox]').forEach((checkbox) => {
        checkbox.addEventListener('change', refresh);
    });
    form.addEventListener('submit', () => {
        const holder = form.querySelector('[data-domain-selected-targets]');
        holder.replaceChildren();
        doc.querySelectorAll('[data-domain-target-checkbox]:checked').forEach((checkbox) => {
            const input = doc.createElement('input');
            input.type = 'hidden';
            input.name = 'target_ids';
            input.value = checkbox.value;
            holder.appendChild(input);
        });
        refresh();
    });
    refresh();
}

if (typeof module !== 'undefined') {
    module.exports = {bindDomainOperationModal, refreshDomainOperationSummary};
}
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => bindDomainOperationModal(document));
    document.addEventListener('app:modal-updated', () => bindDomainOperationModal(document));
}
