"""Tests for the process-local CLI concurrency budget."""
import threading
import time

from django.test import SimpleTestCase, override_settings

from net.inspections import budget


class CliBudgetTests(SimpleTestCase):
    def setUp(self):
        budget.reset()

    def tearDown(self):
        budget.reset()

    def test_limit_reads_the_setting(self):
        with override_settings(NET_SSH_CONCURRENCY=3):
            self.assertEqual(budget.limit(), 3)

    def test_unusable_setting_falls_back_to_default(self):
        with override_settings(NET_SSH_CONCURRENCY='not-a-number'):
            self.assertEqual(budget.limit(), budget.DEFAULT_CONCURRENCY)
        with override_settings(NET_SSH_CONCURRENCY=0):
            self.assertEqual(budget.limit(), 1)

    def test_holders_never_exceed_the_limit(self):
        with override_settings(NET_SSH_CONCURRENCY=2):
            budget.reset()
            lock = threading.Lock()
            observed = {'current': 0, 'peak': 0}

            def hold():
                with budget.cli_budget():
                    with lock:
                        observed['current'] += 1
                        observed['peak'] = max(observed['peak'], observed['current'])
                    time.sleep(0.05)
                    with lock:
                        observed['current'] -= 1

            threads = [threading.Thread(target=hold) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertLessEqual(observed['peak'], 2)
            self.assertEqual(observed['peak'], 2)

    def test_waiting_holder_times_out(self):
        with override_settings(NET_SSH_CONCURRENCY=1):
            budget.reset()
            with budget.cli_budget():
                with self.assertRaises(budget.BudgetTimeout):
                    with budget.cli_budget(timeout=0.05):
                        pass

    def test_slot_is_released_after_an_error(self):
        with override_settings(NET_SSH_CONCURRENCY=1):
            budget.reset()
            with self.assertRaises(ValueError):
                with budget.cli_budget():
                    raise ValueError('boom')
            # A leaked slot would block this acquire until the wait timeout.
            with budget.cli_budget(timeout=0.5):
                pass
