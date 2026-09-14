"""Single-device entry using the inventory import field catalogue."""
from django import forms
from net.data_exchange.inventory_csv import ENTITY_SPECS
from net.secret_masks import MASKED_SECRET, MaskedSecretInput

DEVICE_KINDS = {'networks', 'servers', 'monitors'}
CONNECTION_FIELDS = {'connection_type', 'port', 'username', 'password', 'server_type', 'api_shared_secret',
                     'api_url', 'api_username', 'api_password', 'api_token', 'verify_ssl'}
BASIC_FIELDS = {'name', 'device_name', 'ip', 'device_type', 'vendor', 'os_version'}


class NetworkDeviceForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        api_mode = cleaned.get('connection_type') == 'sangfor_api'
        inactive = (
            ('port', 'username', 'password', 'snmp_version', 'snmp_port', 'snmp_community',
             'snmp_security_level', 'snmp_username', 'snmp_auth_protocol', 'snmp_auth_password',
             'snmp_priv_protocol', 'snmp_priv_password', 'snmp_context_name', 'snmp_retries')
            if api_mode else ('api_url', 'api_shared_secret', 'verify_ssl')
        )
        for name in inactive:
            self._errors.pop(name, None)
            if self.instance and self.instance.pk:
                cleaned[name] = getattr(self.instance, name)
            else:
                cleaned[name] = self._meta.model._meta.get_field(name).get_default()
        return cleaned


class ServerDeviceForm(forms.ModelForm):
    def clean(self):
        cleaned = super().clean()
        inactive = ('port', 'username', 'password') if cleaned.get('server_type') == 'windows' else ('api_url', 'api_token', 'verify_ssl')
        for name in inactive:
            self._errors.pop(name, None)
            cleaned[name] = self.initial.get(name, self._meta.model._meta.get_field(name).get_default())
        return cleaned


def device_form(kind, data=None, instance=None):
    spec = ENTITY_SPECS[kind]
    fields = [name for _, name, _ in spec['columns']
              if name in BASIC_FIELDS or name in CONNECTION_FIELDS or name.startswith('snmp_')]
    labels = {name: label for label, name, _ in spec['columns']}
    secret_fields = spec.get('secret_fields', ())
    form_base = ServerDeviceForm if kind == 'servers' else NetworkDeviceForm if kind == 'networks' else forms.ModelForm
    form_class = forms.modelform_factory(spec['model'], form=form_base, fields=fields, labels=labels,
        widgets={name: MaskedSecretInput(attrs={
            'autocomplete': 'new-password', 'data-secret-mask': 'true',
        }) for name in secret_fields})
    if data is not None:
        data = data.copy()
        if instance and instance.pk:
            if 'os_version' in fields and 'os_version' not in data:
                data['os_version'] = instance.os_version or ''
            for name in secret_fields:
                if data.get(name) in (None, ''):
                    data[name] = MASKED_SECRET
        for name in fields:
            field = spec['model']._meta.get_field(name)
            if name not in data and field.has_default() and field.get_internal_type() != 'BooleanField':
                data[name] = field.get_default()
    form = form_class(data=data, instance=instance, auto_id='add-device-%s')
    if instance and instance.pk and not form.is_bound:
        for name in secret_fields:
            if getattr(instance, name, ''):
                form.initial[name] = MASKED_SECRET
    form.fields['ip'] = forms.GenericIPAddressField(label='IP地址', unpack_ipv4=True)
    for name, field in form.fields.items():
        if name in {'port', 'snmp_port'}:
            form.fields[name] = field = forms.IntegerField(label=field.label, min_value=1, max_value=65535)
        field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                       else 'form-select' if isinstance(field.widget, forms.Select)
                                       else 'form-control')
    if kind in {'networks','monitors'}:
        from net.devices.collection_profiles import vendor_choices, SUBTYPES
        for field_name, options in [('vendor',vendor_choices(kind)),('device_type',SUBTYPES[kind])]:
            if field_name in form.fields:
                choices=[('', '请选择')]+[(value,label) for value,label in options if value]
                current=getattr(instance,field_name,'') if instance else ''
                if current and current not in dict(choices):choices.append((current,current))
                original=form.fields[field_name]
                if field_name=='vendor':
                    form.fields[field_name]=forms.ChoiceField(label=original.label,required=original.required,
                        choices=choices,help_text=original.help_text,widget=forms.Select(attrs={'class':'form-select'}))
                else:original.widget=forms.Select(choices=choices,attrs={'class':'form-select'})
    if kind == 'networks':
        form.fields['connection_type'].choices=[('sangfor_api','深信服 AC Open API'),('auto','SNMP + SSH（默认，缺项自动补充）'),('hybrid','SNMP + SSH（指标不自动回退）'),('snmp','SNMP 指标 + SSH 配置备份'),('ssh','SSH 指标 + SNMP 流量')]
        if not instance:form.initial['connection_type']='auto'
        form.fields['connection_type'].help_text='逐项采集方式以巡检模板为准；默认通过 SNMP 采集指标，SSH 获取日志和配置，缺失指标可用 SSH 补充。'
        form.fields['connection_type'].widget.attrs['data-network-connection']=''
        form.fields['connection_type'].widget.attrs['data-network-new']='true' if not instance else 'false'
        form.fields['vendor'].widget.attrs['data-network-vendor']=''
        form.fields['device_type'].widget.attrs['data-network-device-type']=''
        labels={'port':'SSH 端口','username':'SSH 用户名','password':'SSH 密码','snmp_version':'SNMP 版本','snmp_community':'读取口令（Community）','snmp_port':'SNMP 端口','snmp_security_level':'安全方式','snmp_username':'SNMPv3 用户名','snmp_auth_protocol':'认证算法','snmp_auth_password':'认证密码','snmp_priv_protocol':'加密算法','snmp_priv_password':'加密密码','snmp_context_name':'上下文名称（通常留空）','snmp_retries':'失败重试次数'}
        for key,label in labels.items():form.fields[key].label=label
        form.fields['snmp_version'].widget.attrs['data-snmp-version']=''
        form.fields['snmp_security_level'].widget.attrs['data-snmp-security']=''
        form.fields['snmp_community'].help_text='与设备上的只读 Community 一致。SNMPv2c 通常只需填写此项，端口默认 161。'
        form.fields['snmp_security_level'].choices=[('noAuthNoPriv','仅用户名'),('authNoPriv','用户名 + 认证'),('authPriv','用户名 + 认证 + 加密')]
    if kind == 'servers':
        form.fields['server_type'].widget.attrs['data-server-type'] = ''
        form.fields['port'].label = 'SSH 端口'
        form.fields['api_url'].label = '自定义巡检地址（通常留空）'
        form.fields['api_url'].help_text = '默认访问 http://设备IP:9180/inspection；仅改过脚本端口、路径或使用 HTTPS 时填写。'
        form.fields['api_token'].label = '巡检令牌'
    from index.common.form_examples import apply_field_examples
    from net.data_exchange.inventory_guidance import DEVICE_EXAMPLES
    apply_field_examples(form, DEVICE_EXAMPLES[kind])
    if 'os_version' in form.fields:
        form.fields['os_version'].label = '系统版本（可选）'
    return form


def preserve_empty_secrets(form):
    """Keep saved credentials when an edit form intentionally leaves them blank."""
    if form.instance._state.adding:
        return
    stored = type(form.instance).objects.get(pk=form.instance.pk)
    for name in form._meta.model._meta.fields:
        if name.name in form.fields and isinstance(form.fields[name.name].widget, forms.PasswordInput):
            if form.cleaned_data.get(name.name, None) in (None, '', MASKED_SECRET):
                original = getattr(stored, name.name)
                form.cleaned_data[name.name] = original
                form.instance.__dict__[name.name] = original


def device_form_sections(form):
    if 'server_type' in form.fields:
        return [
            {'title': '基本信息', 'fields': [form[key] for key in ('name', 'ip', 'server_type', 'os_version')]},
            {'title': 'Linux SSH 连接', 'server_type': 'linux', 'fields': [form[key] for key in ('port', 'username', 'password')]},
            {'title': 'Windows 巡检脚本', 'server_type': 'windows', 'fields': [form['api_token']]},
            {'title': 'Windows 高级连接设置（可选）', 'server_type': 'windows', 'advanced': True, 'expanded': bool(form['api_url'].value()), 'fields': [form[key] for key in ('api_url', 'verify_ssl')]},
        ]
    if 'connection_type' in form.fields:
        def section(title,keys,**kwargs):return {'title':title,'fields':[form[k] for k in keys],**kwargs}
        return [section('基本信息',[k for k in form.fields if k in BASIC_FIELDS]),
            section('巡检连接方式',['connection_type']),
            section('深信服 AC Open API',['api_url','api_shared_secret','verify_ssl'],network_fields='sangfor_api'),
            section('SNMP 读取设置',['snmp_version'],network_fields='standard'),
            section('SNMPv2c 读取口令',['snmp_community'],snmp_group='v2c',network_fields='standard'),
            section('SNMPv3 身份与安全',['snmp_username','snmp_security_level'],snmp_group='v3',network_fields='standard'),
            section('SNMPv3 认证',['snmp_auth_protocol','snmp_auth_password'],snmp_group='auth',network_fields='standard'),
            section('SNMPv3 加密',['snmp_priv_protocol','snmp_priv_password'],snmp_group='priv',network_fields='standard'),
            section('SNMP 高级参数（通常不用改）',['snmp_port','snmp_retries','snmp_context_name'],advanced=True,expanded=any(form[k].errors for k in ('snmp_port','snmp_retries','snmp_context_name')),network_fields='standard'),
            section('SSH 登录设置',['port','username','password'],network_fields='standard')]
    grouped = {'basic': [], 'connection': []}
    for field in form:
        group = 'basic' if field.name in BASIC_FIELDS else 'connection'
        grouped[group].append(field)
    return [{'title': title, 'fields': grouped[key]} for key, title in
            (('basic', '基本信息'), ('connection', '巡检连接设置'))]
