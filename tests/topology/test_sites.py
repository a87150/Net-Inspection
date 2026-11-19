"""场地切分：只看链路结构，不依赖设备命名。"""

from django.test import SimpleTestCase

from index.dashboard.topology_presentation import site_components


def node(pk, *, role, tier, label=None):
    return {
        'id': f'networks:{pk}', 'kind': 'backbone', 'tier': tier,
        'label': label or pk.upper(), 'role': role, 'site': '', '_subnet': '',
    }


def link(local, remote):
    return {
        'local_device_id': local, 'remote_device_id': remote,
        'resolution_status': 'resolved', 'status': 'current',
    }


class SiteComponentTests(SimpleTestCase):
    def test_single_site_stays_one_component(self):
        nodes = [
            node('fw', role='firewall', tier=0),
            node('core', role='core_switch', tier=1),
            node('access', role='access_switch', tier=3),
        ]
        components, _ = site_components(
            nodes, [link('fw', 'core'), link('core', 'access')], {},
        )

        self.assertEqual(len(components), 1)
        self.assertEqual({n['site_id'] for n in nodes}, {0})

    def test_exit_to_exit_link_splits_sites(self):
        nodes = [
            node('fw-a', role='firewall', tier=0),
            node('core-a', role='core_switch', tier=1),
            node('fw-b', role='firewall', tier=0),
            node('core-b', role='core_switch', tier=1),
        ]
        components, _ = site_components(
            nodes,
            [link('fw-a', 'core-a'), link('fw-b', 'core-b'), link('fw-a', 'fw-b')],
            {},
        )

        self.assertEqual(len(components), 2)
        site = {n['id']: n['site_id'] for n in nodes}
        self.assertEqual(site['networks:fw-a'], site['networks:core-a'])
        self.assertEqual(site['networks:fw-b'], site['networks:core-b'])
        self.assertNotEqual(site['networks:fw-a'], site['networks:fw-b'])

    def test_dual_firewalls_in_one_site_are_not_split(self):
        nodes = [
            node('fw-1', role='firewall', tier=0),
            node('fw-2', role='firewall', tier=0),
            node('core', role='core_switch', tier=1),
        ]
        components, _ = site_components(
            nodes,
            [link('fw-1', 'fw-2'), link('fw-1', 'core'), link('fw-2', 'core')],
            {},
        )

        # 主备防火墙之间的心跳线被摘掉，但它们各自接核心，仍属同一场地。
        self.assertEqual(len(components), 1)

    def test_orphan_joins_a_site_by_its_ip_subnet(self):
        nodes = [
            node('fw', role='firewall', tier=0),
            node('core', role='core_switch', tier=1),
            node('lonely', role='access_switch', tier=3),
        ]
        nodes[2]['_subnet'] = '10.4'
        nodes[1]['_subnet'] = '10.4'

        components, _ = site_components(nodes, [link('fw', 'core')], {})

        self.assertEqual(len(components), 1)
        self.assertIn('networks:lonely', components[0])
