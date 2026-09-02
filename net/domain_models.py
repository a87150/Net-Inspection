"""Compatibility exports for historical domain model and migration imports."""

from net.models.domain import (
    DomainOperation, DomainOperationSecret, SENSITIVE_PAYLOAD_KEYS,
    contains_sensitive_payload, validate_parameter_summary,
)

__all__ = [
    'DomainOperation', 'DomainOperationSecret', 'SENSITIVE_PAYLOAD_KEYS',
    'contains_sensitive_payload', 'validate_parameter_summary',
]
