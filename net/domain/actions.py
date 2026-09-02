ACCOUNT_ACTIONS = frozenset({
    'create_user', 'move_ou', 'add_group', 'remove_group', 'reset_password',
    'must_change_password', 'password_never_expires', 'unlock', 'enable', 'disable',
})

COMPUTER_ACTIONS = frozenset({
    'move_ou', 'add_group', 'remove_group', 'unlock', 'enable', 'disable',
})
