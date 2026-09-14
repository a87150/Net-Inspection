from django.test import SimpleTestCase
import asyncio
from unittest.mock import patch
from net.devices.network import snmp, traffic


class TrafficSession:
    """In-memory agent: metadata takes 20 seconds, counters take two."""
    def __init__(self, missing_hc=False, name='eth0', changed=None):
        self.calls = []
        self.tick = self.round = 0
        self.missing_hc, self.name, self.changed = missing_hc, name, changed

    async def walk(self, oid):
        self.calls.append(('walk', oid))
        if oid == snmp.IF_NAME:
            self.tick += 20
        if oid == snmp.IF_HC_IN:
            self.round += 1
            self.tick += 2
        values = {
            snmp.IF_NAME: {'1': self.name, '2': 'eth1'},
            snmp.IF_HIGH_SPEED: {'1': 1000, '2': 100},
            snmp.IF_SPEED: {'1': 1000000000, '2': 100000000},
            snmp.IF_HC_IN: {'1': 2**60 + self.round * 250000},
            snmp.IF_HC_OUT: {'1': 2**60 + self.round * 500000},
            snmp.IF_IN_OCTETS: {'1': 0, '2': self.round * 250000},
            snmp.IF_OUT_OCTETS: {'1': 0, '2': self.round * 500000},
            traffic.DISCONTINUITY: {'1': 0, '2': 0},
        }
        if not self.missing_hc:
            values[snmp.IF_HC_IN]['2'] = self.round * 250000
            values[snmp.IF_HC_OUT]['2'] = self.round * 500000
        if self.changed == 'discontinuity':
            values[traffic.DISCONTINUITY]['1'] = self.round
        if self.changed == 'speed':
            values[snmp.IF_HIGH_SPEED]['1'] = 1000 if self.round == 0 else 100
        return list(values.get(oid, {}).items())

    async def get(self, oid):
        self.calls.append(('get', oid))
        if oid == snmp.SYS_UPTIME:
            return 1000 + self.round * 200
        if oid == snmp.IF_IN_OCTETS + '.2':
            return self.round * 250000
        if oid == snmp.IF_OUT_OCTETS + '.2':
            return self.round * 500000
        return None


class TrafficSamplingTests(SimpleTestCase):
    def collect(self, session):
        with patch.object(traffic, 'SAMPLE_SECONDS', 0), patch.object(
                traffic.time, 'monotonic', side_effect=lambda: session.tick):
            return asyncio.run(traffic.collect_traffic(session))

    def test_hc_sampling_reuses_names_without_biasing_elapsed_time(self):
        session = TrafficSession()
        result = self.collect(session)
        self.assertEqual(session.calls.count(('walk', snmp.IF_NAME)), 1)
        self.assertEqual(sum(method == 'walk' for method, _ in session.calls), 9)
        for oid in (snmp.IF_IN_OCTETS, snmp.IF_OUT_OCTETS, snmp.IF_SPEED):
            self.assertNotIn(('walk', oid), session.calls)
        self.assertEqual(result['sample_seconds'], 2)
        self.assertTrue(all(row['rx_mbps'] == 1 for row in result['interfaces']))

    def test_legacy_reads_target_only_interfaces_without_hc(self):
        session = TrafficSession(missing_hc=True)
        result = self.collect(session)
        for oid in (snmp.IF_IN_OCTETS, snmp.IF_OUT_OCTETS):
            self.assertNotIn(('walk', oid), session.calls)
            self.assertEqual(session.calls.count(('get', oid + '.2')), 2)
            self.assertNotIn(('get', oid + '.1'), session.calls)
        self.assertTrue(all(row['rx_mbps'] == 1 for row in result['interfaces']))

    def test_metadata_is_local_to_each_collection(self):
        self.collect(TrafficSession(name='first-device'))
        result = self.collect(TrafficSession(name='second-device'))
        self.assertEqual(next(row for row in result['interfaces'] if row['index'] == '1')['name'], 'second-device')

    def test_live_speed_and_discontinuity_changes_invalidate_rates(self):
        for changed in ('speed', 'discontinuity'):
            with self.subTest(changed=changed):
                result = self.collect(TrafficSession(changed=changed))
                row = next(row for row in result['interfaces'] if row['index'] == '1')
                self.assertEqual(row['data_state'], 'unknown')
                self.assertIsNone(row['rx_bps'])

    def test_partial_hc_pair_falls_back_as_a_pair(self):
        class PartialSession(TrafficSession):
            async def walk(self, oid):
                rows = await super().walk(oid)
                if oid == snmp.IF_HC_OUT:
                    return [(index, value) for index, value in rows if index != '2']
                return rows
        session = PartialSession()
        result = self.collect(session)
        row = next(row for row in result['interfaces'] if row['index'] == '2')
        self.assertEqual((row['rx_mbps'], row['tx_mbps']), (1, 2))
        self.assertEqual(session.calls.count(('get', snmp.IF_IN_OCTETS + '.2')), 2)
        self.assertEqual(session.calls.count(('get', snmp.IF_OUT_OCTETS + '.2')), 2)

    def test_legacy_agent_without_ifxtable_is_discovered(self):
        class LegacySession(TrafficSession):
            async def walk(self, oid):
                rows = await super().walk(oid)
                if oid in (snmp.IF_NAME, snmp.IF_HIGH_SPEED, snmp.IF_HC_IN,
                           snmp.IF_HC_OUT, traffic.DISCONTINUITY):
                    return []
                return [(index, value) for index, value in rows if index == '2']

            async def get(self, oid):
                if oid == snmp.IF_SPEED + '.2':
                    self.calls.append(('get', oid))
                    return 100000000
                return await super().get(oid)
        session = LegacySession()
        row = self.collect(session)['interfaces'][0]
        self.assertEqual((row['index'], row['speed_mbps'], row['rx_mbps']), ('2', 100, 1))
        self.assertEqual(session.calls.count(('walk', snmp.IF_IN_OCTETS)), 1)
        self.assertEqual(session.calls.count(('get', snmp.IF_SPEED + '.2')), 2)

    def test_counter_width_change_does_not_mix_hc_and_legacy_deltas(self):
        class ChangingSession(TrafficSession):
            async def walk(self, oid):
                rows = await super().walk(oid)
                if self.round == 2 and oid in (snmp.IF_HC_IN, snmp.IF_HC_OUT):
                    return [(index, value) for index, value in rows if index != '2']
                return rows
        row = next(row for row in self.collect(ChangingSession())['interfaces'] if row['index'] == '2')
        self.assertEqual(row['data_state'], 'unknown')
        self.assertIsNone(row['rx_mbps'])

    def test_32_bit_single_wrap_is_accurate_and_ambiguous_wrap_is_unknown(self):
        before = {'bits': 32, 'speed_mbps': 100, 'discontinuity': 0,
                  'in_octets': 2**32 - 100, 'out_octets': 2**32 - 200}
        after = {**before, 'in_octets': 249900, 'out_octets': 499800}
        first = {'time': 1, 'uptime': 1000, 'interfaces': {'1': before}}
        second = {'time': 3, 'uptime': 1200, 'interfaces': {'1': after}}
        row = traffic.calculate_rates(first, second)['interfaces'][0]
        self.assertEqual((row['rx_mbps'], row['tx_mbps']), (1, 2))
        second['time'] = 401
        row = traffic.calculate_rates(first, second)['interfaces'][0]
        self.assertEqual(row['data_state'], 'unknown')


class ManyPortSession(TrafficSession):
    def __init__(self, *, ports=48, hc=False, absent=False, discover=False):
        super().__init__()
        self.ports, self.hc, self.absent, self.discover = ports, hc, absent, discover

    def value(self, oid, index):
        if oid == snmp.IF_NAME:
            return f'eth{index}'
        if oid == snmp.IF_SPEED:
            return 100000000
        if oid in (snmp.IF_IN_OCTETS, snmp.IF_HC_IN):
            return self.round * 250000 + (2**60 if oid == snmp.IF_HC_IN else 0)
        if oid in (snmp.IF_OUT_OCTETS, snmp.IF_HC_OUT):
            return self.round * 500000 + (2**60 if oid == snmp.IF_HC_OUT else 0)
        return 0

    async def walk(self, oid):
        await super().walk(oid)
        if oid == snmp.IF_HIGH_SPEED or (self.discover and oid in (snmp.IF_NAME, traffic.DISCONTINUITY)):
            return []
        if oid in (snmp.IF_HC_IN, snmp.IF_HC_OUT):
            return [('1', self.value(oid, '1'))] if self.hc else []
        if self.absent and oid in (snmp.IF_IN_OCTETS, snmp.IF_OUT_OCTETS, snmp.IF_SPEED):
            return []
        return [(str(index), self.value(oid, str(index))) for index in range(1, self.ports + 1)]

    async def get(self, oid):
        self.calls.append(('get', oid))
        if oid == snmp.SYS_UPTIME:
            return 1000 + self.round * 200
        base, _, index = oid.rpartition('.')
        return self.value(base, index)


class AdaptiveTrafficTests(SimpleTestCase):
    collect = TrafficSamplingTests.collect

    def test_many_legacy_ports_use_three_fallback_walks_per_sample(self):
        for options in ({}, {'hc': True}, {'discover': True}, {'absent': True}):
            with self.subTest(options=options):
                session = ManyPortSession(**options)
                result = self.collect(session)
                self.assertEqual(len(result['interfaces']), 48)
                for oid in (snmp.IF_IN_OCTETS, snmp.IF_OUT_OCTETS, snmp.IF_SPEED):
                    self.assertEqual(session.calls.count(('walk', oid)), 2)
                self.assertEqual([oid for method, oid in session.calls if method == 'get'],
                                 [snmp.SYS_UPTIME, snmp.SYS_UPTIME])
                self.assertEqual(len(session.calls), 17)
                if options.get('absent'):
                    self.assertTrue(all(row['rx_mbps'] is None for row in result['interfaces']))
                else:
                    self.assertTrue(all((row['rx_mbps'], row['tx_mbps']) == (1, 2)
                                        for row in result['interfaces']))
                if options.get('hc'):
                    with patch.object(traffic.time, 'monotonic', side_effect=lambda: session.tick):
                        sample = asyncio.run(traffic.sample(session))
                    self.assertEqual(sample['interfaces']['1']['bits'], 64)
                    self.assertGreater(sample['interfaces']['1']['in_octets'], 2**60)

    def test_small_fallback_sets_keep_targeted_gets(self):
        session = ManyPortSession(ports=4)
        result = self.collect(session)
        for oid in (snmp.IF_IN_OCTETS, snmp.IF_OUT_OCTETS, snmp.IF_SPEED):
            self.assertNotIn(('walk', oid), session.calls)
            self.assertEqual(sum(method == 'get' and value.startswith(oid + '.')
                                 for method, value in session.calls), 8)
        self.assertTrue(all(row['rx_mbps'] == 1 for row in result['interfaces']))


class TrafficTests(SimpleTestCase):
    def test_collector_takes_two_samples_and_returns_only_requested_rates(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from net.devices.network import snmp
        class Session:
            samples = 0
            closed = False
            async def get(self, oid):
                self.samples += 1
                return 1000 + self.samples * 100
            async def walk(self, oid):
                values = {snmp.IF_NAME: 'eth0', snmp.IF_HIGH_SPEED: 1000,
                          snmp.IF_HC_IN: 2 ** 60 + self.samples * 100,
                          snmp.IF_HC_OUT: 2 ** 60 + self.samples * 200}
                return [('1', values[oid])] if oid in values else []
            async def close(self):
                self.closed = True
        session = Session()
        with patch('net.devices.network.traffic.SAMPLE_SECONDS', 0.02):
            result = snmp.collect_network_snmp(SimpleNamespace(vendor=''), selected_items=['traffic'], session_factory=lambda *args: session)
        self.assertEqual(session.samples, 2)
        self.assertTrue(session.closed)
        self.assertEqual(result.status, 'success')
        self.assertEqual(set(result.data), {'traffic'})
        self.assertGreater(result.data['traffic']['interfaces'][0]['rx_mbps'], 0)

    def test_two_samples_compute_real_rates_and_keep_64_bit_precision(self):
        from net.devices.network.traffic import calculate_rates
        baseline = 2 ** 60
        first = {'time': 10, 'uptime': 10000, 'interfaces': {'1': {
            'name': 'eth0', 'in_octets': baseline, 'out_octets': baseline, 'bits': 64,
            'speed_mbps': 1000, 'discontinuity': 0}}}
        second = {'time': 12, 'uptime': 10200, 'interfaces': {'1': {
            **first['interfaces']['1'], 'in_octets': baseline + 25000000, 'out_octets': baseline + 50000000}}}
        row = calculate_rates(first, second)['interfaces'][0]
        self.assertEqual(row['rx_mbps'], 100)
        self.assertEqual(row['tx_mbps'], 200)
        self.assertEqual(row['utilization_percent'], 20)

    def test_restart_or_counter_reset_never_becomes_a_traffic_spike(self):
        from net.devices.network.traffic import calculate_rates
        row = {'in_octets': 100, 'out_octets': 200, 'bits': 64, 'speed_mbps': 1000, 'discontinuity': 0}
        first = {'time': 1, 'uptime': 1000, 'interfaces': {'1': row}}
        for uptime, counter, discontinuity in [(5, 1000, 0), (1200, 1, 0), (1200, 1000, 1200)]:
            second = {'time': 3, 'uptime': uptime, 'interfaces': {'1': {**row, 'in_octets': counter, 'discontinuity': discontinuity}}}
            result = calculate_rates(first, second)['interfaces'][0]
            self.assertEqual(result['data_state'], 'unknown')
            self.assertIsNone(result['rx_mbps'])

    def test_unknown_bandwidth_keeps_speed_without_fake_utilization(self):
        from net.devices.network.traffic import calculate_rates
        row = {'in_octets': 100, 'out_octets': 200, 'bits': 64, 'speed_mbps': None}
        result = calculate_rates({'time': 1, 'uptime': 1000, 'interfaces': {'1': row}},
                 {'time': 3, 'uptime': 1200, 'interfaces': {'1': {**row, 'in_octets': 250100}}})['interfaces'][0]
        self.assertEqual(result['rx_mbps'], 1)
        self.assertIsNone(result['utilization_percent'])
