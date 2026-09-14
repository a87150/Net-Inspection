"""Explicit identities for view tests; production authorization is never disabled."""
from django.contrib.auth import get_user_model


def login_admin(client, username='view-test-admin'):
    user, _ = get_user_model().objects.get_or_create(
        username=username, defaults={'is_staff': True, 'is_active': True},
    )
    if not user.is_staff or not user.is_active:
        user.is_staff = True
        user.is_active = True
        user.save(update_fields=['is_staff', 'is_active'])
    client.force_login(user)
    return user


def login_reader(client, username='view-test-reader'):
    user, _ = get_user_model().objects.get_or_create(username=username)
    client.force_login(user)
    return user
