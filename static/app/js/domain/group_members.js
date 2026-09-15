(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module !== 'undefined') module.exports = controller;
    if (typeof window !== 'undefined') window.AppDomainGroupMembers = controller;

    if (typeof document !== 'undefined') {
        const initialize = () => controller.initializeMemberForms(document);
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initialize, {once: true});
        } else {
            initialize();
        }
        document.addEventListener('app:modal-updated', event => {
            controller.initializeMemberForms(event.detail?.modal || document);
        });
        document.addEventListener('click', event => {
            const button = event.target.closest('[data-member-select]');
            if (!button || !button.closest('#domainGroupMembersModal')) return;
            const form = button.closest('form');
            if (form) controller.selectMembers(form, button.dataset.memberSelect);
        });
        document.addEventListener('change', event => {
            const input = event.target;
            if (!input.matches('[data-member-action-form] input[type="checkbox"][name="target_ids"], [data-member-action-form] input[type="checkbox"][name="membership_ids"]')) return;
            controller.updateMemberSelectionState(input.form);
        });
    }
}(function () {
    'use strict';

    const memberSelector = [
        'input[type="checkbox"][name="target_ids"]',
        'input[type="checkbox"][name="membership_ids"]',
    ].join(', ');

    function memberInputs(form) {
        return Array.from(form?.querySelectorAll?.(memberSelector) || []);
    }

    function selectionState(form) {
        const available = memberInputs(form).filter(input => !input.disabled);
        return {
            selected: available.filter(input => input.checked).length,
            total: available.length,
        };
    }

    function updateMemberSelectionState(form) {
        if (!form) return {selected: 0, total: 0};
        const state = selectionState(form);
        const surface = form.closest?.('.modal-content') || form;
        const message = `已选 ${state.selected} / 可选 ${state.total}`;
        Array.from(surface.querySelectorAll?.('[data-member-selection-summary]') || [])
            .forEach(summary => { summary.textContent = message; });
        const submit = surface.querySelector?.('[data-member-submit]');
        if (submit) {
            const locked = submit.hasAttribute?.('data-member-locked') || false;
            submit.disabled = locked || state.selected === 0;
        }
        return state;
    }

    function selectMembers(form, action) {
        if (action === true) action = 'all';
        if (action === false) action = 'none';
        memberInputs(form).filter(input => !input.disabled).forEach(input => {
            if (action === 'all') input.checked = true;
            if (action === 'none') input.checked = false;
            if (action === 'invert') input.checked = !input.checked;
        });
        return updateMemberSelectionState(form);
    }

    function initializeMemberForms(root) {
        Array.from(root?.querySelectorAll?.('[data-member-action-form]') || [])
            .forEach(updateMemberSelectionState);
    }

    return {
        initializeMemberForms,
        selectMembers,
        selectionState,
        updateMemberSelectionState,
    };
}));
