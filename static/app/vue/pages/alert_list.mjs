/* 告警记录：表格与分页走 REST，筛选/排序仍是 URL 参数，首屏由服务端注水。 */
import { api } from '../api.mjs';

function badgeClass(status) {
    const tones = {
        success: ['success', 'normal', 'recovery', 'sent', 'delivered', 'enabled', 'completed'],
        danger: ['failed', 'abnormal', 'error', 'critical'],
        warning: ['partial', 'warning', 'retry', 'blocked'],
        secondary: ['cancelled', 'disabled', 'skipped', 'unknown'],
    };
    for (const [tone, members] of Object.entries(tones)) {
        if (members.includes(status)) return 'text-bg-' + tone;
    }
    return 'text-bg-info';
}

export default {
    delimiters: ['[[', ']]'],
    props: {
        endpoint: {type: String, default: '/api/v1/alert-events/'},
        rows: {type: Array, default: () => []},
        count: {type: Number, default: 0},
        page: {type: Number, default: 1},
        pages: {type: Number, default: 1},
        page_size: {type: Number, default: 20},
        start_index: {type: Number, default: 0},
        end_index: {type: Number, default: 0},
    },
    data() {
        return {
            items: this.rows,
            total: this.count,
            currentPage: this.page,
            totalPages: this.pages,
            from: this.start_index,
            to: this.end_index,
            loading: false,
            error: '',
        };
    },
    methods: {
        badgeClass,
        async load(page) {
            if (this.loading || page < 1 || page > this.totalPages) return;
            this.loading = true;
            this.error = '';
            try {
                const params = Object.fromEntries(new URLSearchParams(window.location.search));
                params.page = page;
                const payload = await api.get(this.endpoint, params);
                this.items = payload.results;
                this.total = payload.count;
                this.currentPage = payload.page;
                this.totalPages = payload.pages;
                this.from = payload.start_index;
                this.to = payload.end_index;
                const url = new URL(window.location.href);
                url.searchParams.set('page', payload.page);
                window.history.replaceState({}, '', url);
            } catch (failure) {
                this.error = failure.message || '加载失败';
            } finally {
                this.loading = false;
            }
        },
    },
    template: `
    <div class="alert-table-region">
        <p v-if="error" class="alert alert-danger py-2" role="alert">[[ error ]]</p>
        <div class="card surface-card table-scroll-shell">
            <p class="table-scroll-hint">可横向滚动查看渠道送达与事件信息</p>
            <div class="table-responsive" role="region" aria-label="告警记录数据表" tabindex="0" :aria-busy="loading ? 'true' : 'false'">
                <table class="table data-table table-hover align-middle mb-0">
                    <caption class="visually-hidden">告警事件类型、来源、目标、级别、状态、渠道结果与发生时间</caption>
                    <thead>
                        <tr>
                            <th scope="col">事件类型</th><th scope="col">来源</th><th scope="col">目标</th>
                            <th scope="col">严重级别</th><th scope="col">事件状态</th><th scope="col">渠道结果</th>
                            <th scope="col">发生时间</th>
                            <th scope="col" class="table-actions-column table-actions-cell">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="row in items" :key="row.id">
                            <td><span class="badge status-badge" :class="badgeClass(row.event_type.status)" :data-status="row.event_type.status">[[ row.event_type.label ]]</span></td>
                            <td>[[ row.name ]]</td>
                            <td class="text-break">[[ row.target ]]</td>
                            <td><span class="badge status-badge" :class="row.severity.class" :data-status="row.severity.status">[[ row.severity.label ]]</span></td>
                            <td><span class="badge status-badge" :class="badgeClass(row.status.status)" :data-status="row.status.status">[[ row.status.label ]]</span></td>
                            <td class="text-break">
                                <div class="alert-delivery-summary" aria-label="渠道送达结果">
                                    <template v-if="row.deliveries.length">
                                        <span v-for="delivery in row.deliveries" :key="delivery.label" class="badge status-badge" :class="badgeClass(delivery.status)" :data-status="delivery.status">[[ delivery.label ]]</span>
                                    </template>
                                    <span v-else class="text-secondary">暂无渠道结果</span>
                                </div>
                            </td>
                            <td>[[ row.occurred_at_display ]]</td>
                            <td class="text-end table-actions-cell"><a class="btn btn-outline-dark btn-sm" :href="row.detail_url">查看详情</a></td>
                        </tr>
                        <tr v-if="!items.length">
                            <td colspan="8">
                                <div class="empty-state"><strong>暂无告警记录</strong><span>异常或恢复事件产生后将显示在这里。</span></div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
        <nav v-if="totalPages > 1" class="page-pagination" aria-label="告警记录分页">
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage <= 1 || loading" @click="load(currentPage - 1)">上一页</button>
            <span class="text-secondary">第 [[ currentPage ]] / [[ totalPages ]] 页，共 [[ total ]] 条（[[ from ]]–[[ to ]]）</span>
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage >= totalPages || loading" @click="load(currentPage + 1)">下一页</button>
        </nav>
    </div>`,
};
