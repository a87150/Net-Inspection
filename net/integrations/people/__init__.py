"""Normalized personnel directory integration contract."""

from .base import (
    DirectoryAdapter,
    DirectoryAdapterError,
    DirectoryAuthenticationError,
    DirectoryPayloadError,
    DirectoryPerson,
    DirectoryRateLimitError,
)

__all__ = [
    'DirectoryAdapter',
    'DirectoryAdapterError',
    'DirectoryAuthenticationError',
    'DirectoryPayloadError',
    'DirectoryPerson',
    'DirectoryRateLimitError',
]
