/* Page mounter: one registry entry per migrated page, no build step. */
import { createApp } from '../../vendor/vue/vue.esm-browser.prod.js';

const pages = {
    'maintenance-list': () => import('./pages/maintenance_list.mjs'),
    'ip-subnet-form': () => import('./pages/ip_subnet_form.mjs'),
    'alert-list': () => import('./pages/alert_list.mjs'),
    'alert-detail': () => import('./pages/resource_detail.mjs'),
    'asset-detail': () => import('./pages/resource_detail.mjs'),
    'record-detail': () => import('./pages/resource_detail.mjs'),
    'inspection-records': () => import('./pages/resource_table.mjs'),
    'error-records': () => import('./pages/resource_table.mjs'),
    'access-records': () => import('./pages/resource_table.mjs'),
    'pc-logs': () => import('./pages/resource_table.mjs'),
    'pc-errors': () => import('./pages/resource_table.mjs'),
    'task-runs': () => import('./pages/resource_table.mjs'),
    'domain-objects': () => import('./pages/resource_table.mjs'),
};

export function readBootstrap(id) {
    const node = document.getElementById(id);
    if (!node) return {};
    try {
        return JSON.parse(node.textContent);
    } catch (error) {
        console.error('bootstrap 解析失败：', id, error);
        return {};
    }
}

export async function mountPages(root = document) {
    const hosts = Array.from(root.querySelectorAll('[data-vue-page]'));
    for (const host of hosts) {
        if (host.__vue_app__) continue;
        const loader = pages[host.dataset.vuePage];
        if (!loader) {
            console.warn('未注册的 Vue 页面：', host.dataset.vuePage);
            continue;
        }
        const module = await loader();
        const props = readBootstrap(host.dataset.vueBootstrap || '');
        const app = createApp(module.default, props);
        app.config.errorHandler = (error) => console.error(error);
        app.mount(host);
    }
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => mountPages(), {once: true});
    } else {
        mountPages();
    }
}