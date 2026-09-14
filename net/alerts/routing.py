"""Non-secret enqueue-time routing; credentials and revocation remain live."""
from net.models import AlertPolicy, ComputerAnalysisProfile


def snapshot_routing(profile):
    field = 'analysis_profile' if isinstance(profile, ComputerAnalysisProfile) else 'inspection_profile'
    policy = AlertPolicy.objects.filter(**{field: profile}).first()
    default = AlertPolicy.objects.filter(default_slot=AlertPolicy.DEFAULT_SLOT).first()
    mode = policy.mode if policy else 'inherit'
    effective = policy if mode == 'override' else default
    return {'mode': mode, 'policy_id': str(policy.pk) if policy else None,
            'effective_policy_id': str(effective.pk) if effective else None,
            'channel_ids': [str(pk) for pk in effective.channels.filter(is_enabled=True)
                            .order_by('pk').values_list('pk', flat=True)] if effective else []}
