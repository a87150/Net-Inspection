from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('net', '0022_network_device_snmp'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='taskrun',
            name='net_task_state_shape_ck',
        ),
        migrations.AddConstraint(
            model_name='taskrun',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status='queued',
                        finished_at__isnull=True,
                        lease_expires_at__isnull=True,
                    )
                    | (
                        models.Q(
                            status='running',
                            started_at__isnull=False,
                            finished_at__isnull=True,
                            lease_expires_at__isnull=False,
                        )
                        & ~models.Q(worker_id='')
                    )
                    | models.Q(
                        status='success',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                        progress=100,
                        completed_targets=models.F('total_targets'),
                        successful_targets=models.F('total_targets'),
                        failed_targets=0,
                    )
                    | (
                        models.Q(
                            status='partial',
                            finished_at__isnull=False,
                            lease_expires_at__isnull=True,
                            progress=100,
                            completed_targets=models.F('total_targets'),
                            failed_targets__gt=0,
                        )
                        & models.Q(
                            completed_targets=(
                                models.F('successful_targets')
                                + models.F('failed_targets')
                            ),
                        )
                    )
                    | models.Q(
                        status='failed',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                        progress=100,
                        completed_targets=models.F('total_targets'),
                        successful_targets=0,
                        failed_targets=models.F('total_targets'),
                    )
                    | models.Q(
                        status='cancelled',
                        finished_at__isnull=False,
                        lease_expires_at__isnull=True,
                    )
                ),
                name='net_task_state_shape_ck',
            ),
        ),
    ]
