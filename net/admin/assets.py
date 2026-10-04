"""Admin registrations for editable inventory and directory configuration."""

from django import forms
from django.db.models import Q
from net.secret_masks import MASKED_SECRET, MaskedSecretInput
from django.contrib import admin

from net.models.topology import (
    NetworkTopologyInterface,
    NetworkTopologyLink,
    NetworkTopologyObservation,
    TopologyDiscoveryBatch,
)

from net.models import Computer, Domain_Account, Domain_Computer, Domain_Group, Domain_Controller_Config, Network_Device, People, WeakCurrentDevice, Server



class DomainControllerConfigAdminForm(forms.ModelForm):
    """Treat the bind password as write-only while retaining a saved value."""

    class Meta:
        model = Domain_Controller_Config
        fields = '__all__'
        widgets = {'bind_password': MaskedSecretInput(attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        password = self.fields['bind_password']
        password.required = False
        password.widget = MaskedSecretInput(
            attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'},
        )
        self.initial['bind_password'] = (
            MASKED_SECRET if self.instance.pk and self.instance.bind_password else ''
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('bind_password') in ('', MASKED_SECRET) and self.instance.pk and self.instance.bind_password:
            cleaned['bind_password'] = self.instance.bind_password
        return cleaned


class SecretPreservingModelForm(forms.ModelForm):
    """Render credentials write-only and retain them on a blank change form."""

    secret_fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing = bool(self.instance and self.instance.pk)
        for name in self.secret_fields:
            field = self.fields[name]
            field.widget = MaskedSecretInput(
                attrs={'autocomplete': 'new-password', 'data-secret-mask': 'true'},
            )
            self.initial[name] = (
                MASKED_SECRET if existing and getattr(self.instance, name, '') else ''
            )
            if existing:
                field.required = False

    def clean(self):
        cleaned = super().clean()
        if self.instance and self.instance.pk:
            for name in self.secret_fields:
                if cleaned.get(name) in ('', MASKED_SECRET):
                    cleaned[name] = getattr(self.instance, name)
        return cleaned


class NetworkDeviceAdminForm(SecretPreservingModelForm):
    secret_fields = (
        'password', 'snmp_community', 'snmp_auth_password',
        'snmp_priv_password', 'api_shared_secret',
    )

    class Meta:
        model = Network_Device
        fields = '__all__'


class ServerAdminForm(SecretPreservingModelForm):
    secret_fields = ('password', 'api_token')

    class Meta:
        model = Server
        fields = '__all__'


class WeakCurrentAdminForm(SecretPreservingModelForm):
    secret_fields = ('api_password', 'api_token')

    class Meta:
        model = WeakCurrentDevice
        fields = '__all__'


@admin.register(People)
class PeopleAdmin(admin.ModelAdmin):
    list_display = ('name', 'employee_id', 'phone', 'department', 'leader', 'is_active', 'source', 'last_synced_at')
    list_filter = ('is_active', 'source', 'department')
    search_fields = ('name', 'employee_id', 'email', 'phone', 'department', 'leader', 'platform_user_id')
    raw_id_fields = ('sync_source',)
    date_hierarchy = 'last_synced_at'


@admin.register(Domain_Account)
class DomainAccountAdmin(admin.ModelAdmin):
    list_display = ('login_name', 'account_name', 'is_active', 'ou', 'last_login_date')
    list_filter = ('is_active', 'ou')
    search_fields = ('login_name', 'account_name', 'object_guid', 'distinguished_name', 'ou')
    date_hierarchy = 'last_login_date'


@admin.register(Domain_Computer)
class DomainComputerAdmin(admin.ModelAdmin):
    list_display = ('computer_name', 'is_active', 'os', 'ou', 'last_login_date')
    list_filter = ('is_active', 'os', 'ou')
    search_fields = ('computer_name', 'object_guid', 'distinguished_name', 'os', 'ou')
    date_hierarchy = 'last_login_date'


@admin.register(Domain_Group)
class DomainGroupAdmin(admin.ModelAdmin):
    list_display = ('group_name', 'login_name', 'group_category', 'group_scope', 'member_count', 'ou')
    list_filter = ('group_category', 'group_scope', 'ou')
    search_fields = ('group_name', 'login_name', 'object_guid', 'distinguished_name', 'description', 'ou')


@admin.register(Computer)
class ComputerAdmin(admin.ModelAdmin):
    list_display = ('computer_name', 'is_active', 'user_name', 'os', 'ip_addresses', 'last_report_at')
    list_filter = ('is_active', 'os')
    search_fields = ('computer_name', 'user_name', 'login_account', 'ip_addresses', 'mac_addresses', 'serial_number')
    date_hierarchy = 'last_report_at'


class DeleteSelectedMixin:
    """Let the built-in bulk delete work on devices that topology still references.

    NetworkTopologyInterface.device, NetworkTopologyObservation.local_device and
    TopologyDiscoveryBatch.device are all PROTECT, so Django's stock action would
    abort on any device that has ever been discovered. Discovery data belongs to
    the device, so drop it in dependency order first; inspection records are
    already CASCADE and go with the device.
    """

    actions = ('delete_selected',)

    def delete_queryset(self, request, queryset):
        device_ids = list(queryset.values_list('pk', flat=True))
        interface_ids = list(
            NetworkTopologyInterface.objects
            .filter(device_id__in=device_ids)
            .values_list('pk', flat=True)
        )
        NetworkTopologyObservation.objects.filter(
            Q(local_device_id__in=device_ids) | Q(local_interface_id__in=interface_ids)
        ).delete()
        NetworkTopologyLink.objects.filter(
            Q(local_interface_id__in=interface_ids) | Q(remote_interface_id__in=interface_ids)
        ).delete()
        NetworkTopologyInterface.objects.filter(pk__in=interface_ids).delete()
        TopologyDiscoveryBatch.objects.filter(device_id__in=device_ids).delete()
        super().delete_queryset(request, queryset)


@admin.register(Network_Device)
class NetworkDeviceAdmin(DeleteSelectedMixin, admin.ModelAdmin):
    form = NetworkDeviceAdminForm
    list_display = (
        'device_name', 'ip', 'device_type', 'vendor', 'model',
        'connection_type', 'snmp_version', 'snmp_port', 'port', 'is_enabled',
    )
    list_filter = ('device_type', 'vendor', 'connection_type', 'snmp_version', 'is_enabled')
    search_fields = ('device_name', 'ip', 'device_type', 'vendor', 'model', 'connection_type')


@admin.register(Server)
class ServerAdmin(DeleteSelectedMixin, admin.ModelAdmin):
    form = ServerAdminForm
    list_display = ('name', 'ip', 'server_type', 'os', 'port', 'verify_ssl', 'is_enabled')
    list_filter = ('server_type', 'os', 'verify_ssl', 'is_enabled')
    search_fields = ('name', 'ip', 'server_type', 'os')


@admin.register(WeakCurrentDevice)
class WeakCurrentAdmin(DeleteSelectedMixin, admin.ModelAdmin):
    form = WeakCurrentAdminForm
    list_display = ('device_name', 'ip', 'device_type', 'vendor', 'model', 'verify_ssl', 'is_enabled')
    list_filter = ('device_type', 'vendor', 'verify_ssl', 'is_enabled')
    search_fields = ('device_name', 'ip', 'device_type', 'vendor', 'model')
    actions = ('delete_selected', 'mark_disabled', 'mark_enabled')

    @admin.action(description='停用选中的弱电设备')
    def mark_disabled(self, request, queryset):
        queryset.update(is_enabled=False)

    @admin.action(description='启用选中的弱电设备')
    def mark_enabled(self, request, queryset):
        queryset.update(is_enabled=True)


@admin.register(Domain_Controller_Config)
class DomainControllerConfigAdmin(admin.ModelAdmin):
    form = DomainControllerConfigAdminForm
    list_display = ('name', 'host', 'port', 'use_ssl', 'base_dn', 'updated_at')
    list_filter = ('use_ssl',)
    search_fields = ('name', 'host', 'base_dn')
    readonly_fields = ('updated_at',)
