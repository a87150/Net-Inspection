"""Homepage aggregates over local synchronized objects; never query AD."""
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from net.models import Domain_Account, Domain_Computer, Domain_Group, TaskRun


def get_domain_statistics(*, today=None, inactive_days=60):
    """Use stored dates, excluding missing logins from the inactivity bucket.

    lastLogonTimestamp and the local snapshot can both lag behind actual logins.
    """
    today = today if today is not None else timezone.localdate(timezone=timezone.get_default_timezone())
    cutoff = today - timedelta(days=inactive_days)
    stats = {'inactive_days': inactive_days}
    for prefix, model in (('account', Domain_Account), ('computer', Domain_Computer)):
        counts = model.objects.aggregate(
            total=Count('pk'),
            active=Count('pk', filter=Q(is_active=True)),
            inactive=Count('pk', filter=Q(is_active=False)),
            stale=Count('pk', filter=Q(is_active=True, last_login_date__lt=cutoff)),
            missing_login=Count('pk', filter=Q(is_active=True, last_login_date__isnull=True)),
        )
        stats.update({f'{prefix}_{key}': value for key, value in counts.items()})
    stats.update(Domain_Group.objects.aggregate(
        group_total=Count('pk'),
        security_group_total=Count('pk', filter=Q(group_category='security')),
        distribution_group_total=Count('pk', filter=Q(group_category='distribution')),
    ))
    stats['latest_successful_sync'] = TaskRun.objects.filter(
        task_type=TaskRun.TaskType.DOMAIN_SYNC, status=TaskRun.Status.SUCCESS,
    ).aggregate(latest=Max('finished_at'))['latest']
    return stats
