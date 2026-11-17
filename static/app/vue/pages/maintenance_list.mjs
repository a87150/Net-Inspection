/* 维护记录页：状态筛选、翻页、删除全部走 REST，首屏由服务端注水。 */
import { api, ApiError } from '../api.mjs';

const COLUMNS = ['资产', '类型', '开始', '结束', '状态', '备注', '操作'];

export default {
    delimiters: ['[[', ']]'],
    props: {
        state: {type: String, default: ''},
        states: {type: Array, default: () => []},
        endpoint: {type: String, default: '/api/v1/maintenance-windows/'},
        count: {type: Number, default: 0},
        page: {type: Number, default: 1},
        pages: {type: Number, default: 1},
        page_size: {type: Number, default: 50},
        rows: {type: Array, default: () => []},
        metrics: {type: Object, default: () => ({})},
        can_administer: {type: Boolean, default: false},
    },
    data() {
        return {
            columns: COLUMNS,
            currentState: this.state,
            currentPage: this.page,
            items: this.rows,
            total: this.count,
            totalPages: this.pages,
            cards: this.metricCards(this.metrics),
            loading: false,
            error: '',
            deleting: '',
        };
    },
    computed: {
        canPrevious() {
            return this.currentPage > 1 && !this.loading;
        },
        canNext() {
            return this.currentPage < this.totalPages && !this.loading;
        },
    },
    methods: {
        metricCards(values) {
            return [
                {label: '窗口总数', value: values.total || 0, hint: '全部登记', tone: ''},
                {label: '进行中', value: values.active || 0, hint: '正在抑制外发', tone: 'page-metric-card--warning'},
                {label: '未开始', value: values.pending || 0, hint: '等待生效', tone: ''},
                {label: '已结束', value: values.ended || 0, hint: '历史窗口', tone: 'page-metric-card--success'},
            ];
        },
        async load(page = 1) {
            this.loading = true;
            this.error = '';
            try {
                const payload = await api.get(this.endpoint, {
                    state: this.currentState, page, page_size: this.page_size,
                });
                this.items = payload.results || [];
                this.total = payload.count || 0;
                this.totalPages = this.pageCount(payload.count, payload.page_size);
                this.currentPage = page;
                this.syncUrl();
                await this.refreshMetrics();
            } catch (error) {
                this.error = error instanceof ApiError ? error.message : '列表加载失败。';
            } finally {
                this.loading = false;
            }
        },
        async refreshMetrics() {
            try {
                const meta = await api.get(this.endpoint + 'meta/');
                this.cards = this.metricCards(meta.metrics || {});
            } catch (error) {
                /* 指标是辅助信息，取不到就保留上一次的值 */
            }
        },
        pageCount(count, size) {
            const per = Number(size) || this.page_size;
            return Math.max(1, Math.ceil((Number(count) || 0) / per));
        },
        applyFilter() {
            this.load(1);
        },
        resetFilter() {
            this.currentState = '';
            this.load(1);
        },
        go(page) {
            if (page < 1 || page > this.totalPages) return;
            this.load(page);
        },
        async remove(row) {
            if (!this.can_administer || this.deleting) return;
            this.deleting = row.id;
            this.error = '';
            try {
                await api.delete(this.endpoint + encodeURIComponent(row.id) + '/');
                const page = this.items.length === 1 && this.currentPage > 1
                    ? this.currentPage - 1 : this.currentPage;
                await this.load(page);
            } catch (error) {
                this.error = error instanceof ApiError ? error.message : '删除失败。';
            } finally {
                this.deleting = '';
            }
        },
        syncUrl() {
            const url = new URL(window.location.href);
            if (this.currentState) {
                url.searchParams.set('state', this.currentState);
            } else {
                url.searchParams.delete('state');
            }
            if (this.currentPage > 1) {
                url.searchParams.set('page', String(this.currentPage));
            } else {
                url.searchParams.delete('page');
            }
            window.history.replaceState({}, '', url);
        },
    },
    template: `
    <div class="maintenance-page">
        <section class="page-metric-grid" aria-label="维护窗口统计">
            <article v-for="card in cards" :key="card.label" :class="'page-metric-card glass-card ' + card.tone">
                <span>[[ card.label ]]</span><strong>[[ card.value ]]</strong><small>[[ card.hint ]]</small>
            </article>
        </section>

        <div class="table-toolbar">
            <form class="table-filter-bar" @submit.prevent="applyFilter">
                <div class="table-filter-field">
                    <label class="form-label small text-secondary" for="maintenance-state">状态</label>
                    <select class="form-select" id="maintenance-state" v-model="currentState" @change="applyFilter">
                        <option v-for="option in states" :key="option.value" :value="option.value">[[ option.label ]]</option>
                    </select>
                </div>
                <div class="table-filter-actions">
                    <button class="btn btn-primary text-nowrap" type="submit" :disabled="loading">筛选</button>
                    <button class="btn btn-outline-secondary text-nowrap" type="button" @click="resetFilter" :disabled="loading">重置</button>
                </div>
            </form>
        </div>

        <p v-if="error" class="alert alert-danger" role="alert">[[ error ]]</p>

        <div class="card surface-card table-scroll-shell" :aria-busy="loading ? 'true' : 'false'">
            <p class="table-scroll-hint">窗口内相关资产的告警照常生成并入库，只是不外发</p>
            <div class="table-responsive" role="region" aria-label="维护记录数据表" tabindex="0">
                <table class="table data-table table-hover align-middle mb-0">
                    <caption class="visually-hidden">维护窗口的资产、类型、起止时间、状态与备注</caption>
                    <thead><tr><th v-for="column in columns" :key="column" scope="col" :class="column === '操作' ? 'table-actions-column table-actions-cell' : ''">[[ column ]]</th></tr></thead>
                    <tbody>
                        <tr v-for="row in items" :key="row.id">
                            <td>[[ row.asset_name ]]</td>
                            <td>[[ row.target_type_label ]]</td>
                            <td>[[ row.starts_at_display ]]</td>
                            <td>[[ row.ends_at_display ]]</td>
                            <td><span :class="'status-badge status-badge--' + row.phase_badge">[[ row.phase_label ]]</span></td>
                            <td>[[ row.note || '—' ]]</td>
                            <td class="table-actions-cell">
                                <button v-if="can_administer" class="btn btn-sm btn-outline-danger" type="button"
                                        :disabled="deleting === row.id" @click="remove(row)">删除</button>
                            </td>
                        </tr>
                        <tr v-if="!items.length">
                            <td :colspan="columns.length">
                                <div class="empty-state">
                                    <strong>[[ loading ? '正在加载…' : '暂无维护窗口' ]]</strong>
                                    <span v-if="!loading">在网络设备、服务器或弱电设备列表里勾选资产，点「加入维护窗口」即可批量登记。</span>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <nav v-if="totalPages > 1" class="page-pagination" aria-label="维护记录分页">
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="!canPrevious" @click="go(currentPage - 1)">上一页</button>
            <span class="text-secondary">第 [[ currentPage ]] / [[ totalPages ]] 页，共 [[ total ]] 条</span>
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="!canNext" @click="go(currentPage + 1)">下一页</button>
        </nav>
    </div>`,
};
