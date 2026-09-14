"""Administrator forms for reusable and per-device collection settings."""
import json
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from index.common.access import admin_required
from net.models import DeviceCollectionTemplate, DeviceCollectionBinding, Network_Device, Server, SecurityDevice
from net.devices.collection_profiles import (
    vendor_choices, SUBTYPES, SECRET_FIELDS, SNMP_FIELDS, normalize_vendor, normalize_subtype,
    resolve_collection_settings, validate_settings, encrypt_credentials, decrypt_credentials, collection_method_choices, template_settings, merge,
)
from net.inspections.issues import PROJECT_RULES, METRIC_DEFAULTS, PROJECT_LABELS
from net.devices.pc.severity import LEVELS

MODELS = {'networks':Network_Device,'servers':Server,'monitors':SecurityDevice}

class CollectionSettingsForm(forms.Form):
    version = forms.CharField(required=False, widget=forms.HiddenInput)
    item_mode = forms.ChoiceField(label='巡检项目', choices=[('inherit','继承项目/上级模板'),('custom','单独选择')])
    selected_items = forms.MultipleChoiceField(label='采集项目',required=False, widget=forms.CheckboxSelectMultiple)
    alert_mode = forms.ChoiceField(label='报警项目', choices=[('inherit','继承全部已选巡检项目'),('custom','单独选择（可全部关闭）')])
    alert_items = forms.MultipleChoiceField(label='需要报警的项目', required=False, widget=forms.CheckboxSelectMultiple)
    commands = forms.JSONField(label='命令映射 JSON', required=False, widget=forms.Textarea(attrs={'rows':5}))
    parsers = forms.JSONField(label='解析模板 JSON',required=False,widget=forms.Textarea(attrs={'rows':6}))
    snmp_oids = forms.JSONField(label='SNMP OID 映射 JSON',required=False,widget=forms.Textarea(attrs={'rows':4}))
    mib_modules = forms.JSONField(label='已导入 MIB 符号映射 JSON',required=False,widget=forms.Textarea(attrs={'rows':4}))
    mib_file = forms.FileField(label='导入厂商 MIB 文件（ASN.1 文本）',required=False)

    def __init__(self,*args,kind,saved=None,version='',api_mode=False,**kwargs):
        saved=saved or {}; self.api_mode=api_mode; initial={**saved,'version':version,
            'item_mode':'custom' if 'selected_items' in saved else 'inherit',
            'alert_mode':'custom' if 'alert_items' in saved else 'inherit'}
        super().__init__(*args,initial=initial,**kwargs)
        self.kind=kind;self.saved=saved;self.rule_rows=[]
        self.rule_keys=list(PROJECT_RULES[kind])
        if self.api_mode:
            from net.devices.network.sangfor import DEFAULT_ITEMS
            self.rule_keys=list(DEFAULT_ITEMS)
            for name in ('commands','parsers','snmp_oids','mib_modules','mib_file'):self.fields.pop(name,None)
        elif kind == 'networks':
            from net.inspections.selection import NETWORK_FIELDS, NETWORK_FUNCTION_ITEMS
            supported = set(NETWORK_FIELDS) | set(NETWORK_FUNCTION_ITEMS) | {'traffic', 'inspection_collection'}
            self.rule_keys = [key for key in self.rule_keys if key in supported]
        for name in ('item_mode','selected_items','alert_mode','alert_items'):self.fields.pop(name,None)
        for key in self.rule_keys:
            _,label=PROJECT_RULES[kind][key]
            names=[]
            for prefix,rule in [('level',key),('missing','missing.'+key)]:
                name=f'{prefix}_{key}';names.append(name)
                self.fields[name]=forms.ChoiceField(label=label,required=False,choices=[('','继承'),*LEVELS.items()],initial=saved.get('severity_overrides',{}).get(rule,''))
            threshold=None
            if key in METRIC_DEFAULTS.get(kind,{}):
                name='threshold_'+key
                self.fields[name]=forms.FloatField(required=False,label=label+'阈值',min_value=0.01,max_value=200 if key=='temperature' else 100,initial=saved.get('thresholds',{}).get(key))
                threshold=self[name]
            self.rule_rows.append((label,self[names[0]],self[names[1]],threshold))
        self.command_rows=[]
        if kind=='networks' and not self.api_mode:
            from net.devices.network.templates import TEMPLATE_ITEMS
            for key in self.rule_keys:
                _,label=PROJECT_RULES[kind][key]
                if key not in TEMPLATE_ITEMS:continue
                parser=saved.get('parsers',{}).get(key,{})
                definitions={
                    'commands':forms.CharField(label='采集命令（一行一条）',required=False,widget=forms.Textarea(attrs={'rows':3}),initial='\n'.join(saved.get('commands',{}).get(key,[]))),
                    'engine':forms.ChoiceField(label='回显解析方式',required=False,choices=[('','继承 / 内置解析'),('regex','正则表达式'),('textfsm','TextFSM')],initial=parser.get('engine','')),
                    'template':forms.CharField(label='解析模板',required=False,strip=False,widget=forms.Textarea(attrs={'rows':5}),initial=parser.get('template','')),
                    'flags':forms.CharField(label='正则选项（i / m / s）',required=False,initial=parser.get('flags','')),
                    'empty':forms.CharField(label='明确空表的回显规则（可选）',required=False,max_length=1024,initial=parser.get('empty_pattern','')),
                    'sample':forms.CharField(label='粘贴设备回显测试（不保存）',required=False,strip=False,max_length=1048576,widget=forms.Textarea(attrs={'rows':4})),
                }
                row={'key':key,'label':label}
                for suffix,field in definitions.items():
                    name=f'{suffix}_{key}';self.fields[name]=field;row[suffix]=self[name]
                self.command_rows.append(row)
        if kind!='networks':
            for key in ('commands','parsers'):self.fields.pop(key)
        if kind=='servers':
            for key in ('snmp_oids','mib_modules','mib_file'):self.fields.pop(key)
        if kind=='monitors':
            self.fields['protocol']=forms.ChoiceField(label='采集方式',choices=[('','继承 / 自动'),('auto','自动 API / SNMP / Ping'),('api','API'),('snmp','SNMP'),('ping','仅 Ping 在线检查')],required=False,initial=saved.get('protocol',''))
            choices={'snmp_version':Network_Device.SNMP_VERSION_CHOICES,'snmp_security_level':Network_Device.SNMP_SECURITY_LEVEL_CHOICES,'snmp_auth_protocol':[('','未设置'),*Network_Device.SNMP_AUTH_PROTOCOL_CHOICES],'snmp_priv_protocol':[('','未设置'),*Network_Device.SNMP_PRIV_PROTOCOL_CHOICES]}
            for key in SNMP_FIELDS:
                field=Network_Device._meta.get_field(key).formfield(required=False)
                field.label={'snmp_username':'SNMPv3 用户名','snmp_context_name':'SNMP 上下文','snmp_retries':'SNMP 重试次数'}.get(key,key)
                field.initial=saved.get('snmp',{}).get(key,'')
                self.fields[key]=field
        self.item_rows=[]
        command_rows={row['key']:row for row in self.command_rows}
        for key in self.rule_keys:
            _,label=PROJECT_RULES[kind][key]
            methods=[] if self.api_mode else collection_method_choices(kind,key)
            if kind=='networks' and saved.get('item_methods',{}).get(key)!='auto':methods=[choice for choice in methods if choice[0]!='auto']
            row={'key':key,'label':label,'level':self['level_'+key],'missing':self['missing_'+key],
                'threshold':self['threshold_'+key] if 'threshold_'+key in self.fields else None,
                'ssh':command_rows.get(key),'snmp_fields':[], 'api': self._api_description(key) if self.api_mode else None}
            state='enabled_'+key
            self.fields[state]=forms.ChoiceField(label='项目适用性', required=False, choices=[('','继承'),('yes','适用'),('no','不适用')], initial=('yes' if saved['item_enabled'][key] else 'no') if key in saved.get('item_enabled',{}) else '')
            row['enabled']=self[state]
            local=any(key in saved.get(section,{}) for section in ('item_methods','item_enabled','commands','parsers','thresholds','severity_overrides')) or 'missing.'+key in saved.get('severity_overrides',{})
            metrics_for_item={'cpu':['cpu'],'memory':['memory_total','memory_used'],'temperature':['temperature']}.get(key,[])
            local=local or any(metric in saved.get(section,{}) for metric in metrics_for_item for section in ('snmp_oids','snmp_transforms'))
            mode='rule_mode_'+key
            self.fields[mode]=forms.ChoiceField(label='项目设置',choices=[('inherit','继承父模板 / 默认规则'),('custom','单独修改此项目')],initial='custom' if local else 'inherit')
            row['mode']=self[mode]
            if self.is_bound and mode not in self.data:
                self.fields[mode].required=False
            if methods:
                name='method_'+key
                self.fields[name]=forms.ChoiceField(label='巡检方式',choices=[('','继承父模板 / 内置方式'),*methods],required=False,initial=saved.get('item_methods',{}).get(key,''))
                row['method']=self[name]
            metrics={'cpu':['cpu'],'memory':['memory_total','memory_used'],'temperature':['temperature']}.get(key,[]) if kind=='networks' and not self.api_mode else []
            for metric in metrics:
                transform=saved.get('snmp_transforms',{}).get(metric,{})
                fields=[]
                for suffix,field in (
                    ('oid',forms.CharField(label={'memory_total':'内存总量 OID','memory_used':'已用内存 OID'}.get(metric,'指标 OID'),required=False,initial=saved.get('snmp_oids',{}).get(metric,''))),
                    ('scale',forms.FloatField(label='数值倍率',required=False,min_value=0.000000001,initial=transform.get('scale',1))),
                    ('offset',forms.FloatField(label='数值偏移',required=False,initial=transform.get('offset',0))),
                ):
                    name=f'snmp_{suffix}_{metric}';self.fields[name]=field;fields.append(self[name])
                row['snmp_fields'].append(fields)
            self.item_rows.append(row)
        from .parameter_examples import collection_examples
        collection_examples(self)
        for field in self.fields.values():
            if not isinstance(field.widget,(forms.HiddenInput,forms.CheckboxSelectMultiple)):
                field.widget.attrs['class']='form-select' if isinstance(field.widget,forms.Select) else 'form-control'

    def _api_description(self, key):
        from net.devices.network.sangfor import STATUS_ENDPOINTS
        endpoint, _converter = STATUS_ENDPOINTS[key]
        fields = {
            'device_info':'data → version', 'online_users':'data → count', 'sessions':'data → count',
            'inside_libraries':'data → libraries', 'log_statistics':'data → JSON 对象',
            'cpu':'data → usage_percent', 'memory':'data → usage_percent',
            'disk_usage':'data → usage_percent', 'system_time':'data → value',
            'bandwidth_usage':'data → usage_percent', 'throughput':'data → send / recv / unit',
        }
        return {'endpoint':'/v1/status/'+endpoint, 'fields':fields[key], 'method':'POST（_method=GET）' if key=='throughput' else 'GET'}

    def settings_value(self):
        d=self.cleaned_data;result={}
        result['severity_overrides']={key if prefix=='level' else 'missing.'+key:d[prefix+'_'+key] for key in PROJECT_RULES[self.kind] for prefix in ('level','missing') if d.get(prefix+'_'+key)}
        result['thresholds']={key:d['threshold_'+key] for key in self.rule_keys if key in METRIC_DEFAULTS.get(self.kind,{}) and d.get('threshold_'+key) is not None}
        for key in ('commands','parsers','snmp_oids','mib_modules'):
            if d.get(key): result[key]=d[key]
        result['item_enabled']={row['key']:d['enabled_'+row['key']]=='yes' for row in self.item_rows if d.get('enabled_'+row['key'])}
        result['item_methods']={row['key']:d['method_'+row['key']] for row in self.item_rows if d.get('method_'+row['key'])}
        if self.kind=='networks':
            for metric in ('cpu','memory_total','memory_used','temperature'):
                if 'snmp_oid_'+metric not in self.data:continue
                oid=d.get('snmp_oid_'+metric,'')
                if oid:result.setdefault('snmp_oids',{})[metric]=oid
                else:result.get('snmp_oids',{}).pop(metric,None)
                scale=d.get('snmp_scale_'+metric) if d.get('snmp_scale_'+metric) is not None else 1
                offset=d.get('snmp_offset_'+metric) or 0
                if oid or scale!=1 or offset!=0:
                    if not oid:raise ValidationError('设置 SNMP 倍率或偏移时，请填写对应的指标 OID。')
                    result.setdefault('snmp_transforms',{})[metric]={'scale':scale,'offset':offset}
            for row in self.command_rows:
                key=row['key']
                if 'commands_'+key in self.data:
                    commands=[line.strip() for line in d.get('commands_'+key,'').splitlines() if line.strip()]
                    if commands:result.setdefault('commands',{})[key]=commands
                    else:result.get('commands',{}).pop(key,None)
                if 'engine_'+key in self.data:
                    engine=d.get('engine_'+key)
                    if engine:
                        result.setdefault('parsers',{})[key]={'engine':engine,'template':d.get('template_'+key,''),'flags':d.get('flags_'+key,'')}
                        empty=d.get('empty_'+key)
                        if empty:result['parsers'][key]['empty_pattern']=empty
                    else:result.get('parsers',{}).pop(key,None)
        if self.kind=='monitors':
            if d.get('protocol'):result['protocol']=d['protocol']
            snmp={key:d[key] for key in SNMP_FIELDS if d.get(key) not in (None,'')}
            if snmp:result['snmp']=snmp
        if d.get('mib_file'):
            from net.devices.network.templates import compile_mib_text
            uploaded=d['mib_file']
            if uploaded.size>1048576:raise ValidationError('MIB 文件超过 1 MB。')
            try:compiled=compile_mib_text(uploaded.read().decode('utf-8-sig'))
            except UnicodeError:raise ValidationError('MIB 文件必须是 UTF-8 或 ASCII 文本。') from None
            modules=result.get('mib_modules',[])
            result['mib_modules']=[m for m in modules if m['name']!=compiled['name']]+[compiled]
        for row in self.item_rows:
            key=row['key']
            if d.get('rule_mode_'+key) != 'inherit':continue
            for section in ('item_methods','item_enabled','commands','parsers','thresholds','severity_overrides'):
                result.get(section,{}).pop(key,None)
            result.get('severity_overrides',{}).pop('missing.'+key,None)
            for metric in {'cpu':['cpu'],'memory':['memory_total','memory_used'],'temperature':['temperature']}.get(key,[]):
                result.get('snmp_oids',{}).pop(metric,None)
                result.get('snmp_transforms',{}).pop(metric,None)
        validate_settings(self.kind,result)
        return result

    def clean(self):
        d=super().clean()
        for row in self.item_rows:
            key=row['key']
            if d.get('rule_mode_'+key)=='inherit':
                for prefix in ('commands','engine','template','flags','empty','sample','threshold','level','missing','method','enabled'):
                    self._errors.pop(prefix+'_'+key,None);d.pop(prefix+'_'+key,None)
                for metric in {'cpu':['cpu'],'memory':['memory_total','memory_used'],'temperature':['temperature']}.get(key,[]):
                    for prefix in ('snmp_oid','snmp_scale','snmp_offset'):
                        self._errors.pop(prefix+'_'+metric,None);d.pop(prefix+'_'+metric,None)
        return d


def _kind(kind):
    if kind not in MODELS:raise Http404


def _preview_template(form, item):
    from net.devices.network.templates import parse_template_output
    from net.infrastructure.ssh_collectors import _template_network_data
    settings=merge(getattr(form,'inherited_settings',{}),form.settings_value())
    parser=settings.get('parsers',{}).get(item)
    if not parser:raise ValidationError('请选择解析方式并填写解析模板，再测试回显。')
    sample=form.cleaned_data.get('sample_'+item,'')
    if not sample:raise ValidationError('请先粘贴需要测试的设备回显。')
    try:parsed=parse_template_output(parser,sample)
    except (ValueError,TimeoutError):raise ValidationError('解析失败或超时，请检查模板与回显格式。') from None
    form.preview_result=json.dumps({'匹配结果':parsed,'归一化指标':_template_network_data({item:parsed})},ensure_ascii=False,indent=2)
    form.preview_item=item


def _bind_form(kind,data,files,saved,version,*,api_mode=False):
    return CollectionSettingsForm(data,files,kind=kind,saved=saved,version=version,api_mode=api_mode)


def _is_sangfor_api_template(kind, template=None, data=None):
    if kind != 'networks':
        return False
    values = data or {}
    vendor = getattr(template, 'vendor', None) if template else values.get('vendor')
    subtype = getattr(template, 'subtype', None) if template else values.get('subtype')
    # The vendor-wide base is the parent of the API gateway template, so it
    # must expose the same API-only contract as its ac_gateway child.
    return vendor == 'sangfor' and subtype in {'', 'ac_gateway'}

@admin_required
def collection_templates(request,kind):
    _kind(kind)
    template=get_object_or_404(DeviceCollectionTemplate,pk=request.GET['edit'],kind=kind) if request.GET.get('edit') else None
    parent_hint = None
    if kind == 'networks' and not template and request.GET.get('parent'):
        try:
            parent_hint = get_object_or_404(DeviceCollectionTemplate, pk=request.GET['parent'], kind=kind, version_match='')
        except (ValidationError, ValueError):
            raise Http404 from None
        if not parent_hint.subtype:
            raise Http404
    preset={}
    preset_vendor=request.GET.get('builtin','') if kind=='networks' and not template else ''
    if preset_vendor in {'huawei','h3c','ruijie','cisco'}:
        from net.devices.network.templates import builtin_collection_settings
        preset={'commands':builtin_collection_settings(preset_vendor)['commands']}
    scope = request.POST if request.method == 'POST' else {'vendor': parent_hint.vendor, 'subtype': parent_hint.subtype} if parent_hint else {}
    api_mode=_is_sangfor_api_template(kind, template, scope)
    form=_bind_form(kind,request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None,template.settings if template else preset,template.updated_at.isoformat() if template else '',api_mode=api_mode)
    for name,field in {
        'parent':forms.ModelChoiceField(label='继承父模板',queryset=DeviceCollectionTemplate.objects.filter(kind=kind,version_match='').exclude(pk=template.pk if template else None),required=False,initial=template.parent_id if template else parent_hint.pk if parent_hint else None,empty_label='无父模板（使用内置默认规则）'),
        'name':forms.CharField(label='模板名称',max_length=150,initial=template.name if template else ''),
        'vendor':forms.ChoiceField(label='适用厂商',required=kind!='servers' and not (template and not template.vendor),choices=vendor_choices(kind,template.vendor if template else ''),initial=template.vendor if template else parent_hint.vendor if parent_hint else preset_vendor),
        'subtype':forms.ChoiceField(label='适用设备类型',required=False,choices=[('', '基础模板（未限定类型）'),*SUBTYPES[kind][1:]],initial=template.subtype if template else parent_hint.subtype if parent_hint else ''),
        'is_enabled':forms.BooleanField(label='启用模板',required=False,initial=template.is_enabled if template else True),
    }.items():
        field.widget.attrs['class']='form-check-input' if isinstance(field.widget,forms.CheckboxInput) else 'form-select' if isinstance(field.widget,forms.Select) else 'form-control'
        form.fields[name]=field
    if kind == 'networks':
        form.fields['version_match'] = forms.CharField(label='版本匹配关键字', max_length=96, required=False,
            initial=template.version_match if template else '', widget=forms.TextInput(attrs={'class': 'form-control'}),
            help_text='留空为基础 / 类型模板；填写后为第三层版本模板，必须继承对应类型模板。例如 V200R019、7.1.070。不区分大小写，不使用正则或通配符。')
    if kind=='servers':form.fields.pop('vendor',None)
    parent_id=request.POST.get('parent') if request.method=='POST' else (template.parent_id if template else parent_hint.pk if parent_hint else None)
    try: parent=DeviceCollectionTemplate.objects.filter(kind=kind,pk=parent_id).first() if parent_id else None
    except (ValidationError,ValueError): parent=None
    form.inherited_settings=template_settings(parent)
    if request.POST.get('preview_inheritance'):
        for name in ('name','vendor'):
            if name in form.fields:form.fields[name].required=False
    saved=False
    if request.method=='POST' and form.is_valid() and request.POST.get('preview_inheritance'):
        pass
    elif request.method=='POST' and form.is_valid() and request.POST.get('preview_item'):
        try:_preview_template(form,request.POST['preview_item'])
        except ValidationError as exc:form.add_error(None,'；'.join(exc.messages))
    elif request.method=='POST' and form.is_valid():
        try:
            value=form.settings_value()
            with transaction.atomic():
                # Serialize parent changes so concurrent saves cannot introduce a cycle.
                list(DeviceCollectionTemplate.objects.filter(kind=kind).order_by('pk').select_for_update())
                if template:
                    row=DeviceCollectionTemplate.objects.select_for_update().get(pk=template.pk)
                    if row.updated_at.isoformat()!=form.cleaned_data['version']:raise ValidationError('模板已被修改，请刷新后重试。')
                else:row=DeviceCollectionTemplate(kind=kind)
                for key in ('name','subtype','is_enabled'):setattr(row,key,form.cleaned_data[key])
                row.vendor='' if kind=='servers' else form.cleaned_data['vendor']
                row.version_match=form.cleaned_data.get('version_match', '')
                row.parent=form.cleaned_data['parent']
                row.settings=value;row.full_clean();row.save();template=row;saved=True
                form.fields['version'].initial=row.updated_at.isoformat()
                form.data=form.data.copy();form.data['version']=row.updated_at.isoformat()
        except (ValidationError,IntegrityError) as exc:
            form.add_error(None,'；'.join(exc.messages) if isinstance(exc,ValidationError) else '该适用范围已有模板，请修改原模板。')
    from index.common.form_examples import apply_field_examples
    from net.data_exchange.inventory_guidance import SNMP_EXAMPLES
    apply_field_examples(form, {
        'name': ('Linux 基础巡检' if kind == 'servers' else '门禁基础巡检' if kind == 'monitors' else '华为交换机巡检', '按用途命名模板。'),
        'version_match': ('V200R019', '匹配设备版本中的关键字，留空不按版本细分。'),
        **{key: SNMP_EXAMPLES[key] for key in SECRET_FIELDS},
    })
    _describe_inheritance(form)
    return render(request,'devices/collection_settings.html',{'inherited_json':json.dumps(form.inherited_settings,ensure_ascii=False,indent=2),'form':form,'kind':kind,'title':PROJECT_LABELS[kind]+'模板','templates':DeviceCollectionTemplate.objects.filter(kind=kind).select_related('parent').order_by('vendor','subtype','version_match'),'template':template,'saved':saved,'template_mode':True,'api_mode':api_mode,'snmp_fields':[form[k] for k in ('protocol',*SNMP_FIELDS) if k in form.fields],'advanced_fields':[form[k] for k in ('snmp_oids','mib_modules','mib_file') if k in form.fields]})

@admin_required
def device_collection_settings(request,kind,pk):
    _kind(kind);asset=get_object_or_404(MODELS[kind],pk=pk)
    binding=DeviceCollectionBinding.objects.filter(kind=kind,target_id=pk).first()
    api_mode=kind == 'networks' and getattr(asset, 'connection_type', '') == 'sangfor_api'
    form=_bind_form(kind,request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None,binding.overrides if binding else {},binding.updated_at.isoformat() if binding else '',api_mode=api_mode)
    form.fields['template']=forms.ModelChoiceField(label='继承模板',queryset=DeviceCollectionTemplate.objects.filter(kind=kind,is_enabled=True),required=False,initial=binding.template_id if binding else None,empty_label='自动按操作系统匹配' if kind=='servers' else '自动按厂商、设备类型和系统版本匹配' if kind=='networks' else '自动按厂商和设备类型匹配')
    form.fields['template'].widget.attrs['class']='form-select'
    selected_id=request.POST.get('template') if request.method=='POST' else (binding.template_id if binding else None)
    try:selected=DeviceCollectionTemplate.objects.filter(kind=kind,is_enabled=True,pk=selected_id).first() if selected_id else None
    except (ValidationError,ValueError):selected=None
    form.inherited_settings=template_settings(selected) if selected else resolve_collection_settings(kind,asset,bindings={})
    if kind=='monitors':
        for key in SECRET_FIELDS:form.fields[key]=forms.CharField(label={'snmp_community':'SNMP Community','snmp_auth_password':'SNMPv3 认证密码','snmp_priv_password':'SNMPv3 加密密码'}[key],required=False,widget=forms.PasswordInput(attrs={'class':'form-control','autocomplete':'new-password'}),help_text='留空保留已有凭据；凭据加密保存。')
    saved=False
    if request.method=='POST' and form.is_valid() and request.POST.get('preview_inheritance'):
        pass
    elif request.method=='POST' and form.is_valid() and request.POST.get('preview_item'):
        try:_preview_template(form,request.POST['preview_item'])
        except ValidationError as exc:form.add_error(None,'；'.join(exc.messages))
    elif request.method=='POST' and form.is_valid():
        try:
            value=form.settings_value()
            with transaction.atomic():
                row=DeviceCollectionBinding.objects.select_for_update().filter(kind=kind,target_id=pk).first()
                if row and row.updated_at.isoformat()!=form.cleaned_data['version']:raise ValidationError('设备设置已被修改，请刷新后重试。')
                row=row or DeviceCollectionBinding(kind=kind,target_id=pk)
                credentials=decrypt_credentials(row)
                for key in SECRET_FIELDS:
                    if form.cleaned_data.get(key):credentials[key]=form.cleaned_data[key]
                if credentials and kind=='monitors':
                    from types import SimpleNamespace
                    selected_template=form.cleaned_data.get('template')
                    inherited=form.inherited_settings.get('snmp',{})
                    inherited={**inherited, **(selected_template.settings.get('snmp',{}) if selected_template else {})}
                    snmp={**inherited, **value.get('snmp',{}), **credentials}
                    candidate=Network_Device(connection_type='snmp',**snmp)
                    candidate.clean()
                row.template=form.cleaned_data['template'];row.overrides=value
                if any(form.cleaned_data.get(k) for k in SECRET_FIELDS):row.encrypted_credentials=encrypt_credentials(credentials)
                row.full_clean();row.save();binding=row;saved=True
                form.data=form.data.copy();form.data['version']=row.updated_at.isoformat()
                for key in SECRET_FIELDS:form.data.pop(key,None)
        except (ValidationError,IntegrityError) as exc:
            form.add_error(None,'；'.join(exc.messages) if isinstance(exc,ValidationError) else '设备设置保存冲突，请刷新后重试。')
    from index.common.form_examples import apply_field_examples
    from net.data_exchange.inventory_guidance import SNMP_EXAMPLES
    apply_field_examples(form, {
        'name': ('Linux 基础巡检' if kind == 'servers' else '门禁基础巡检' if kind == 'monitors' else '华为交换机巡检', '按用途命名模板。'),
        'version_match': ('V200R019', '匹配设备版本中的关键字，留空不按版本细分。'),
        **{key: SNMP_EXAMPLES[key] for key in SECRET_FIELDS},
    })
    _describe_inheritance(form)
    effective=resolve_collection_settings(kind,asset)
    effective.pop('_credentials_identity',None)
    return render(request,'devices/collection_settings.html',{'form':form,'kind':kind,'asset':asset,'version_match_info':effective.get('_version_match',{}),'title':str(asset)+' · 巡检设置','saved':saved,'api_mode':api_mode,'effective_json':json.dumps(effective,ensure_ascii=False,indent=2),'advanced_fields':[form[k] for k in ('snmp_oids','mib_modules','mib_file') if k in form.fields],'snmp_fields':[form[k] for k in ('protocol',*SNMP_FIELDS,*SECRET_FIELDS) if k in form.fields]})


def _describe_inheritance(form):
    inherited=getattr(form,'inherited_settings',{})
    for row in form.item_rows:
        key=row['key']; parts=[]
        for section, label in [('item_methods','方式'),('commands','命令'),('parsers','解析'),('thresholds','阈值'),('severity_overrides','异常级别'),('item_enabled','适用性')]:
            value=inherited.get(section,{}).get(key)
            if value is not None:
                source=inherited.get('_rule_sources',{}).get(section+'.'+key,'父模板')
                if section=='parsers':value=value.get('engine','内置解析')
                elif section=='commands':value=str(len(value))+' 条命令'
                elif section=='item_enabled':value='适用' if value else '不适用'
                parts.append(label+'：'+str(value)+'（'+source+'）')
        row['inherited']='；'.join(parts) or '未设置父级规则，使用内置默认值。'
