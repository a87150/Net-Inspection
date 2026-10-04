"""The one button set every asset workspace renders.

Asset lists and their inspection/analysis record pages show the same actions
for the same kind; only the back-link differs, because a record page needs a
way back to the list it belongs to.
"""


def workspace_action_context(kind, *, back_url=''):
    """Flags the shared action-bar template needs for this asset kind."""
    from index.devices.forms import DEVICE_KINDS
    from net.data_exchange.inventory_csv import IMPORTABLE_ENTITIES
    return {
        'action_kind': kind,
        'action_can_manage_devices': kind in DEVICE_KINDS,
        'action_can_import': kind in IMPORTABLE_ENTITIES,
        'action_issue_settings': kind == 'computers',
        'action_script_download': kind == 'servers',
        'action_back_url': back_url,
    }
