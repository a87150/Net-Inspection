/* 通用详情：标题 + 动作 + 分区（字段 / 列表 / 长文本），数据由后端 payload 决定。 */
export default {
    delimiters: ['[[', ']]'],
    props: {
        eyebrow: {type: String, default: ''},
        title: {type: String, default: ''},
        description: {type: String, default: ''},
        actions: {type: Array, default: () => []},
        sections: {type: Array, default: () => []},
        notice: {type: String, default: ''},
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
        actionClass(action) {
            if (action.variant === 'primary') return 'btn btn-primary';
            if (action.variant === 'success') return 'btn btn-outline-success';
            if (action.variant === 'danger') return 'btn btn-outline-danger';
            return 'btn btn-outline-secondary';
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
                <a v-for="action in actions" :key="action.url" :class="actionClass(action)" :href="action.url">[[ action.label ]]</a>
            </div>
        </header>
        <div v-if="notice" class="alert alert-warning">{{ notice }}</div>
        <div v-for="(section, index) in alerts" :key="'alert-' + index" class="resource-detail__alert"
             :class="'alert alert-' + section.level" role="alert">[[ section.text ]]</div>
        <div class="resource-detail__grid">
            <section v-for="section in blocks" :key="section.heading" class="card surface-card">
                <div v-if="section.heading" class="card-header surface-card__header"><h2 class="h5 mb-0">[[ section.heading ]]</h2></div>
                <div v-if="section.kind === 'table'" class="card-body">
                    <p v-if="section.note" class="small text-secondary">[[ section.note ]]</p>
                    <div class="table-responsive" role="region" :aria-label="section.heading" tabindex="0">
                        <table class="table data-table mb-0">
                            <thead><tr><th v-for="column in section.columns" :key="column" scope="col">[[ column ]]</th></tr></thead>
                            <tbody>
                                <tr v-for="(row, rowIndex) in section.rows" :key="rowIndex">
                                    <td v-for="(cell, cellIndex) in row" :key="cellIndex">[[ cell ]]</td>
                                </tr>
                                <tr v-if="!section.rows.length"><td :colspan="section.columns.length">[[ section.empty ]]</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
                <div v-else-if="section.kind === 'groups'" class="card-body">
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
                </div>
                <div class="card-body">
                    <dl v-if="section.kind === 'fields'" class="row mb-0">
                        <template v-for="field in section.fields" :key="field.label">
                            <dt class="col-sm-4 col-lg-3 text-secondary">[[ field.label ]]</dt>
                            <dd class="col-sm-8 col-lg-9">
                                <span v-if="field.badge" class="badge status-badge" :class="badgeClass(field.badge)" :data-status="field.badge.status">[[ field.badge.label ]]</span>
                                <template v-else>[[ field.value ]]</template>
                            </dd>
                        </template>
                    </dl>
                    <div v-else-if="section.kind === 'list'" class="list-group list-group-flush">
                        <div v-for="(item, index) in section.items" :key="index" class="list-group-item px-0">
                            <div class="d-flex justify-content-between gap-2">
                                <strong>[[ item.label ]]</strong>
                                <span v-if="item.badge" class="badge status-badge" :class="badgeClass(item.badge)" :data-status="item.badge.status">[[ item.badge.label ]]</span>
                            </div>
                            <div v-if="item.meta" class="small text-secondary">[[ item.meta ]]</div>
                            <div v-if="item.detail" class="small text-break mt-1">[[ item.detail ]]</div>
                        </div>
                        <p v-if="!section.items.length" class="text-secondary mb-0">[[ section.empty || '暂无内容' ]]</p>
                    </div>
                    <div v-else>
                        <div class="detail-text">{{ section.text }}</div>
                        <a v-if="section.link" class="btn btn-outline-secondary btn-sm mt-3" :href="section.link.url">[[ section.link.label ]]</a>
                    </div>
                </div>
            </section>
        </div>
    </div>`,
};
