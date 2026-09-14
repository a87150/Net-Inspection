from subprocess import CompletedProcess
from unittest.mock import patch

from django.test import SimpleTestCase

from net.infrastructure.reachability import ping_host


class ReachabilityTests(SimpleTestCase):
    @patch('net.infrastructure.reachability.subprocess.run')
    @patch('net.infrastructure.reachability.platform.system', return_value='Linux')
    def test_linux_ping_uses_argument_vector_with_bounded_timeout(self, system, run):
        run.return_value = CompletedProcess([], 0)

        result = ping_host('camera.example', timeout=4)

        self.assertEqual(result, (True, 'ICMP reply'))
        run.assert_called_once_with(
            ['ping', '-c', '1', '-W', '4', 'camera.example'],
            stdin=-3, stdout=-3, stderr=-3, timeout=6, check=False,
        )

    def test_invalid_target_is_rejected_before_subprocess(self):
        with patch('net.infrastructure.reachability.subprocess.run') as run:
            with self.assertRaises(ValueError):
                ping_host('example.com; shutdown /s')
        run.assert_not_called()