"""Single-device entry using the inventory import field catalogue."""
from django import forms
from net.data_exchange.inventory_csv import ENTITY_SPECS

DEVICE_KINDS = {'networks', 'servers', 'monitors'}
CONNECTION_FIELDS = {'connection_type', 'port', 'username', 'password', 'server_type',
                     'api_url', 'api_username', 'api_password', 'api_token', 'verify_ssl'}
BASIC_FIELDS = {'name', 'device_name', 'ip', 'device_type', 'vendor'}


def device_form(kind, data=None):
    spec = ENTITY_SPECS[kind]
    fields = [name for _, name, _ in spec['columns']
              if name in BASIC_FIELDS or name in CONNECTION_FIELDS or name.startswith('snmp_')]
    labels = {name: label for label, name, _ in spec['columns']}
    form_class = forms.modelform_factory(spec['model'], fields=fields, labels=labels,
        widgets={name: forms.PasswordInput(attrs={'autocomplete': 'new-password'})
                 for name in spec.get('secret_fields', ())})
    if data is not None:
        data = data.copy()
        for name in fields:
            field = spec['model']._meta.get_field(name)
            if name not in data and field.has_default() and field.get_internal_type() != 'BooleanField':
                data[name] = field.get_default()
    form = form_class(data=data, auto_id='add-device-%s')
    form.fields['ip'] = forms.GenericIPAddressField(label='IP地址', unpack_ipv4=True)
    for name, field in form.fields.items():
        if name in {'port', 'snmp_port'}:
            form.fields[name] = field = forms.IntegerField(label=field.label, min_value=1, max_value=65535)
        field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                       else 'form-select' if isinstance(field.widget, forms.Select)
                                       else 'form-control')
    return form


def device_form_sections(form):
    grouped = {'basic': [], 'connection': []}
    for field in form:
        group = 'basic' if field.name in BASIC_FIELDS else 'connection'
        grouped[group].append(field)
    return [{'title': title, 'fields': grouped[key]} for key, title in
            (('basic', '基本信息'), ('connection', '巡检连接设置'))]
