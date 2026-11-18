/* 通用详情：标题 + 动作 + 分区。分区种类由后端 payload 决定。 */
const CELL_TEXT = (cell) => (cell && typeof cell === 'object' ? (cell.text || '') : (cell ?? ''));

export default {
    delimiters: ['[[', ']]'],
    props: {
        eyebrow: {type: String, default: ''},
        title: {type: String, default: ''},
        description: {type: String, default: ''},
        actions: {type: Array, default: () => []},
        sections: {type: Array, default: () => []},
        notice: {type: String, default: ''},
        disclosure: {type: Object, default: null},
        csrf: {type: String, default: ''},
    },
    data() {
        return {locationPath: window.location.pathname + window.location.search};
    },
    computed: {
        alerts() {
            return this.sections.filter((section) => section.kind === 'alert');
        },
        blocks() {
            return this.sections.filter((section) => section.kind !== 'alert');
        },
    },
    methods: {
        badgeClass(badge) {
            return badge.class || ('text-bg-' + (badge.tone || 'info'));
        },
        actionClass(variant) {
            if (variant === 'primary') return 'btn btn-primary';
            if (variant === 'success') return 'btn btn-outline-success';
            if (variant === 'danger') return 'btn btn-outline-danger';
            return 'btn btn-outline-secondary';
        },
        cellText(cell) {
            return CELL_TEXT(cell);
        },
        isObject(cell) {
            return Boolean(cell) && typeof cell === 'object';
        },
    },
    template: `
    <div class="resource-detail">
        <header class="page-heading">
            <div class="page-heading__copy">
                <p v-if="eyebrow" class="page-heading__eyebrow">[[ eyebrow ]]</p>
                <h1 class="page-heading__title">[[ title ]]</h1>
                <p v-if="description" class="page-heading__description">[[ description ]]</p>
            </div>
            <div v-if="actions.length" class="page-heading__actions">
                <template v-for="(action, index) in actions" :key="index">
                    <form v-if="action.kind === 'form'" method="post" :action="action.url" :data-confirm-message="action.confirm || null">
                        <input type="hidden" name="csrfmiddlewaretoken" :value="csrf">
                        <input type="hidden" name="next" :value="locationPath">
                        <button :class="actionClass(action.variant)" type="submit">[[ action.label ]]</button>
                    </form>
                    <a v-else :class="actionClass(action.variant)" :href="action.url">[[ action.label ]]</a>
                </template>
            </div>
        </header>
        <div v-if="notice" class="alert alert-warning">{{ notice }}</div>
        <div v-for="(section, index) in alerts" :key="'alert-' + index" class="resource-detail__alert"
             :class="'alert alert-' + section.level" role="alert">[[ section.text ]]</div>
        <div class="resource-detail__grid">
            <section v-for="section in blocks" :key="section.heading" class="card surface-card">
                <div v-if="section.heading" class="card-header surface-card__header"><h2 class="h5 mb-0">[[ section.heading ]]</h2></div>
                <div class="card-body">
                    <p v-if="section.kind === 'table' && section.note" class="small text-secondary">[[ section.note ]]</p>
                    <div v-if="section.kind === 'table'" class="table-responsive" role="region" :aria-label="section.heading" tabindex="0"
                         :data-table-workspace="section.workspace || null" :data-table-key="section.workspace || null">
                        <table class="table data-table mb-0">
                            <thead>
                                <tr>
                                    <th v-for="(column, columnIndex) in section.columns" :key="columnIndex" scope="col" :aria-sort="column.aria_sort || null">
                                        <a v-if="column.sort_url" :href="column.sort_url">[[ column.label ]]</a>
                                        <template v-else>[[ column.label ]]</template>
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="(row, rowIndex) in section.rows" :key="rowIndex">
                                    <td v-for="(cell, cellIndex) in row" :key="cellIndex">
                                        <template v-if="isObject(cell)">
                                            <span v-if="cell.badge" class="badge status-badge" :class="badgeClass(cell.badge)" :data-status="cell.badge.status">[[ cell.badge.label ]]</span>
                                            <span v-if="cell.text">{{ cell.text }}</span>
                                            <a v-if="cell.url" class="btn btn-outline-primary btn-sm" :href="cell.url">[[ cell.label || '查看' ]]</a>
                                            <div v-if="cell.note" class="small text-secondary mt-1">[[ cell.note ]]</div>
                                        </template>
                                        <template v-else>[[ cell ]]</template>
                                    </td>
                                </tr>
                                <tr v-if="!section.rows.length"><td :colspan="section.columns.length">[[ section.empty ]]</td></tr>
                            </tbody>
                        </table>
                    </div>
                    <nav v-if="section.pagination" class="d-flex align-items-center gap-3 small mt-3" aria-label="分页">
                        <a v-if="section.pagination.prev_url" :href="section.pagination.prev_url">上一页</a>
                        <span>[[ section.pagination.label ]]</span>
                        <a v-if="section.pagination.next_url" :href="section.pagination.next_url">下一页</a>
                    </nav>
                    <template v-if="section.kind === 'groups'">
                        <p v-if="section.summary" class="text-secondary">[[ section.summary ]]</p>
                        <div v-for="group in section.groups" :key="group.title" class="card surface-card status-surface mb-3">
                            <div class="card-header surface-card__header d-flex align-items-center justify-content-between gap-2">
                                <strong>[[ group.title ]]</strong>
                                <span class="status-badge status-badge--neutral">[[ group.count ]] 项</span>
                            </div>
                            <ul class="list-group list-group-flush">
                                <li v-for="(item, itemIndex) in group.items" :key="itemIndex" class="list-group-item">
                                    <span v-if="item.badge" class="badge status-badge" :class="badgeClass(item.badge)" :data-status="item.badge.status">[[ item.badge.label ]]</span>
                                    <strong>[[ item.label ]]</strong>
                                    <div class="text-secondary mt-1">[[ item.detail ]]</div>
                                </li>
                            </ul>
                        </div>
                    </template>
                    <dl v-if="section.kind === 'fields'" class="row mb-0">
                        <template v-for="field in section.fields" :key="field.label">
                            <dt class="col-sm-4 col-lg-3 text-secondary">[[ field.label ]]</dt>
                            <dd class="col-sm-8 col-lg-9">
                                <span v-if="field.badge" class="badge status-badge" :class="badgeClass(field.badge)" :data-status="field.badge.status">[[ field.badge.label ]]</span>
                                <template v-else>[[ field.value ]]</template>
                            </dd>
                        </template>
                    </dl>
                    <div v-if="section.kind === 'list'" class="list-group list-group-flush">
                        <div v-for="(item, index) in section.items" :key="index" class="list-group-item px-0">
                            <div class="d-flex justify-content-between gap-2">
                                <strong>[[ item.label ]]</strong>
                                <span v-if="item.badge" class="badge status-badge" :class="badgeClass(item.badge)" :data-status="item.badge.status">[[ item.badge.label ]]</span>
                            </div>
                            <div v-if="item.meta" class="small text-secondary">[[ item.meta ]]</div>
                            <div v-if="item.detail" class="small text-break mt-1">[[ item.detail ]]</div>
                            <a v-if="item.url" :href="item.url" class="small">[[ item.action || '查看详情' ]]</a>
                        </div>
                        <p v-if="!section.items.length" class="text-secondary mb-0">[[ section.empty || '暂无内容' ]]</p>
                    </div>
                    <template v-if="section.kind === 'form'">
                        <p v-if="section.note" class="text-secondary">[[ section.note ]]</p>
                        <dl class="row small mb-0">
                            <template v-for="field in section.fields" :key="field.label">
                                <dt class="col-sm-3">[[ field.label ]]</dt>
                                <dd class="col-sm-9 mb-0">[[ field.value ]]</dd>
                            </template>
                        </dl>
                        <form method="post" :action="section.submit.url" class="d-flex flex-wrap gap-2 align-items-end mt-3"
                              :data-confirm-message="section.submit.confirm || null">
                            <input type="hidden" name="csrfmiddlewaretoken" :value="csrf">
                            <div v-for="input in section.inputs" :key="input.name">
                                <label class="visually-hidden" :for="'field-' + input.name">[[ input.label ]]</label>
                                <input class="form-control form-control-sm" :type="input.type || 'text'" :id="'field-' + input.name"
                                       :name="input.name" :autocomplete="input.autocomplete || 'off'" :placeholder="input.label">
                            </div>
                            <button :class="actionClass(section.submit.variant)" type="submit">[[ section.submit.label ]]</button>
                        </form>
                    </template>
                    <div v-if="section.kind === 'text'">
                        <div class="detail-text">[[ section.text ]]</div>
                        <a v-if="section.link" class="btn btn-outline-secondary btn-sm mt-3" :href="section.link.url">[[ section.link.label ]]</a>
                    </div>
                </div>
            </section>
        </div>
        <details v-if="disclosure" class="card surface-card card-body mt-3">
            <summary>[[ disclosure.heading ]]</summary>
            <pre class="json-view text-wrap text-break">[[ disclosure.text ]]</pre>
        </details>
    </div>`,
};
