/* 资产工作区：左侧列表 + 右侧详情。点行只换右栏，不离开页面。 */
import { api } from '../api.mjs';
import ResourceDetail from './resource_detail.mjs';

export default {
    delimiters: ['[[', ']]'],
    components: {ResourceDetail},
    props: {
        endpoint: {type: String, default: ''},
        detailBase: {type: String, default: ''},
        columns: {type: Array, default: () => []},
        rows: {type: Array, default: () => []},
        count: {type: Number, default: 0},
        page: {type: Number, default: 1},
        pages: {type: Number, default: 1},
        start_index: {type: Number, default: 0},
        end_index: {type: Number, default: 0},
        emptyTitle: {type: String, default: '暂无数据'},
        emptyMessage: {type: String, default: ''},
        label: {type: String, default: '资产'},
        regionLabel: {type: String, default: ''},
        selectAttr: {type: String, default: ''},
        formId: {type: String, default: ''},
        csrf: {type: String, default: ''},
        selectedId: {type: String, default: ''},
        initialDetail: {type: Object, default: null},
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
            visible: {},
            detail: this.initialDetail,
            detailId: this.selectedId,
            detailLoading: false,
            detailError: '',
        };
    },
    computed: {
        visibleColumns() {
            return this.columns.filter((column) => this.visible[column.key] !== false);
        },
        sort() {
            return new URLSearchParams(window.location.search).get('sort') || '';
        },
        order() {
            return new URLSearchParams(window.location.search).get('order') || 'asc';
        },
        columnCount() {
            return this.visibleColumns.length + (this.selectAttr ? 1 : 0) + 1;
        },
    },
    mounted() {
        this.syncColumnToggles();
        document.addEventListener('change', this.syncColumnToggles);
    },
    unmounted() {
        document.removeEventListener('change', this.syncColumnToggles);
    },
    methods: {
        syncColumnToggles() {
            const next = {};
            document.querySelectorAll('[data-column-toggle][data-column-key]')
                .forEach((toggle) => { next[toggle.dataset.columnKey] = toggle.checked; });
            this.visible = next;
        },
        async load(page) {
            if (!this.endpoint || this.loading || page < 1 || page > this.totalPages) return;
            this.loading = true;
            this.error = '';
            try {
                const params = Object.fromEntries(new URLSearchParams(window.location.search));
                params.page = page;
                const payload = await api.get(this.endpoint, params);
                this.items = payload.results || [];
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
        async sortBy(column) {
            if (!column.sortable) return;
            const url = new URL(window.location.href);
            url.searchParams.set('sort', column.key);
            url.searchParams.set('order', this.nextOrder(column));
            url.searchParams.delete('page');
            window.history.replaceState({}, '', url);
            await this.load(1);
        },
        nextOrder(column) {
            return this.sort === column.key && this.order === 'asc' ? 'desc' : 'asc';
        },
        ariaSort(column) {
            if (!column.sortable || this.sort !== column.key) return null;
            return this.order === 'asc' ? 'ascending' : 'descending';
        },
        cell(row, key) {
            const value = row.cells ? row.cells[key] : null;
            return value || {text: '-'};
        },
        badgeClass(row, key) {
            const badge = this.cell(row, key).badge || {};
            return badge.class || ('text-bg-' + (badge.tone || 'info'));
        },
        actionClass(action) {
            const style = action.variant === 'primary'
                ? 'btn-outline-primary'
                : (action.danger ? 'btn-outline-danger' : 'btn-outline-dark');
            return 'btn btn-sm ' + style;
        },
        async openDetail(row) {
            const id = row.id || '';
            if (!id || !this.detailBase || this.detailLoading) return;
            this.detailLoading = true;
            this.detailError = '';
            try {
                this.detail = await api.get(this.detailBase + id + '/detail.json');
                this.detailId = id;
                const url = new URL(window.location.href);
                url.searchParams.set('selected', id);
                window.history.replaceState({}, '', url);
            } catch (failure) {
                this.detailError = failure.message || '详情加载失败';
            } finally {
                this.detailLoading = false;
            }
        },
        closeDetail() {
            this.detail = null;
            this.detailId = '';
            const url = new URL(window.location.href);
            url.searchParams.delete('selected');
            window.history.replaceState({}, '', url);
        },
        rowClick(row, event) {
            if (event.target.closest('a, button, input, form')) return;
            this.openDetail(row);
        },
    },
    template: `
    <div class="row g-3">
        <div :class="detail ? 'col-12 col-lg-7' : 'col-12'">
            <p v-if="error" class="alert alert-danger py-2" role="alert">[[ error ]]</p>
            <div class="table-responsive" role="region" :aria-label="regionLabel || (label + '数据表')" tabindex="0" :aria-busy="loading ? 'true' : 'false'">
                <table class="table data-table table-hover align-middle mb-0">
                    <thead><tr>
                        <th v-if="selectAttr" scope="col" class="table-selection-column" data-device-selection-column><span class="visually-hidden">选择目标</span></th>
                        <th v-for="column in visibleColumns" :key="column.key" scope="col" :data-column-key="column.key" :aria-sort="ariaSort(column)">
                            <a v-if="column.sortable" href="#" :data-sort-key="column.key" @click.prevent="sortBy(column)">[[ column.label ]]</a>
                            <template v-else>[[ column.label ]]</template>
                        </th>
                        <th scope="col" class="table-actions-column table-actions-cell">操作</th>
                    </tr></thead>
                    <tbody>
                    <tr v-for="(row, index) in items" :key="row.id || index" :class="{'table-active': detailId && row.id === detailId}" style="cursor:pointer" @click="rowClick(row, $event)">
                        <td v-if="selectAttr" class="table-selection-column" data-device-selection-column>
                            <input class="form-check-input" type="checkbox" name="target_ids" :value="row.id" :form="formId || null" :aria-label="'选择 ' + (row.id || '')" v-bind="{[selectAttr]: ''}">
                        </td>
                        <td v-for="column in visibleColumns" :key="column.key" :data-column-key="column.key">
                            <span v-if="cell(row, column.key).badge" class="badge status-badge" :class="badgeClass(row, column.key)">[[ cell(row, column.key).badge.label ]]</span>
                            <template v-else>[[ cell(row, column.key).text ]]</template>
                        </td>
                        <td class="text-end table-actions-cell"><div class="d-inline-flex gap-2 table-row-actions">
                            <template v-for="(action, actionIndex) in (row.actions || [])" :key="actionIndex">
                                <form v-if="action.kind === 'form'" method="post" :action="action.url" class="d-inline">
                                    <input type="hidden" name="csrfmiddlewaretoken" :value="csrf">
                                    <input v-for="(value, name) in (action.fields || {})" :key="name" type="hidden" :name="name" :value="value">
                                    <button :class="actionClass(action)" type="submit">[[ action.label ]]</button>
                                </form>
                                <button v-else-if="action.kind === 'modal'" type="button" :class="actionClass(action)" data-bs-toggle="modal" :data-bs-target="action.modal_target ? '#' + action.modal_target : null">[[ action.label ]]</button>
                                <a v-else :class="actionClass(action)" :href="action.url" :data-modal-load="action.modal_target || null">[[ action.label ]]</a>
                            </template>
                        </div></td>
                    </tr>
                    <tr v-if="!items.length"><td :colspan="columnCount"><div class="empty-state"><strong>[[ emptyTitle ]]</strong><span>[[ emptyMessage ]]</span></div></td></tr>
                    </tbody>
                </table>
            </div>
            <nav v-if="totalPages > 1" class="page-pagination" :aria-label="label + '分页'">
                <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage <= 1 || loading" @click="load(currentPage - 1)">上一页</button>
                <span class="text-secondary">第 [[ currentPage ]] / [[ totalPages ]] 页，共 [[ total ]] 条（[[ from ]]–[[ to ]]）</span>
                <button class="btn btn-outline-secondary btn-sm" type="button" :disabled="currentPage >= totalPages || loading" @click="load(currentPage + 1)">下一页</button>
            </nav>
        </div>
        <div v-if="detail || detailLoading || detailError" class="col-12 col-lg-5">
            <div class="card surface-card h-100"><div class="card-body">
                <p v-if="detailError" class="alert alert-danger py-2 mb-2" role="alert">[[ detailError ]]</p>
                <p v-if="detailLoading" class="text-secondary mb-0">正在加载详情…</p>
                <resource-detail v-else-if="detail" v-bind="detail"></resource-detail>
            </div></div>
            <button class="btn btn-outline-secondary btn-sm mt-2" type="button" @click="closeDetail">收起详情</button>
        </div>
    </div>`,
};
