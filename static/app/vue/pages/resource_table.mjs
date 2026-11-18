/* 通用列表：列由后端 TableDefinition 决定，排序/筛选仍是 URL 参数，翻页走 REST。 */
import { api } from '../api.mjs';

export default {
    delimiters: ['[[', ']]'],
    props: {
        endpoint: {type: String, default: ''},
        columns: {type: Array, default: () => []},
        rows: {type: Array, default: () => []},
        count: {type: Number, default: 0},
        page: {type: Number, default: 1},
        pages: {type: Number, default: 1},
        start_index: {type: Number, default: 0},
        end_index: {type: Number, default: 0},
        empty_title: {type: String, default: '暂无记录'},
        empty_message: {type: String, default: ''},
        label: {type: String, default: '记录'},
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
        async load(page) {
            if (!this.endpoint || this.loading || page < 1 || page > this.totalPages) return;
            this.loading = true;
            this.error = '';
            try {
                const params = Object.fromEntries(new URLSearchParams(window.location.search));
                params.page = page;
                const payload = await api.get(this.endpoint, params);
                this.items = payload.rows || payload.results || [];
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
        cell(row, key) {
            const value = row.cells ? row.cells[key] : null;
            return value || {text: '-'};
        },
    },
    template: `
    <div class="resource-table">
        <p v-if="error" class="alert alert-danger py-2" role="alert">[[ error ]]</p>
        <div class="table-responsive" role="region" :aria-label="label + '数据表'" tabindex="0" :aria-busy="loading ? 'true' : 'false'">
            <table class="table data-table table-hover align-middle mb-0">
                <thead>
                    <tr>
                        <th v-for="column in columns" :key="column.key" scope="col" :data-column-key="column.key" v-show="column.default_visible">[[ column.label ]]</th>
                        <th scope="col" class="table-actions-column table-actions-cell">操作</th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="(row, index) in items" :key="index">
                        <td v-for="column in columns" :key="column.key" :data-column-key="column.key" v-show="column.default_visible">
                            <span v-if="cell(row, column.key).badge" class="badge status-badge" :class="'text-bg-' + (cell(row, column.key).badge.tone || 'info')" :data-status="cell(row, column.key).badge.status">[[ cell(row, column.key).badge.label ]]</span>
                            <template v-else>[[ cell(row, column.key).text ]]</template>
                        </td>
                        <td class="table-actions-cell">
                            <a v-if="row.url" class="btn btn-outline-dark btn-sm" :href="row.url">查看详情</a>
                            <span v-else class="text-secondary">—</span>
                        </td>
                    </tr>
                    <tr v-if="!items.length">
                        <td :colspan="columns.length + 1">
                            <div class="empty-state"><strong>[[ empty_title ]]</strong><span>[[ empty_message ]]</span></div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
        <nav v-if="totalPages > 1" class="page-pagination" :aria-label="label + '分页'">
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage <= 1 || loading" @click="load(currentPage - 1)">上一页</button>
            <span class="text-secondary">第 [[ currentPage ]] / [[ totalPages ]] 页，共 [[ total ]] 条（[[ from ]]–[[ to ]]）</span>
            <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage >= totalPages || loading" @click="load(currentPage + 1)">下一页</button>
        </nav>
    </div>`,
};
