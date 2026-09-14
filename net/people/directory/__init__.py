"""Personnel directory provider contracts and synchronization."""

from .base import (
    DirectoryAdapter,
    DirectoryAdapterError,
    DirectoryAuthenticationError,
    DirectoryPayloadError,
    DirectoryPerson,
    DirectoryRateLimitError,
    DirectorySourceSnapshot,
    directory_source_configuration_identity,
    freeze_directory_source,
)
from .sync import (
    PeopleSyncApplyError,
    PeopleSyncError,
    PeopleSyncPreviewError,
    SyncPreview,
    SyncResult,
    apply_people_sync,
    preview_people_sync,
)

__all__ = [
    'DirectoryAdapter', 'DirectoryAdapterError', 'DirectoryAuthenticationError',
    'DirectoryPayloadError', 'DirectoryPerson', 'DirectoryRateLimitError',
    'DirectorySourceSnapshot', 'directory_source_configuration_identity',
    'freeze_directory_source', 'PeopleSyncApplyError', 'PeopleSyncError',
    'PeopleSyncPreviewError', 'SyncPreview', 'SyncResult', 'apply_people_sync',
    'preview_people_sync',
]
