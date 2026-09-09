"""One Netmiko session per network target; Linux keeps its own SSH client."""

DEVICE_TYPES = {
    'cisco': 'cisco_ios', 'h3c': 'hp_comware', 'huawei': 'huawei',
    'ruijie': 'ruijie_os', 'generic': 'generic_termserver',
}


def connect_network(device, timeout, vendor):
    from netmiko import ConnectHandler

    client = ConnectHandler(
        device_type=DEVICE_TYPES[vendor], host=device.ip, port=device.port or 22,
        username=device.username, password=device.password,
        conn_timeout=timeout, auth_timeout=timeout, banner_timeout=timeout,
        blocking_timeout=timeout, timeout=timeout, session_timeout=timeout,
        read_timeout_override=timeout, use_keys=False, allow_agent=False,
        ssh_strict=False, fast_cli=False, encoding='utf-8',
        disable_lf_normalization=True, session_log=None, auto_connect=False,
    )
    try:
        client.establish_connection()
        client.session_preparation()
    except BaseException:
        client.disconnect()
        raise
    return client
