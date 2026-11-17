(function (factory) {
    'use strict';

    const controller = factory();
    if (typeof module !== 'undefined') module.exports = controller;
    if (typeof window !== 'undefined') window.AppMaintenanceWindow = controller;

    if (typeof document !== 'undefined') {
        const refresh = () => controller.refreshTrigger(document);
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', refresh, {once: true});
        } else {
            refresh();
        }
        document.addEventListener('change', (event) => {
            if (event.target?.matches?.('[data-device-selection-target]')) refresh();
        });
        document.addEventListener('click', (event) => {
            if (!event.target.closest?.('[data-device-selection-all], [data-device-select-invert], [data-device-select-all]')) return;
            controller.refreshTrigger(document);
        });
        document.addEventListener('click', (event) => {
            const trigger = event.target.closest?.('[data-maintenance-open]');
            if (!trigger) return;
            controller.prepare(document, window.bootstrap);
        });
    }
}(function () {
    'use strict';

    function selectedIds(root) {
        return Array.from(root?.querySelectorAll?.('[data-device-selection-target]:checked') || [])
            .map((input) => input.value);
    }

    function fillTargets(container, ids) {
        if (!container) return 0;
        container.replaceChildren?.();
        ids.forEach((id) => {
            const input = container.ownerDocument.createElement('input');
            input.type = 'hidden';
            input.name = 'target_ids';
            input.value = id;
            container.append(input);
        });
        return ids.length;
    }

    function refreshTrigger(root) {
        const trigger = root?.querySelector?.('[data-maintenance-open]');
        if (!trigger) return 0;
        const count = selectedIds(root).length;
        trigger.disabled = count === 0;
        trigger.textContent = count ? '加入维护窗口（' + count + '）' : '加入维护窗口';
        return count;
    }

    function prepare(root, bootstrapApi) {
        const modal = root?.querySelector?.('#maintenanceWindowModal');
        if (!modal) return 0;
        const count = fillTargets(modal.querySelector('[data-maintenance-targets]'), selectedIds(root));
        const badge = modal.querySelector('[data-maintenance-count]');
        if (badge) badge.textContent = String(count);
        const submit = modal.querySelector('[data-maintenance-submit]');
        if (submit) submit.disabled = count === 0;
        bootstrapApi?.Modal?.getOrCreateInstance(modal)?.show();
        return count;
    }

    return {fillTargets, prepare, refreshTrigger, selectedIds};
}));
