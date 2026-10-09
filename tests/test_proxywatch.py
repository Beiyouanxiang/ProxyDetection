import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxywatch as p


class NetworkTests(unittest.TestCase):
    def test_geo_rejects_html(self):
        with self.assertRaises(ValueError):
            p.parse_geo('<html>\nblocked\nAS1\nserver')

    def test_geo_accepts_ipv6(self):
        self.assertEqual(p.parse_geo('2001:db8::1\nplace\nAS123\nISP')['ip'], '2001:db8::1')

    def test_socks_dns_stays_on_proxy(self):
        self.assertEqual(p.validate_proxy('socks5://127.0.0.1:1080'), 'socks5h://127.0.0.1:1080')

    def test_missing_system_proxy_never_uses_direct(self):
        with patch.object(p, 'system_proxy', side_effect=ValueError('missing')), patch.object(p.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                p.request('https://example.com', p.DEFAULTS)
            run.assert_not_called()

    def test_explicit_proxy_ignores_bypass(self):
        result = subprocess.CompletedProcess([], 0, b'\n__PW__204|0.25', b'')
        config = dict(p.DEFAULTS, mode=p.MODES[1], proxy='socks5://127.0.0.1:1080')
        with patch.object(p, 'curl_path', return_value='curl'), patch.dict(os.environ, {'NO_PROXY': '*', 'HTTPS_PROXY': 'http://wrong:80'}), patch.object(p.subprocess, 'run', return_value=result) as run:
            self.assertEqual(p.request(config['target'], config)[1:], (204, 250.0))
            args = run.call_args.args[0]
            self.assertEqual(args[args.index('--proxy') + 1], 'socks5h://127.0.0.1:1080')
            self.assertEqual(args[args.index('--noproxy') + 1], '')
            self.assertNotIn('NO_PROXY', run.call_args.kwargs['env'])

    def test_tun_disables_application_proxy(self):
        result = subprocess.CompletedProcess([], 0, b'\n__PW__204|0.25', b'')
        with patch.object(p, 'curl_path', return_value='curl'), patch.object(p.subprocess, 'run', return_value=result) as run:
            p.request(p.DEFAULTS['target'], dict(p.DEFAULTS, mode=p.MODES[2]))
            args = run.call_args.args[0]
            self.assertEqual(args[args.index('--proxy') + 1], '')
            self.assertEqual(args[args.index('--noproxy') + 1], '*')

    def test_curl_failure_is_not_success_and_hides_credentials(self):
        result = subprocess.CompletedProcess([], 28, b'', b'timeout proxy user:secret')
        with patch.object(p, 'curl_path', return_value='curl'), patch.object(p.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, '请求超时'):
                p.request(p.DEFAULTS['target'], dict(p.DEFAULTS, mode=p.MODES[2]))


class UITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(p, 'load_config', return_value=p.DEFAULTS.copy()), patch.object(p.Monitor, 'start'):
            self.app = p.Widget(self.root)

    def tearDown(self):
        self.app.close()

    def event(self, kind, ok, data, generation=0):
        self.app.monitor.events.put((generation, kind, ok, data, time.time()))
        self.app.tick()
        self.root.update_idletasks()

    def test_ip_mismatch_is_red(self):
        self.app.config['expected_ip'] = '1.1.1.1'
        self.event('geo', True, dict(ip='8.8.8.8', location='place', asn='AS1', org='ISP'))
        self.event('latency', True, dict(ms=20))
        self.assertEqual(self.app.status.cget('fg'), '#f87171')

    def test_ip_failure_keeps_last_result_but_not_green(self):
        self.event('geo', True, dict(ip='1.1.1.1', location='place', asn='AS1', org='ISP'))
        self.event('latency', True, dict(ms=20))
        self.event('geo', False, 'timeout')
        self.assertEqual(self.app.geo['ip'], '1.1.1.1')
        self.assertEqual(self.app.status.cget('fg'), '#fbbf24')

    def test_stale_generation_is_ignored(self):
        self.app.monitor.update(p.DEFAULTS)
        self.event('latency', True, dict(ms=20), generation=0)
        self.assertIsNone(self.app.latency_ok)

    def test_settings_load_and_buttons_fit(self):
        self.root.deiconify()
        self.root.update_idletasks()
        self.app.settings()
        self.root.update_idletasks()
        self.assertTrue(self.app.settings_window.winfo_exists())
        last = self.root.winfo_children()[-1]
        # Find the main button frame, excluding the settings Toplevel.
        frame = self.root.winfo_children()[-2]
        self.assertLessEqual(frame.winfo_y() + frame.winfo_height(), self.root.winfo_height())


if __name__ == '__main__':
    unittest.main()
