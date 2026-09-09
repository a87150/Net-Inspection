"""Single-device entry using the inventory import field catalogue."""
from django import forms
from net.data_exchange.inventory_csv import ENTITY_SPECS

DEVICE_KINDS = {'networks', 'servers', 'monitors'}
CONNECTION_FIELDS = {'connection_type', 'port', 'username', 'password', 'server_type',
                     'api_url', 'api_username', 'api_password', 'api_token', 'verify_ssl'}
BASIC_FIELDS = {'name', 'device_name', 'ip', 'device_type', 'vendor'}


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
    form_class = forms.modelform_factory(spec['model'], form=ServerDeviceForm if kind == 'servers' else forms.ModelForm, fields=fields, labels=labels,
        widgets={name: forms.PasswordInput(attrs={'autocomplete': 'new-password'})
                 for name in spec.get('secret_fields', ())})
    if data is not None:
        data = data.copy()
        for name in fields:
            field = spec['model']._meta.get_field(name)
            if name not in data and field.has_default() and field.get_internal_type() != 'BooleanField':
                data[name] = field.get_default()
    form = form_class(data=data, instance=instance, auto_id='add-device-%s')
    form.fields['ip'] = forms.GenericIPAddressField(label='IP地址', unpack_ipv4=True)
    for name, field in form.fields.items():
        if name in {'port', 'snmp_port'}:
            form.fields[name] = field = forms.IntegerField(label=field.label, min_value=1, max_value=65535)
        field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                       else 'form-select' if isinstance(field.widget, forms.Select)
                                       else 'form-control')
    if kind == 'servers':
        form.fields['server_type'].widget.attrs['data-server-type'] = ''
        form.fields['port'].label = 'SSH 端口'
        form.fields['api_url'].label = '自定义巡检地址（通常留空）'
        form.fields['api_url'].help_text = '默认访问 http://设备IP:9180/inspection；仅改过脚本端口、路径或使用 HTTPS 时填写。'
        form.fields['api_token'].label = '巡检令牌'
    return form


def preserve_empty_secrets(form):
    """Keep saved credentials when an edit form intentionally leaves them blank."""
    if form.instance._state.adding:
        return
    for name in form._meta.model._meta.fields:
        if name.name in form.fields and isinstance(form.fields[name.name].widget, forms.PasswordInput):
            if form.cleaned_data.get(name.name, None) in (None, ''):
                form.instance.__dict__[name.name] = form.initial.get(name.name) or getattr(
                    type(form.instance).objects.get(pk=form.instance.pk), name.name,
                )


def device_form_sections(form):
    if 'server_type' in form.fields:
        return [
            {'title': '基本信息', 'fields': [form[key] for key in ('name', 'ip', 'server_type')]},
            {'title': 'Linux SSH 连接', 'server_type': 'linux', 'fields': [form[key] for key in ('port', 'username', 'password')]},
            {'title': 'Windows 巡检脚本', 'server_type': 'windows', 'fields': [form['api_token']]},
            {'title': 'Windows 高级连接设置（可选）', 'server_type': 'windows', 'advanced': True, 'expanded': bool(form['api_url'].value()), 'fields': [form[key] for key in ('api_url', 'verify_ssl')]},
        ]
    grouped = {'basic': [], 'connection': []}
    for field in form:
        group = 'basic' if field.name in BASIC_FIELDS else 'connection'
        grouped[group].append(field)
    return [{'title': title, 'fields': grouped[key]} for key, title in
            (('basic', '基本信息'), ('connection', '巡检连接设置'))]
