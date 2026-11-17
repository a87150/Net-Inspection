"""IP address plan: subnets, DHCP pools, and what inspections actually saw.

A subnet is the unit of truth here. An address row is either registered by a
human or learned from ARP evidence; conflicts compare the two instead of
trusting either side alone.
"""
import ipaddress
import uuid

from django.core.exceptions import ValidationError
from django.db import models


def normalize_cidr(value):
    """Store the network address, not whatever host bits the form received."""
    try:
        return str(ipaddress.ip_network(str(value).strip(), strict=False))
    except ValueError as error:
        raise ValidationError("网段必须写成 CIDR，例如 10.1.1.0/24。") from error


class IpSubnet(models.Model):
    class Purpose(models.TextChoices):
        OFFICE = "office", "办公"
        MANAGEMENT = "management", "管理"
        SERVER = "server", "服务器"
        WIRELESS = "wireless", "无线"
        OTHER = "other", "其他"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cidr = models.CharField(max_length=43, unique=True)
    gateway = models.GenericIPAddressField(null=True, blank=True)
    vlan = models.CharField(max_length=32, blank=True, default="")
    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.OFFICE)
    pool_start = models.GenericIPAddressField(null=True, blank=True)
    pool_end = models.GenericIPAddressField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cidr"]
        verbose_name = "IP 网段"
        verbose_name_plural = "IP 网段"

    def __str__(self):
        return self.cidr

    def clean(self):
        if self.cidr:
            self.cidr = normalize_cidr(self.cidr)
        if self.pool_start and self.pool_end:
            if ipaddress.ip_address(self.pool_start) > ipaddress.ip_address(self.pool_end):
                raise ValidationError({"pool_end": "地址池结束必须大于等于开始。"})
        elif self.pool_start or self.pool_end:
            raise ValidationError({"pool_start": "地址池的开始和结束要一起填。"})

    @property
    def network(self):
        return ipaddress.ip_network(self.cidr, strict=False)

    @property
    def usable_hosts(self):
        network = self.network
        return network.num_addresses - 2 if network.num_addresses > 2 else network.num_addresses

    @property
    def pool_size(self):
        if not (self.pool_start and self.pool_end):
            return 0
        return int(ipaddress.ip_address(self.pool_end)) - int(ipaddress.ip_address(self.pool_start)) + 1

    def contains(self, address):
        try:
            return ipaddress.ip_address(str(address)) in self.network
        except ValueError:
            return False

    def in_pool(self, address):
        if not (self.pool_start and self.pool_end):
            return False
        try:
            return ipaddress.ip_address(self.pool_start) <= ipaddress.ip_address(str(address)) <= ipaddress.ip_address(self.pool_end)
        except ValueError:
            return False


class IpAddress(models.Model):
    class Status(models.TextChoices):
        FREE = "free", "空闲"
        RESERVED = "reserved", "保留"
        USED = "used", "已用"
        DHCP = "dhcp", "DHCP 动态"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subnet = models.ForeignKey(IpSubnet, on_delete=models.CASCADE, related_name="addresses")
    address = models.GenericIPAddressField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.USED)
    hostname = models.CharField(max_length=120, blank=True, default="")
    mac = models.CharField(max_length=32, blank=True, default="")
    owner = models.CharField(max_length=64, blank=True, default="")
    device_id = models.UUIDField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["address"]
        verbose_name = "IP 登记"
        verbose_name_plural = "IP 登记"
        constraints = [
            models.UniqueConstraint(fields=["subnet", "address"], name="net_ip_subnet_addr_uk"),
        ]
        indexes = [models.Index(fields=["address"], name="net_ip_address_idx")]

    def __str__(self):
        return self.address
