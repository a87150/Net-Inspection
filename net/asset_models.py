"""Compatibility exports for historical asset model imports."""

from net.models.devices import Computer, Monitor, Network_Device, Server
from net.models.domain import Domain_Account, Domain_Computer, Domain_Controller_Config
from net.models.people import People

__all__ = [
    'People', 'Domain_Account', 'Domain_Computer', 'Computer', 'Network_Device',
    'Server', 'Monitor', 'Domain_Controller_Config',
]
