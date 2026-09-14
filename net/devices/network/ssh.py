"""Network-device collection and configuration capture through SSH."""

from net.infrastructure.ssh_collectors import (
    NETWORK_COMMANDS,
    PAGING_COMMANDS,
    collect_network_ssh,
)

__all__ = ['NETWORK_COMMANDS', 'PAGING_COMMANDS', 'collect_network_ssh']
