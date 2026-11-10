"""One Netmiko session per network target; Linux keeps its own SSH client."""

DEVICE_TYPES = {
    'cisco': 'cisco_ios', 'h3c': 'hp_comware', 'huawei': 'huawei',
    'ruijie': 'ruijie_os', 'generic': 'generic_termserver',
}


def device_type_for(vendor, protocol):
    """Netmiko ships a telnet driver for every vendor we map; SSH is the bare name."""
    return DEVICE_TYPES[vendor] + ('_telnet' if protocol == 'telnet' else '')


def connect_network(device, timeout, vendor):
    from netmiko import ConnectHandler

    protocol = getattr(device, 'effective_cli_protocol', 'ssh')
    port = getattr(device, 'effective_port', None) or device.port or (23 if protocol == 'telnet' else 22)
    arguments = dict(
        device_type=device_type_for(vendor, protocol), host=device.ip, port=port,
        username=device.username, password=device.password,
        conn_timeout=timeout, auth_timeout=timeout, banner_timeout=timeout,
        blocking_timeout=timeout, timeout=timeout, session_timeout=timeout,
        fast_cli=False, encoding='utf-8',
        disable_lf_normalization=True, session_log=None, auto_connect=False,
    )
    # Telnet has no SSH layer, so the key/agent switches would only be ignored.
    if protocol != 'telnet':
        arguments.update(read_timeout_override=timeout, use_keys=False,
                         allow_agent=False, ssh_strict=False)
    client = ConnectHandler(**arguments)
    try:
        client.establish_connection()
        client.session_preparation()
    except BaseException:
        client.disconnect()
        raise
    return client
