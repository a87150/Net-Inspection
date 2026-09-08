from django.db import migrations


def split_domain(apps, schema_editor):
    Profile = apps.get_model('net', 'ComputerAnalysisProfile')
    for profile in Profile.objects.using(schema_editor.connection.alias).all():
        items = profile.analysis_items
        if not isinstance(items, list) or 'domain' not in items:
            continue
        profile.analysis_items = list(dict.fromkeys(
            part for item in items
            for part in (('domain_trust', 'group_policy') if item == 'domain' else (item,))
        ))
        profile.save(using=schema_editor.connection.alias, update_fields=['analysis_items'])


class Migration(migrations.Migration):
    dependencies = [('net', '0031_domain_sync_tasks')]
    operations = [migrations.RunPython(split_domain, migrations.RunPython.noop)]
