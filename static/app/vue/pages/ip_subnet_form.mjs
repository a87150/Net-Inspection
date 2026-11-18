/* IP 管理页的「增加 IP 段」：本页弹窗，提交走 REST，成功后刷新台账。 */
import { api } from '../api.mjs';

const EMPTY = () => ({
    cidr: '',
    purpose: 'office',
    vlan: '',
    gateway: '',
    pool_start: '',
    pool_end: '',
    note: '',
});

export default {
    delimiters: ['[[', ']]'],
    props: {
        endpoint: {type: String, default: '/api/v1/ip-subnets/'},
        purposes: {type: Array, default: () => []},
        can_administer: {type: Boolean, default: false},
    },
    data() {
        return {
            open: false,
            submitting: false,
            message: '',
            errors: {},
            form: EMPTY(),
        };
    },
    methods: {
        show() {
            this.form = EMPTY();
            this.message = '';
            this.errors = {};
            this.open = true;
        },
        hide() {
            if (this.submitting) return;
            this.open = false;
        },
        errorFor(name) {
            const value = this.errors[name];
            if (!value) return '';
            return Array.isArray(value) ? value.join(' ') : String(value);
        },
        async submit() {
            if (this.submitting) return;
            this.submitting = true;
            this.message = '';
            this.errors = {};
            try {
                await api.post(this.endpoint, {...this.form});
                window.location.reload();
            } catch (error) {
                this.message = error.message || '保存失败';
                this.errors = (error.payload && error.payload.errors) || {};
                this.submitting = false;
            }
        },
    },
    template: `
    <div class="d-inline-block">
        <button v-if="can_administer" class="btn btn-primary" type="button" @click="show">增加 IP 段</button>

        <div v-if="open" class="modal fade show d-block" role="dialog" aria-modal="true" aria-labelledby="ip-subnet-modal-title" @click.self="hide">
            <div class="modal-dialog modal-dialog-scrollable modal-shell">
                <div class="modal-content modal-surface">
                    <form @submit.prevent="submit">
                        <div class="modal-header">
                            <h2 class="modal-title fs-5" id="ip-subnet-modal-title">增加 IP 段</h2>
                            <button type="button" class="btn-close" aria-label="关闭" @click="hide"></button>
                        </div>
                        <div class="modal-body modal-body--scroll">
                            <p v-if="message" class="alert alert-danger py-2" role="alert">[[ message ]]</p>
                            <p v-if="errorFor('__all__')" class="alert alert-danger py-2" role="alert">[[ errorFor('__all__') ]]</p>

                            <div class="row g-3">
                                <div class="col-12">
                                    <label class="form-label" for="ip-subnet-cidr">网段</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('cidr')}" id="ip-subnet-cidr" v-model.trim="form.cidr" placeholder="10.1.1.0/24" maxlength="43" required autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('cidr')">[[ errorFor('cidr') ]]</div>
                                    <div class="form-text" v-else>按 CIDR 填写，主机位会被规范到网络地址。</div>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label" for="ip-subnet-purpose">用途</label>
                                    <select class="form-select" id="ip-subnet-purpose" v-model="form.purpose">
                                        <option v-for="item in purposes" :key="item.value" :value="item.value">[[ item.label ]]</option>
                                    </select>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label" for="ip-subnet-vlan">VLAN</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('vlan')}" id="ip-subnet-vlan" v-model.trim="form.vlan" maxlength="32" autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('vlan')">[[ errorFor('vlan') ]]</div>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label" for="ip-subnet-gateway">网关</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('gateway')}" id="ip-subnet-gateway" v-model.trim="form.gateway" placeholder="10.1.1.1" maxlength="39" autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('gateway')">[[ errorFor('gateway') ]]</div>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label" for="ip-subnet-pool-start">地址池开始</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('pool_start')}" id="ip-subnet-pool-start" v-model.trim="form.pool_start" placeholder="10.1.1.100" maxlength="39" autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('pool_start')">[[ errorFor('pool_start') ]]</div>
                                </div>
                                <div class="col-md-6">
                                    <label class="form-label" for="ip-subnet-pool-end">地址池结束</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('pool_end')}" id="ip-subnet-pool-end" v-model.trim="form.pool_end" placeholder="10.1.1.200" maxlength="39" autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('pool_end')">[[ errorFor('pool_end') ]]</div>
                                    <div class="form-text" v-else>开始和结束要一起填，留空表示没有 DHCP 池。</div>
                                </div>
                                <div class="col-12">
                                    <label class="form-label" for="ip-subnet-note">备注</label>
                                    <input class="form-control" :class="{'is-invalid': errorFor('note')}" id="ip-subnet-note" v-model.trim="form.note" maxlength="200" autocomplete="off">
                                    <div class="invalid-feedback d-block" v-if="errorFor('note')">[[ errorFor('note') ]]</div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer modal-footer--sticky">
                            <button type="button" class="btn btn-outline-secondary" @click="hide" :disabled="submitting">取消</button>
                            <button type="submit" class="btn btn-primary" :disabled="submitting">[[ submitting ? '保存中…' : '保存网段' ]]</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
        <div v-if="open" class="modal-backdrop fade show"></div>
    </div>`,
};
