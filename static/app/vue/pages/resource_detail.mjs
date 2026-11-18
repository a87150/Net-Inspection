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
        <div class="resource-detail__grid">
            <section v-for="section in sections" :key="section.heading" class="card surface-card">
                <div class="card-header surface-card__header"><h2 class="h5 mb-0">[[ section.heading ]]</h2></div>
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
