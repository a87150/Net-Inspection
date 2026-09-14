from django.test import TestCase

from net.models import People
from tests.devices.pc.test_remote_rules import RemoteRuleFixture


class IdentityReferenceTests(RemoteRuleFixture, TestCase):
    def test_trailing_name_numbers_are_ignored_with_matching_computer(self):
        People.objects.create(employee_id='TEST046937', name='测试丙')
        system = self.payloads['windows']['系统信息概览']
        system.update({'计算机名': 'TEST046937', '当前登录用户工号': None})
        for name in ('测试丙1', '测试丙123', ' 测试丙１ ', '测试丙 2'):
            with self.subTest(name=name):
                system['当前登录用户姓名'] = name
                detail = self.analyze(['identity_match']).details['identity_match']
                self.assertEqual(detail['data_state'], 'known')
                self.assertEqual(detail['matched_fields'], ['姓名', '计算机名'])
                self.assertEqual(detail['reference_employee_id'], 'TEST046937')
                self.assertEqual(detail['evidence']['当前登录用户姓名'], name)

    def test_name_normalization_does_not_remove_middle_digits_or_change_employee_ids(self):
        for name, computer in (('测1试甲', 'TEST026685'), ('测试甲1', 'TEST0266851'), ('123', 'TEST026685')):
            with self.subTest(name=name, computer=computer):
                self.payloads['windows']['系统信息概览'].update({
                    '当前登录用户姓名': name, '计算机名': computer, '当前登录用户工号': None,
                })
                detail = self.analyze(['identity_match']).details['identity_match']
                self.assertNotEqual(detail['data_state'], 'known')

    def test_name_suffix_is_ignored_with_login_pair_and_frozen_roster(self):
        self.payloads['windows']['系统信息概览'].update({
            '当前登录用户姓名': '测试甲12', '计算机名': 'OTHER-PC', '当前登录用户工号': 'DOMAIN\\TEST026685',
        })
        roster = [{'id': 'snapshot-person', 'employee_id': 'TEST026685', 'name': '测试甲1', 'department': '', 'is_active': True}]
        detail = self.analyze(['identity_match'], rules={'personnel_roster': roster}).details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(detail['matched_fields'], ['姓名', '当前登录用户工号'])

    def test_hyphen_description_is_ignored_without_changing_evidence(self):
        original = ' 测试乙-示例客服部门-其他说明 '
        self.payloads['windows']['系统信息概览'].update({
            '当前登录用户姓名': original, '计算机名': 'TEST026685', '当前登录用户工号': None,
        })
        roster = [{'id': 'snapshot-person', 'employee_id': 'TEST026685', 'name': '测试乙-人员备注', 'department': '', 'is_active': True}]
        detail = self.analyze(['identity_match'], rules={'personnel_roster': roster}).details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(detail['matched_fields'], ['姓名', '计算机名'])
        self.assertEqual(detail['evidence']['当前登录用户姓名'], original)

    def setUp(self):
        super().setUp()
        People.objects.create(employee_id='TEST026685', name='测试甲')
        self.payloads['windows']['系统信息概览'].update({
            '计算机名': 'TEST026685', '当前登录用户姓名': '测试甲',
            '当前登录用户工号': None,
        })

    def test_name_and_computer_match_without_login_identifier(self):
        result = self.analyze(['identity_match'])
        self.assertEqual(result.details['identity_match']['data_state'], 'known')
        self.assertEqual(result.result_level, 'normal')
        self.assertEqual(result.details['identity_match']['match_basis'], 'person_name_and_computer')

    def test_name_and_computer_are_sufficient_even_with_different_login(self):
        self.payloads['windows']['系统信息概览']['当前登录用户工号'] = 'local-user'
        self.assertEqual(self.analyze(['identity_match']).result_level, 'normal')

    def test_same_name_on_other_employee_does_not_block_exact_pair(self):
        People.objects.create(employee_id='H999999', name='测试甲')
        self.assertEqual(self.analyze(['identity_match']).result_level, 'normal')

    def test_wrong_name_does_not_match_by_computer_alone(self):
        self.payloads['windows']['系统信息概览']['当前登录用户姓名'] = '其他人'
        result = self.analyze(['identity_match'])
        self.assertNotEqual(result.details['identity_match']['data_state'], 'known')

    def test_frozen_empty_roster_does_not_fall_back_to_live_people(self):
        result = self.analyze(['identity_match'], rules={'personnel_roster': []})
        self.assertNotEqual(result.details['identity_match']['data_state'], 'known')

    def test_name_and_login_match_person_despite_unrelated_computer_name(self):
        People.objects.create(employee_id='TEST053930', name='测试丁')
        self.payloads['windows']['系统信息概览'].update({
            '计算机名': 'DEMO-OTHER-PC', '当前登录用户姓名': '测试丁',
            '当前登录用户工号': r'EXAMPLE\TEST053930',
        })
        self.payloads['windows']['计算机和用户匹配情况'] = '不匹配!'
        result = self.analyze(['identity_match'])
        detail = result.details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(result.result_level, 'normal')
        self.assertEqual(detail['reference_employee_id'], 'TEST053930')
        self.assertEqual(detail['matched_fields'], ['姓名', '当前登录用户工号'])
        self.assertEqual(detail['reported_match'], '不匹配!')

    def test_computer_and_login_pair_requires_same_person_but_not_name(self):
        self.payloads['windows']['系统信息概览'].update({
            '计算机名': 'test026685', '当前登录用户姓名': '其他人',
            '当前登录用户工号': 'TEST026685@example.test',
        })
        detail = self.analyze(['identity_match']).details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(detail.get('matched_fields'), ['计算机名', '当前登录用户工号'])
        self.assertEqual(detail.get('reference_employee_id'), 'TEST026685')

    def test_equal_computer_and_login_without_person_does_not_pass(self):
        self.payloads['windows']['系统信息概览']['当前登录用户工号'] = 'TEST026685'
        detail = self.analyze(['identity_match'], rules={'personnel_roster': []}).details['identity_match']
        self.assertNotEqual(detail['data_state'], 'known')

    def test_two_qualifying_people_are_ambiguous_not_arbitrarily_selected(self):
        People.objects.create(employee_id='H999999', name='测试甲')
        self.payloads['windows']['系统信息概览']['当前登录用户工号'] = 'H999999'
        detail = self.analyze(['identity_match']).details['identity_match']
        self.assertEqual(detail['data_state'], 'unknown')
        self.assertEqual(detail.get('match_basis'), 'ambiguous_person')

    def test_frozen_roster_matches_name_login_instead_of_live_personnel(self):
        self.payloads['windows']['系统信息概览'].update({
            '计算机名': 'OTHER-PC', '当前登录用户姓名': '历史姓名',
            '当前登录用户工号': 'TEST026685',
        })
        detail = self.analyze(['identity_match'], rules={'personnel_roster': [
            {'id': 'snapshot-person', 'employee_id': 'TEST026685', 'name': '历史姓名', 'department': '', 'is_active': True},
        ]}).details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(detail.get('reference_person_name'), '历史姓名')

    def test_all_three_fields_match_same_person(self):
        self.payloads['windows']['系统信息概览']['当前登录用户工号'] = 'TEST026685'
        detail = self.analyze(['identity_match']).details['identity_match']
        self.assertEqual(detail['data_state'], 'known')
        self.assertEqual(detail['matched_fields'], ['姓名', '计算机名', '当前登录用户工号'])

    def test_fields_matching_three_different_people_cannot_be_combined(self):
        People.objects.create(employee_id='H111111', name='另一人甲')
        People.objects.create(employee_id='H222222', name='另一人乙')
        self.payloads['windows']['系统信息概览'].update({
            '计算机名': 'H111111', '当前登录用户工号': 'H222222',
        })
        detail = self.analyze(['identity_match']).details['identity_match']
        self.assertEqual(detail['data_state'], 'failed')
        self.assertEqual(detail['matched_fields'], [])
