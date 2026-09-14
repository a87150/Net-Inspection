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

    function updateSnmpFields(select) {
        const form=select.closest('form');
        if (!form)return;
        const version=form.querySelector('[data-snmp-version]')?.value;
        const security=form.querySelector('[data-snmp-security]')?.value;
        form.querySelectorAll('[data-snmp-group]').forEach(group=>{
            const kind=group.dataset.snmpGroup;
            setGroupEnabled(group, kind===version || (version==='v3' && ((kind==='auth' && security!=='noAuthNoPriv') || (kind==='priv' && security==='authPriv'))));
        });
    }

    function updateNetworkFields(select) {
        const form = select.closest('form');
        if (!form) return;
        const api = select.value === 'sangfor_api';
        form.querySelectorAll('[data-network-fields]').forEach(group => {
            setGroupEnabled(group, group.dataset.networkFields === (api ? 'sangfor_api' : 'standard'));
        });
        if (!api) updateSnmpFields(select);
    }

    function chooseSangforApi(form) {
        const connection = form.querySelector('[data-network-connection]');
        const vendor = form.querySelector('[data-network-vendor]');
        const deviceType = form.querySelector('[data-network-device-type]');
        if (!connection || connection.dataset.networkNew !== 'true' || !vendor || !deviceType) return;
        if (vendor.value === 'sangfor' && deviceType.value === 'ac_gateway' && connection.value === 'auto') {
            connection.value = 'sangfor_api';
            connection.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }

    function bindScheduleFields(root) {
        root.querySelectorAll('[data-snmp-version], [data-snmp-security]').forEach(select=>{
            if (select.dataset.snmpBound!==undefined)return;
            select.dataset.snmpBound='';
            select.addEventListener('change',()=>updateSnmpFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored',()=>updateSnmpFields(select));
            updateSnmpFields(select);
        });
        root.querySelectorAll('[data-server-type]').forEach(select => {
            if (select.dataset.serverBound !== undefined) return;
            select.dataset.serverBound = '';
            select.addEventListener('change', () => updateServerFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored', () => updateServerFields(select));
            updateServerFields(select);
        });
        root.querySelectorAll('[data-network-connection]').forEach(select => {
            if (select.dataset.networkBound !== undefined) return;
            select.dataset.networkBound = '';
            select.addEventListener('change', () => updateNetworkFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored', () => updateNetworkFields(select));
            updateNetworkFields(select);
            const form = select.closest('form');
            form?.querySelectorAll('[data-network-vendor], [data-network-device-type]').forEach(field => {
                field.addEventListener('change', () => chooseSangforApi(form));
            });
            chooseSangforApi(form);
        });
        root.querySelectorAll('[data-schedule-kind]').forEach(select => {
            if (select.dataset.scheduleBound !== undefined) return;
            select.dataset.scheduleBound = '';
            select.addEventListener('change', () => updateScheduleFields(select));
            select.closest('form')?.addEventListener('modal-draft-restored', () => updateScheduleFields(select));
            updateScheduleFields(select);
        });
    }

    return {updateSnmpFields, updateServerFields, updateNetworkFields, chooseSangforApi, bindScheduleFields, setGroupEnabled, updateScheduleFields};
}));
