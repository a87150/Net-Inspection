"""Import a frozen SQLite backup into an empty migrated MariaDB database.

Web/Worker must be stopped. NET_ENV_FILE selects target configuration. The source
is never modified. Data mismatch rolls back the import; export remains for audit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve(strict=True)
    output = Path(args.output).resolve()
    if output.exists():
        raise RuntimeError('Export already exists; choose a new output path.')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'net.settings')
    import django
    django.setup()
    from django.apps import apps
    from django.core.management import call_command
    from django.core import serializers
    from django.db import connections, transaction
    from django.db.models import CharField
    from django.db.models.functions import Cast
    from net.models import TaskRun
    target = connections['default']
    if target.vendor != 'mysql':
        raise RuntimeError('Target must use the MySQL/MariaDB backend.')
    source_config = dict(connections.databases['default'])
    source_config.update(ENGINE='django.db.backends.sqlite3',
                         NAME=source.as_uri() + '?mode=ro', OPTIONS={'uri': True},
                         CONN_MAX_AGE=0, CONN_HEALTH_CHECKS=False)
    connections.databases['legacy'] = source_config
    if TaskRun.objects.using('legacy').filter(status__in=['queued', 'running']).exists():
        raise RuntimeError('Source contains active tasks; finish/cancel them before migration.')
    excluded = {'contenttypes.contenttype', 'auth.permission'}
    serializers.register_serializer('migration_json', 'deploy.migration_json')
    models = [model for model in apps.get_models()
              if model._meta.managed and not model._meta.proxy and model._meta.label_lower not in excluded]
    if any(model._base_manager.using('default').exists() for model in models):
        raise RuntimeError('Target contains application data; refusing to overwrite it.')
    with output.open('x', encoding='utf-8') as stream:
        call_command('dumpdata', database='legacy', format='migration_json', use_base_manager=True,
                     use_natural_foreign_keys=True, exclude=list(excluded), stdout=stream, verbosity=0)

    class Digest:
        def __init__(self):
            self.hash = hashlib.sha256()

        def write(self, value):
            self.hash.update(value.encode('utf-8'))

    def fingerprint(model, alias):
        digest = Digest()
        rows = model._base_manager.using(alias).annotate(
            _migration_order=Cast('pk', CharField())).order_by('_migration_order')
        count = rows.count()
        serializers.serialize('migration_json', rows.iterator(chunk_size=200), stream=digest,
                              use_natural_foreign_keys=True, sort_keys=True)
        return count, digest.hash.hexdigest()

    reports = []
    mismatches = []
    print('Export complete; importing records in one transaction...', flush=True)
    with transaction.atomic(using='default'):
        pending_relations = []
        with target.constraint_checks_disabled(), output.open(encoding='utf-8') as stream:
            for obj in serializers.deserialize('json', stream, using='default'):
                relations = obj.m2m_data
                obj.save(using='default', save_m2m=False)
                if relations:
                    pending_relations.append((type(obj.object), obj.object.pk, relations))
            # Domain and alert relation guards need all referenced task rows
            # to exist. Preserve these guards rather than disconnect signals.
            for model, pk, relations in pending_relations:
                row = model._base_manager.using('default').get(pk=pk)
                for name, identities in relations.items():
                    getattr(row, name).set_base(identities, raw=True)
        print('Import complete; verifying counts and record hashes...', flush=True)
        for model in models:
            before, after = fingerprint(model, 'legacy'), fingerprint(model, 'default')
            if before != after:
                mismatches.append(model._meta.label_lower)
            reports.append({'model': model._meta.label_lower, 'count': after[0], 'sha256': after[1]})
            print(model._meta.label_lower + ': ' + str(after[0]) +
                  (' verified' if before == after else ' MISMATCH'), flush=True)
        if mismatches:
            raise RuntimeError('Verification mismatch: ' + ', '.join(mismatches))
        target.check_constraints()
    report = output.with_suffix('.verification.json')
    report.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Verified {len(reports)} models, {sum(row["count"] for row in reports)} records.', flush=True)
    print(f'Export: {output}\nVerification: {report}', flush=True)
    connections.close_all()


if __name__ == '__main__':
    main()
