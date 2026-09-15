"""Forms used by the permission-protected domain management workspace."""

from django import forms

from index.domain.connection_form import DomainControllerConfigForm as _BaseDomainControllerConfigForm
from net.secret_masks import MASKED_SECRET, MaskedSecretInput


class DomainControllerConfigForm(_BaseDomainControllerConfigForm):
    """Do not expose the saved bind password in HTML or validation responses."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        password_field = self.fields['bind_password']
        password_field.required = False
        password_field.widget = MaskedSecretInput(attrs={
            'autocomplete': 'new-password',
            'class': 'form-control',
            'data-secret-mask': 'true',
        })
        if not self.is_bound:
            self.initial['bind_password'] = (
                MASKED_SECRET if self.instance and self.instance.pk and self.instance.bind_password else ''
            )

    def clean(self):
        # Bypass the base form's required-password check so an existing
        # password can be retained when the privileged operator leaves it
        # blank.  PasswordInput(render_value=False) keeps it out of HTML.
        cleaned = forms.ModelForm.clean(self)
        required = ['host', 'base_dn', 'bind_username']
        for name in required:
            if not cleaned.get(name):
                self.add_error(name, '该字段不能为空。')

        password = cleaned.get('bind_password')
        if not password or password == MASKED_SECRET:
            if self.instance and self.instance.pk and self.instance.bind_password:
                cleaned['bind_password'] = self.instance.bind_password
            else:
                self.add_error('bind_password', '该字段不能为空。')
        if cleaned.get('use_ssl') and cleaned.get('port') == 389:
            self.add_error('port', '389 端口不能使用隐式 LDAPS；请改为 636，或取消“使用 LDAPS”。')
        return cleaned


class DomainOperationForm(forms.Form):
    """Normalize bulk operation input before it reaches the queue service."""

    object_type = forms.ChoiceField(
        choices=(('account', '域账号'), ('computer', '域计算机')),
        widget=forms.HiddenInput,
    )
    action = forms.CharField(widget=forms.HiddenInput)
    target_ids = forms.MultipleChoiceField(
        required=False,
        widget=forms.MultipleHiddenInput,
    )
    destination_dn = forms.ChoiceField(required=False)
    group_dn = forms.ChoiceField(required=False)
    enabled = forms.BooleanField(required=False)
    password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password'},
        ),
    )
    user_dn = forms.CharField(required=False, max_length=500)
    login_name = forms.CharField(required=False, max_length=256)
    display_name = forms.CharField(required=False, max_length=256)
    initial_password = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
    )
    initial_password_confirm = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, target_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        from net.models import Domain_Controller_Config, DomainOU, Domain_Group
        from net.domain.memberships import directory_choices
        config = Domain_Controller_Config.objects.filter(pk=1).first()
        base_dn = config.base_dn if config else ''
        self.fields['destination_dn'].choices = [('', '请选择 OU')] + directory_choices(DomainOU, base_dn)
        self.fields['group_dn'].choices = [('', '请选择安全组')] + directory_choices(Domain_Group, base_dn, security_only=True)
        self.fields['target_ids'].choices = [
            (str(value), str(value)) for value in target_choices
        ]

    def clean_target_ids(self):
        values = [str(value) for value in self.cleaned_data['target_ids']]
        if len(values) != len(set(values)):
            raise forms.ValidationError('请选择至少一个目标。')
        return values

    def clean(self):
        cleaned = super().clean()
        parameter = {'move_ou': 'destination_dn', 'add_group': 'group_dn'}.get(cleaned.get('action'))
        if parameter and not cleaned.get(parameter):
            self.add_error(parameter, '请先同步目录并选择目标。')
        if cleaned.get('action') == 'remove_group':
            self.add_error('action', '请从分组成员窗口执行移出。')
        if cleaned.get('action') != 'create_user':
            if not cleaned.get('target_ids'):
                self.add_error('target_ids', '请选择至少一个目标。')
            return cleaned
        if cleaned.get('object_type') != 'account':
            self.add_error('action', '新增用户仅适用于域账号。')
        if cleaned.get('target_ids'):
            self.add_error('target_ids', '新增用户不应选择既有账号。')
        for field in ('user_dn', 'login_name', 'display_name', 'initial_password', 'initial_password_confirm'):
            if not cleaned.get(field):
                self.add_error(field, '该字段不能为空。')
        if (
            cleaned.get('initial_password')
            and cleaned.get('initial_password_confirm')
            and cleaned['initial_password'] != cleaned['initial_password_confirm']
        ):
            self.add_error('initial_password_confirm', '两次输入的初始密码不一致。')
        return cleaned

    def operation_parameters(self):
        cleaned = self.cleaned_data
        action = cleaned['action']
        if action == 'create_user':
            return {
                'user_dn': cleaned.get('user_dn', '').strip(),
                'login_name': cleaned.get('login_name', '').strip(),
                'display_name': cleaned.get('display_name', '').strip(),
            }
        if action == 'move_ou':
            return {'destination_dn': cleaned.get('destination_dn', '').strip()}
        if action == 'add_group':
            return {'group_dn': cleaned.get('group_dn', '').strip()}
        if action in {'must_change_password', 'password_never_expires'}:
            return {'enabled': cleaned.get('enabled', False)}
        return {}

    def operation_password(self):
        return (
            self.cleaned_data.get('initial_password')
            if self.cleaned_data.get('action') == 'create_user'
            else self.cleaned_data.get('password')
        )


class DomainAccountImportForm(forms.Form):
    file = forms.FileField()
    initial_password = forms.CharField(strip=False, widget=forms.PasswordInput())
    initial_password_confirm = forms.CharField(strip=False, widget=forms.PasswordInput())

    def clean_file(self):
        upload = self.cleaned_data['file']
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError('导入文件不能超过 5 MB。')
        if not str(upload.name or '').casefold().endswith(('.csv', '.xlsx')):
            raise forms.ValidationError('仅支持 CSV 或 Excel (.xlsx) 文件。')
        return upload

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get('initial_password')
            and cleaned.get('initial_password_confirm')
            and cleaned['initial_password'] != cleaned['initial_password_confirm']
        ):
            self.add_error('initial_password_confirm', '两次输入的初始密码不一致。')
        return cleaned
