"""Startup and readiness remain local even with broken DNS/trust-store setup."""
import io
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import manage
from local_http import LocalHTTPServer, local_opener


class LocalTransportTests(unittest.TestCase):
    def test_health_without_reverse_dns_proxy_discovery_or_https_setup(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/health' else 404)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def log_message(self, *_):
                pass
        with mock.patch('socket.getfqdn', side_effect=AssertionError('Reverse DNS is unavailable')), mock.patch('urllib.request.getproxies', side_effect=AssertionError('Proxy discovery is unavailable')), mock.patch('ssl._create_default_https_context', side_effect=AssertionError('Trust store is unavailable')):
            server = LocalHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                opener = local_opener()
                url = f'http://127.0.0.1:{server.server_port}'
                with opener.open(url + '/health', timeout=1) as response:
                    self.assertEqual(response.read(), b'{"ok":true}')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    opener.open(url + '/missing', timeout=1)
                self.assertEqual(error.exception.code, 404)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_request_forwards_readiness_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cfg = bridge.initialize(home)
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(b'{"ok":true}')
            with mock.patch.object(bridge, 'local_opener', return_value=opener):
                self.assertTrue(bridge.request(home, cfg, '/health', timeout=.2)['ok'])
            self.assertEqual(opener.open.call_args.kwargs['timeout'], .2)

    def test_unready_daemon_has_bounded_startup_and_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            clock = [0.0]
            def sleep(seconds):
                clock[0] += seconds
            process = mock.Mock()
            process.poll.return_value = None
            with mock.patch.object(manage, 'locked', return_value=False), mock.patch.object(manage.subprocess, 'Popen', return_value=process), mock.patch.object(manage, 'status', side_effect=OSError('Not listening')) as status, mock.patch.object(manage.time, 'monotonic', side_effect=lambda: clock[0]), mock.patch.object(manage.time, 'sleep', side_effect=sleep):
                with self.assertRaisesRegex(ValueError, 'failed to become ready'):
                    manage.start(home, {}, timeout=.3)
            self.assertAlmostEqual(clock[0], .3)
            self.assertTrue(status.called)
            self.assertTrue(all(0 < call.kwargs['timeout'] <= .3 for call in status.call_args_list))
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=10)


if __name__ == '__main__':
    unittest.main()
