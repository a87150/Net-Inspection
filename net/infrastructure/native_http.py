"""Cancellable Dahua configuration transport; no executor/DNS worker threads."""
import asyncio
import socket

import aiohttp
import dns.asyncresolver


class ConfigurationResolver(aiohttp.abc.AbstractResolver):
    """Use async DNS rather than asyncio's blocking getaddrinfo executor.

    Numeric IPs bypass DNS in aiohttp. Hostnames use the OS-configured DNS
    servers (not hosts-file/NSS/mDNS lookup). All DNS I/O stays in the caller's
    cancellable task; disabling connector DNS caching prevents shielding it.
    """
    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        try:
            answers = await dns.asyncresolver.Resolver().resolve_name(host, family=family)
        except dns.exception.DNSException as exc:
            raise OSError('configuration DNS failed') from exc
        return [{'hostname': host, 'host': address, 'port': port, 'family': af,
                 'proto': socket.IPPROTO_TCP, 'flags': socket.AI_NUMERICHOST}
                for address, af in answers.addresses_and_families()]

    async def close(self):
        # No cached resolver, sockets or independent tasks are owned here.
        pass


async def read_dahua_network(device, url, budget):
    from net.data_exchange.adapters import MAX_CONFIG_BYTES
    from net.devices.security.configuration import validate_network

    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget
    connector = aiohttp.TCPConnector(
        resolver=ConfigurationResolver(), use_dns_cache=False, force_close=True)
    try:
        # aiohttp may round long ClientTimeout values. This outer absolute
        # deadline does not round or restart for Digest retries/body progress.
        async with asyncio.timeout_at(deadline):
            async with aiohttp.ClientSession(
                connector=connector, connector_owner=False,
                timeout=aiohttp.ClientTimeout(total=budget),
                middlewares=(aiohttp.DigestAuthMiddleware(
                    device.api_username or '', device.api_password or ''),),
                headers={'Accept': 'text/plain', 'Accept-Encoding': 'identity'},
                auto_decompress=False, trust_env=False,
            ) as session:
                async with session.get(
                    url, params={'action': 'getConfig', 'name': 'Network'},
                    ssl=device.verify_ssl, allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise ValueError('configuration HTTP failure')
                    media = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if media != 'text/plain':
                        return {'status': 'failed' if media in ('application/json', 'text/html', 'application/xml') else 'unsupported',
                                'message': '仅支持具名原生可读配置文本；二进制、未知格式或状态响应不支持。'}
                    length = response.headers.get('content-length')
                    if response.headers.get('content-encoding', 'identity') != 'identity':
                        raise ValueError('unsupported transfer encoding')
                    if length is not None and not 0 <= int(length) <= MAX_CONFIG_BYTES:
                        raise ValueError('configuration size limit')
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body) > MAX_CONFIG_BYTES or loop.time() >= deadline:
                            raise ValueError('configuration bound exceeded')
                    if length is not None and len(body) != int(length):
                        raise ValueError('truncated configuration')
                    content = bytes(body).decode('utf-8')
                    validate_network(content, 'text')
    finally:
        # Abort TLS immediately; never wait for a peer close_notify. DNS/body
        # cancellation is propagated and awaited, not abandoned in a thread.
        await connector.close(abort_ssl=True)
    # EOF/validation/cleanup may complete synchronously without dispatching the
    # timeout callback. Never declare a late response complete in that case.
    if loop.time() >= deadline:
        raise TimeoutError('configuration deadline exceeded')
    return {'status': 'success', 'vendor': 'dahua', 'format': 'text',
            'scope': 'Network', 'complete': True, 'full_backup': False, 'content': content}
