"""Executors used exclusively by the background task Worker."""

from .computer_analysis import execute_computer_target, persist_computer_execution_failure
from .inspection import ExecutionOutcome, execute_target

__all__ = [
    'ExecutionOutcome', 'execute_target', 'execute_computer_target',
    'persist_computer_execution_failure',
]
