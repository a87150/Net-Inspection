from django import forms
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.views.decorators.clickjacking import xframe_options_sameorigin
from net.models import IssueSeverityPolicy
from net.inspections.issues import PROJECT_RULES, PROJECT_LABELS, METRIC_DEFAULTS, PC_THRESHOLD_FIELDS
from net.devices.pc.severity import LEVELS


class SeveritySettingsForm(forms.Form):
    updated_at = forms.DateTimeField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, overrides=None, project='computers', thresholds=None, updated_at=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy_version = updated_at
        self.fields['updated_at'].initial = updated_at.isoformat() if updated_at else ''
        self.rules = PROJECT_RULES[project]
        self.project = project
        self.saved_thresholds = thresholds or {}
        self.threshold_keys = set(PC_THRESHOLD_FIELDS if project == 'computers' else METRIC_DEFAULTS.get(project, {}))
        self.rule_rows = []
        for key, (category, label) in self.rules.items():
            for prefix in ('rule', 'missing'):
                field_name = f'{prefix}_{key}'
                rule_key = key if prefix == 'rule' else f'missing.{key}'
                self.fields[field_name] = forms.ChoiceField(
                    label=f'{label} {"问题等级" if prefix == "rule" else "数据不足等级"}',
                    required=False, choices=[('', '系统默认'), *LEVELS.items()],
                    initial=(overrides or {}).get(rule_key, ''), widget=forms.Select(attrs={'class': 'form-select'}))
            thresholds_for_row = []
            specs = ([(name, title, maximum) for name, (rule, title, maximum) in PC_THRESHOLD_FIELDS.items() if rule == key]
                     if project == 'computers' else [(key, '温度（℃）' if key == 'temperature' else f'{label}（%）',
                                                      200 if key == 'temperature' else 100)]
                     if key in METRIC_DEFAULTS.get(project, {}) else [])
            for threshold_key, title, maximum in specs:
                name = f'threshold_{threshold_key}'
                is_pc = project == 'computers'
                field_type = forms.IntegerField if is_pc else forms.FloatField
                self.fields[name] = field_type(required=False, min_value=1 if is_pc else 0.01,
                    max_value=maximum, label=title,
                    initial=self.saved_thresholds.get(threshold_key, None if is_pc else METRIC_DEFAULTS[project][threshold_key]),
                    widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '1' if is_pc else 'any',
                                                   'placeholder': '使用分析配置' if is_pc else '系统默认'}))
                thresholds_for_row.append(self[name])
            self.rule_rows.append((category, label, self[f'rule_{key}'], self[f'missing_{key}'], thresholds_for_row))

    def clean(self):
        cleaned = super().clean()
        if 'updated_at' in self.data and 'updated_at' in cleaned and cleaned['updated_at'] != self.policy_version:
            raise forms.ValidationError('设置已被其他请求修改，请刷新后重试。')
        unknown = [name for name in self.data if name.startswith(('rule_', 'missing_', 'threshold_')) and name not in self.fields]
        if unknown:
            raise forms.ValidationError('提交了不属于当前项目的规则。')
        return cleaned

    def thresholds(self):
        return {key: self.cleaned_data.get(f'threshold_{key}') if f'threshold_{key}' in self.data else value
                for key, value in self.saved_thresholds.items()
                if f'threshold_{key}' not in self.data or self.cleaned_data.get(f'threshold_{key}') is not None} | {
                    key: self.cleaned_data[f'threshold_{key}'] for key in self.threshold_keys
                    if self.cleaned_data.get(f'threshold_{key}') is not None}

    def overrides(self):
        return {key if prefix == 'rule' else f'missing.{key}': self.cleaned_data[f'{prefix}_{key}']
                for key in self.rules for prefix in ('rule', 'missing') if self.cleaned_data[f'{prefix}_{key}']}


@require_http_methods(['GET', 'POST'])
@xframe_options_sameorigin
def issue_severity_settings(request):
    if not request.user.is_authenticated or not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied
    project = request.GET.get('project', 'computers')
    if project not in PROJECT_RULES:
        raise Http404('未知的项目')
    policy = IssueSeverityPolicy.objects.filter(project=project).first()
    form = SeveritySettingsForm(request.POST if request.method == 'POST' else None,
                                updated_at=policy.updated_at if policy else None,
                                project=project, thresholds=policy.thresholds if policy else {},
                                overrides=policy.overrides if policy else {})
    saved = False
    if request.method == 'POST' and form.is_valid():
        candidate = IssueSeverityPolicy(project=project, overrides=form.overrides(), thresholds=form.thresholds())
        candidate.full_clean(validate_unique=False, validate_constraints=False)
        if policy is not None:
            candidate.updated_at = timezone.now()
            saved = bool(IssueSeverityPolicy.objects.filter(pk=policy.pk, updated_at=policy.updated_at).update(
                overrides=candidate.overrides, thresholds=candidate.thresholds, updated_at=candidate.updated_at))
        else:
            try:
                with transaction.atomic():
                    candidate.save(force_insert=True)
                saved = True
            except IntegrityError:
                if not IssueSeverityPolicy.objects.filter(project=project).exists():
                    raise
        if not saved:
            form.add_error(None, '设置已被其他请求修改，请刷新后重试。')
        else:
            form = SeveritySettingsForm(project=project, overrides=candidate.overrides,
                                        thresholds=candidate.thresholds, updated_at=candidate.updated_at)
    return render(request, 'inspections/issue_settings.html', {'form': form, 'saved': saved,
                  'project': project, 'project_label': PROJECT_LABELS[project]},
                  status=400 if request.method == 'POST' and not saved else 200)
