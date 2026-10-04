"""Shared retention predicate.

Both the PC retention and the infrastructure inspection retention must never delete a
row whose alerts are still being processed, or a pending alert loses the record it is
about. The predicate lives here rather than in either caller so the rule is defined
once.
"""
from django.db.models import Q


def alert_processing_complete(query):
    """Rows whose task target and task summary alert processing have both finished."""
    return query.filter(
        Q(task_target__isnull=True)
        | Q(
            task_target__alert_processed_at__isnull=False,
            task_target__task__alert_summary_processed_at__isnull=False,
        )
    )
