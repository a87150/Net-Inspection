"""Bounded retention: durable archive, then optional guarded snapshot compaction."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Exists, OuterRef, PROTECT, RESTRICT
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from net.models import TaskRun, TaskTargetRun


def encoded(value):
    return (json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


def protected_references(instance):
    """Explain direct incoming deletion blockers, including hidden reverse links."""
    refs = []
    for relation in instance._meta.get_fields(include_hidden=True):
        if not relation.auto_created or relation.concrete or not (relation.one_to_many or relation.one_to_one):
            continue
        field = relation.field
        if field.remote_field.on_delete not in (PROTECT, RESTRICT):
            continue
        count = relation.related_model._base_manager.using(instance._state.db).filter(
            **{field.attname: instance.pk},
        ).count()
        if count:
            refs.append({'owner': instance._meta.label, 'model': relation.related_model._meta.label,
                         'field': field.name, 'count': count})
    return refs


def fields(instance):
    values = {field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields}
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()}


def compaction_plan(target, *, lock=False):
    from net.models import ComputerAnalysis, Network_Device_Inspection, Server_Inspection, Monitor_Inspection
    from net.inspections.result_storage import compact_result_snapshot

    original = target.result_snapshot
    if not isinstance(original, dict) or original.get('snapshot_version') == 2:
        return None, 'Already compact or unsupported snapshot.'
    if 'snapshot_version' in original:
        return None, 'Unknown explicit snapshot version.'
    model = {'computer_analysis': ComputerAnalysis, 'network_device_inspection': Network_Device_Inspection,
             'server_inspection': Server_Inspection, 'monitor_inspection': Monitor_Inspection}.get(target.result_type)
    if model is None:
        return None, 'No supported immutable evidence type.'
    records = model.objects.select_for_update() if lock else model.objects
    try:
        record = records.filter(pk=target.result_id, task_target_id=target.pk).first()
    except (ValidationError, ValueError):
        record = None
    if record is None:
        return None, 'Referenced evidence missing or belongs to another target.'
    if 'details' not in original or original['details'] != record.details:
        return None, 'Snapshot details do not exactly match the referenced evidence.'
    if any(key in original and original[key] != value for key, value in (
        ('result_type', target.result_type), ('result_id', str(record.pk)),
    )):
        return None, 'Snapshot reference conflicts with the target reference.'
    compact = compact_result_snapshot(record, result_type=target.result_type)
    # Legacy readers distinguish missing status/health from values inferred by
    # the helper. Add only reference metadata; preserve absence of all markers.
    compact = {key: value for key, value in compact.items()
               if key in original or key in {'snapshot_version', 'result_type', 'result_id'}}
    # Only the duplicated details are removed. Original status/summary/health and
    # every other marker override helper defaults, even when empty or null.
    compact.update({key: value for key, value in original.items() if key != 'details'})
    if len(encoded(compact)) >= len(encoded(original)):
        return None, 'Compaction would not reduce snapshot size.'
    return compact, None


def compact_archived_target(row, cutoff):
    """Compare-and-swap only data already present in the synced archive."""
    data = row['target']
    with transaction.atomic():
        target = TaskTargetRun.objects.filter(pk=data['id']).first()
        if target is None:
            return False
        if any(getattr(target, key) != data[key] for key in ('result_snapshot', 'result_type', 'result_id')):
            return False
        compact, reason = compaction_plan(target, lock=True)
        if reason:
            return False
        unfinished = TaskTargetRun.objects.filter(task_id=OuterRef('task_id')).exclude(
            status__in=TaskRun.TERMINAL_STATUSES,
        )
        return bool(TaskTargetRun.objects.filter(
            pk=target.pk, task_id=data['task_id'], status=data['status'], status__in=TaskRun.TERMINAL_STATUSES,
            finished_at=data['finished_at'], finished_at__lt=cutoff,
            task__status__in=TaskRun.TERMINAL_STATUSES, task__finished_at__lt=cutoff,
            result_type=data['result_type'], result_id=data['result_id'], result_snapshot=target.result_snapshot,
        ).filter(~Exists(unfinished)).update(result_snapshot=compact))


class Command(BaseCommand):
    help = 'Preview old terminal results; --apply --output archives, and optional --compact reduces verified duplicates.'

    def add_arguments(self, parser):
        parser.add_argument('--before', required=True, help='Exclusive ISO-8601 completion cutoff with timezone.')
        parser.add_argument('--limit', required=True, type=int, help='Maximum target snapshots, 1..1000.')
        parser.add_argument('--after-id', help='With --after-finished-at, resume after the previous batch cursor.')
        parser.add_argument('--after-finished-at', help='Completion timestamp of the previous batch last target.')
        parser.add_argument('--output', help='New JSONL path; parent directory must already exist.')
        parser.add_argument('--apply', action='store_true', help='Write archive; database changes require --compact too.')
        parser.add_argument('--compact', action='store_true', help='After durable archive, compact verified duplicate details with compare-and-swap.')

    def handle(self, *args, **options):
        try:
            cutoff = parse_datetime(options['before'])
        except (ValueError, TypeError):
            cutoff = None
        if cutoff is None or timezone.is_naive(cutoff) or cutoff >= timezone.now():
            raise CommandError('--before must be a past ISO-8601 timestamp with timezone.')
        if not 1 <= options['limit'] <= 1000:
            raise CommandError('--limit must be between 1 and 1000.')
        if options['apply'] and not options['output']:
            raise CommandError('--apply requires a new --output archive path.')

        unfinished = TaskTargetRun.objects.filter(task_id=OuterRef('task_id')).exclude(
            status__in=TaskRun.TERMINAL_STATUSES,
        )
        candidates = TaskTargetRun.objects.filter(
            status__in=TaskRun.TERMINAL_STATUSES, finished_at__lt=cutoff,
            task__status__in=TaskRun.TERMINAL_STATUSES, task__finished_at__lt=cutoff,
        ).filter(~Exists(unfinished)).order_by('finished_at', 'id')
        after_id, after_time = options['after_id'], options['after_finished_at']
        if after_id or after_time:
            from uuid import UUID
            from django.db.models import Q
            try:
                after_id = UUID(after_id)
                after_time = parse_datetime(after_time)
                if after_time is None or timezone.is_naive(after_time) or after_time >= cutoff:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise CommandError('Both cursor options must contain a UUID and timezone-aware timestamp before cutoff.') from None
            candidates = candidates.filter(Q(finished_at__gt=after_time) | Q(finished_at=after_time, id__gt=after_id))

        # Plain preview avoids JSON payloads; compaction assessment needs evidence.
        if options['apply'] or options['compact']:
            candidates = candidates.select_related('task')
        else:
            candidates = candidates.only('id', 'task_id', 'finished_at', 'status', 'result_type', 'result_id')
        candidates = candidates[:options['limit']]
        archive = None
        count, last_cursor, compacted = 0, None, 0
        digest = hashlib.sha256()
        try:
            if options['apply']:
                # O_EXCL refuses existing files and symlinks; never truncates an archive.
                descriptor = os.open(Path(options['output']), os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, 'O_BINARY', 0), 0o600)
                archive = os.fdopen(descriptor, 'w+b')
                manifest = encoded({'kind': 'manifest', 'schema_version': 1,
                                    'created_at': timezone.now(), 'before': cutoff,
                                    'limit': options['limit'], 'mode': 'archive_then_compact' if options['compact'] else 'archive_only',
                                    'after_id': after_id, 'after_finished_at': after_time})
                archive.write(manifest)
                digest.update(manifest)
            for target in candidates.iterator(chunk_size=1):
                task = target.task if options['apply'] or options['compact'] else TaskRun.objects.only('id').get(pk=target.task_id)
                refs = protected_references(target) + protected_references(task)
                if archive is not None:
                    row = encoded({'kind': 'result', 'target': fields(target), 'task': fields(task),
                                   'protected_references': refs,
                                   'fetched_log_ids': list(target.fetched_logs.values_list('pk', flat=True))})
                    archive.write(row)
                    digest.update(row)
                else:
                    self.stdout.write(encoded({'kind': 'candidate', 'target_id': target.pk,
                                               'task_id': target.task_id, 'finished_at': target.finished_at,
                                               'result_type': target.result_type, 'result_id': target.result_id,
                                               'protected_references': refs,
                                               'compaction_blocked': compaction_plan(target)[1] if options['compact'] else 'Use --compact to assess duplicate details.'}).decode().rstrip('\n'))
                count += 1
                last_cursor = {'after_id': str(target.pk), 'after_finished_at': target.finished_at.isoformat()}
            if archive is not None:
                # Flush payload before adding a completion marker; then sync the marker too.
                archive.flush()
                os.fsync(archive.fileno())
                archive.write(encoded({'kind': 'complete', 'count': count, 'sha256': digest.hexdigest(),
                                       'next_cursor': last_cursor}))
                archive.flush()
                os.fsync(archive.fileno())
                if options['compact']:
                    # Re-read our still-open archive, never an externally replaced path.
                    archive.seek(0)
                    for line in archive:
                        row = json.loads(line)
                        if row['kind'] == 'result':
                            compacted += int(compact_archived_target(row, cutoff))
        except OSError as exc:
            raise CommandError(f'Archive failed: {exc}. Any newly created file is untrusted; use a new path on retry.') from exc
        except DatabaseError as exc:
            raise CommandError(f'Database operation failed after {compacted} compactions: {exc}. Keep the archive and reconcile before retrying.') from exc
        finally:
            if archive is not None:
                archive.close()
        mode = ('archive_then_compact' if options['compact'] else 'archive') if options['apply'] else 'preview'
        self.stdout.write(encoded({'kind': 'summary', 'mode': mode,
                                   'count': count, 'compacted': compacted, 'next_cursor': last_cursor,
                                   'database_changed': bool(compacted)}).decode().rstrip('\n'))
