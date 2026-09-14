"""Two read-only SNMP counter samples, producing short-window interface rates."""
import asyncio
import time

DISCONTINUITY = '1.3.6.1.2.1.31.1.1.1.19'
SAMPLE_SECONDS = 2
FALLBACK_GET_LIMIT = 4


def calculate_rates(first, second):
    elapsed = second['time'] - first['time']
    rows = []
    for index, current in second['interfaces'].items():
        before = first['interfaces'].get(index)
        row = {'index': index, 'name': current.get('name') or index,
               'speed_mbps': current.get('speed_mbps'), 'rx_mbps': None, 'tx_mbps': None,
               'rx_bps': None, 'tx_bps': None, 'utilization_percent': None,
               'data_state': 'unknown'}
        reason = ''
        if not before or elapsed <= 0 or first.get('uptime') is None or second.get('uptime') is None:
            reason = '缺少有效的前后采样或设备运行时间'
        elif second['uptime'] < first['uptime'] or current.get('discontinuity') != before.get('discontinuity'):
            reason = '采样期间设备重启或计数器发生重置'
        elif current.get('bits') != before.get('bits') or current.get('speed_mbps') != before.get('speed_mbps'):
            reason = '采样期间计数器类型或端口带宽变化'
        else:
            rates = []
            speed = current.get('speed_mbps')
            for key in ('in_octets', 'out_octets'):
                a, b = before.get(key), current.get(key)
                if not isinstance(a, int) or not isinstance(b, int) or a < 0 or b < 0:
                    reason = '收发计数器缺失或无效'
                    break
                bits = current.get('bits')
                if bits == 32 and (not speed or speed * 1_000_000 * elapsed / 8 >= 2 ** 32):
                    reason = '32位计数器可能多次回绕，需要64位计数器'
                    break
                delta = b - a
                if delta < 0 and bits == 32:
                    delta += 2 ** 32
                if delta < 0:
                    reason = '收发计数器倒退，可能已重置'
                    break
                rate = delta * 8 / elapsed
                if speed and rate > speed * 1_000_000 * 1.05:
                    reason = '采样差值超过端口容量，不能可靠计算速率'
                    break
                rates.append(rate)
            if not reason:
                row.update(rx_bps=round(rates[0], 2), tx_bps=round(rates[1], 2),
                           rx_mbps=round(rates[0] / 1_000_000, 4), tx_mbps=round(rates[1] / 1_000_000, 4),
                           utilization_percent=round(max(rates) / (speed * 1_000_000) * 100, 2) if speed else None,
                           data_state='known')
        if reason:
            row['reason'] = reason
        rows.append(row)
    return {'sample_seconds': round(elapsed, 3), 'interfaces': rows,
            'status': 'success' if rows and all(row['data_state'] == 'known' for row in rows) else 'partial'}


async def sample(session, *, names=None):
    from .snmp import (_safe_get, _safe_walk, _table, SYS_UPTIME, IF_NAME, IF_HIGH_SPEED,
                       IF_SPEED, IF_HC_IN, IF_HC_OUT, IF_IN_OCTETS, IF_OUT_OCTETS)
    tables = {}
    if names is None:
        names = _table({'tables': {IF_NAME: await _safe_walk(session, IF_NAME)}}, IF_NAME)
    tables[IF_NAME] = names
    # Bandwidth is live metadata: a link renegotiation must invalidate the rate.
    for oid in (IF_HIGH_SPEED,):
        tables[oid] = _table({'tables': {oid: await _safe_walk(session, oid)}}, oid)
    def integer(value):
        try:
            return int(str(value))
        except (ValueError, TypeError):
            return None
    # Exclude the one-off name walk from sample timing, otherwise its latency
    # biases the midpoint of only the first counter sample.
    started = time.monotonic()
    for oid in (IF_HC_IN, IF_HC_OUT, DISCONTINUITY):
        tables[oid] = _table({'tables': {oid: await _safe_walk(session, oid)}}, oid)
    indices = set(names) | set(tables[IF_HIGH_SPEED]) | set(tables[IF_HC_IN]) | set(tables[IF_HC_OUT]) | set(tables[DISCONTINUITY])
    tables[IF_IN_OCTETS], tables[IF_OUT_OCTETS], tables[IF_SPEED] = {}, {}, {}
    walked = set()
    if not indices:
        # Agents lacking ifXTable still need legacy interface discovery.
        tables[IF_IN_OCTETS] = _table({'tables': {IF_IN_OCTETS: await _safe_walk(session, IF_IN_OCTETS)}}, IF_IN_OCTETS)
        walked.add(IF_IN_OCTETS)
        indices.update(tables[IF_IN_OCTETS])
    missing_hc = {index for index in indices
                  if any(integer(tables[oid].get(index)) is None or integer(tables[oid].get(index)) < 0
                         for oid in (IF_HC_IN, IF_HC_OUT))}
    missing_speed = {index for index in indices if not integer(tables[IF_HIGH_SPEED].get(index))}
    for oid, missing in ((IF_IN_OCTETS, missing_hc), (IF_OUT_OCTETS, missing_hc), (IF_SPEED, missing_speed)):
        # A large legacy switch needs a table walk, not two GETs per port.
        # An attempted walk is final even when unsupported or incomplete; do
        # not amplify that failure with an unbounded per-interface retry burst.
        if oid in walked:
            continue
        if len(missing) > FALLBACK_GET_LIMIT:
            tables[oid] = _table({'tables': {oid: await _safe_walk(session, oid)}}, oid)
        else:
            for index in sorted(missing):
                if index not in tables[oid]:
                    tables[oid][index] = await _safe_get(session, f'{oid}.{index}')
    uptime = await _safe_get(session, SYS_UPTIME)
    finished = time.monotonic()
    interfaces = {}
    for index in sorted(indices):
        use64 = all(integer(tables[oid].get(index)) is not None and integer(tables[oid].get(index)) >= 0
                    for oid in (IF_HC_IN, IF_HC_OUT))
        speed = integer(tables[IF_HIGH_SPEED].get(index)) or (integer(tables[IF_SPEED].get(index)) or 0) / 1_000_000
        interfaces[index] = {'name': str(tables[IF_NAME].get(index) or index), 'bits': 64 if use64 else 32,
            'in_octets': integer(tables[IF_HC_IN if use64 else IF_IN_OCTETS].get(index)),
            'out_octets': integer(tables[IF_HC_OUT if use64 else IF_OUT_OCTETS].get(index)),
            'speed_mbps': speed or None, 'discontinuity': integer(tables[DISCONTINUITY].get(index))}
    return {'time': (started + finished) / 2, 'uptime': integer(uptime), 'interfaces': interfaces}


async def collect_traffic(session):
    first = await sample(session)
    await asyncio.sleep(SAMPLE_SECONDS)
    # Request-local reuse: no sessions, credentials, or device metadata survive
    # this collection. Dynamic counters, uptime and discontinuity are reread.
    second = await sample(session, names={index: row['name'] for index, row in first['interfaces'].items()})
    return calculate_rates(first, second)
