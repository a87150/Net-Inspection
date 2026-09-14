from django.test import SimpleTestCase
from net.devices.pc.matching import match_person


class NameDescriptionTests(SimpleTestCase):
    def test_all_name_sources_and_both_sides_ignore_description(self):
        roster = [{'id': '1', 'employee_id': 'H-001', 'name': '测试乙-人员备注'}]
        for field in ('当前登录用户姓名', '当前登录用户名', '姓名'):
            with self.subTest(field=field):
                self.assertEqual(match_person({field: ' 测试乙 -示例客服部门-备注 '}, roster), (roster[0], 'matched'))
        self.assertEqual(roster[0]['name'], '测试乙-人员备注')
        self.assertEqual(match_person({'当前登录用户工号': 'H-001'}, roster), (roster[0], 'matched'))
        self.assertEqual(match_person({'当前登录用户工号': 'OTHER', '姓名': '测试乙-备注'}, roster), (None, 'unmatched'))
        self.assertEqual(match_person({'姓名': '-备注'}, roster), (None, 'missing'))
        self.assertEqual(match_person({'姓名': '测试乙-备注'}, roster + [{'id': '2', 'employee_id': 'H-002', 'name': '测试乙'}]), (None, 'ambiguous'))
