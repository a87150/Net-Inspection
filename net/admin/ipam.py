"""Admin entry for the IP address plan."""
from django.contrib import admin

from net.models import IpAddress, IpSubnet


class IpAddressInline(admin.TabularInline):
    model = IpAddress
    extra = 0
    fields = ("address", "status", "hostname", "mac", "owner", "note")


@admin.register(IpSubnet)
class IpSubnetAdmin(admin.ModelAdmin):
    list_display = ("cidr", "purpose", "vlan", "gateway", "pool_range", "address_count")
    list_filter = ("purpose",)
    search_fields = ("cidr", "vlan", "note")
    inlines = [IpAddressInline]

    @admin.display(description="地址池")
    def pool_range(self, obj):
        if not (obj.pool_start and obj.pool_end):
            return "—"
        return "%s - %s（%d 个）" % (obj.pool_start, obj.pool_end, obj.pool_size)

    @admin.display(description="登记数")
    def address_count(self, obj):
        return obj.addresses.count()


@admin.register(IpAddress)
class IpAddressAdmin(admin.ModelAdmin):
    list_display = ("address", "subnet", "status", "hostname", "mac", "owner")
    list_filter = ("status", "subnet")
    search_fields = ("address", "hostname", "mac", "owner")
