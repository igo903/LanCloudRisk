import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('risklens', Path(__file__).resolve().parent.parent / 'run_risklens_local.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


class QuoteConnection(unittest.TestCase):
    def test_real_refused_loopback_proxy_recovers_with_a_clean_direct_request(self):
        class Feed(app.BaseHTTPRequestHandler):
            def do_GET(self):
                assert self.path == '/quote' and self.headers.get('Proxy-Authorization') is None
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'quote fixture')

        with app.ThreadingHTTPServer(('127.0.0.1', 0), Feed) as feed, socket.socket() as offline_proxy:
            offline_proxy.bind(('127.0.0.1', 0))  # Reserved port with no listener.
            url = f'http://127.0.0.1:{feed.server_port}/quote'
            proxy = f'http://test:secret@127.0.0.1:{offline_proxy.getsockname()[1]}'
            thread = threading.Thread(target=feed.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {'http_proxy': proxy}, clear=True):
                    opener = app.urllib.request.build_opener()
                    with patch.object(app.urllib.request, 'urlopen', opener.open), patch.object(app, 'PROXY_MODE', 'system'):
                        with self.assertRaisesRegex(ConnectionError, '本机代理 127.0.0.1') as error:
                            app.fetch(url)
                        self.assertNotIn('secret', str(error.exception))
                    with patch.object(app.urllib.request, 'urlopen', opener.open), patch.object(app, 'PROXY_MODE', 'auto'):
                        self.assertEqual(app.fetch(url), b'quote fixture')
            finally:
                feed.shutdown()
                thread.join()

    def test_provider_failure_uses_sina_quote_with_its_original_units_and_timestamp(self):
        fields = ['光迅科技', '10', '9', '11', '12', '9', '0', '0', '1234', '12345'] + ['0'] * 20 + ['2026-10-30', '13:20:00']
        raw = ('var hq_str_sz002281="' + ','.join(fields) + '";').encode('gb18030')
        refused = app.urllib.error.URLError(OSError(61, 'Connection refused'))
        self.assertTrue(app.connection_refused(refused))
        with patch.object(app, 'fetch', side_effect=[refused, raw]) as fetch:
            quote = app.load_cn_quotes(['sz002281'])['sz002281']
        self.assertEqual(fetch.call_args.args[0], app.SINA_QUOTE + 'sz002281')
        self.assertEqual((quote['code'], quote['name'], quote['volume'], quote['source']),
                         ('002281', '光迅科技', 1234, '新浪公开行情'))
        self.assertEqual(quote['timestamp'], '2026-10-30T13:20:00+08:00')
        self.assertIsNone(quote['turnover'])

    def test_a_share_outage_keeps_old_records_and_allows_external_prices_to_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'market-cache.json'
            old = {'as_of': '2026-11-06', 'bars': [{'date': '2026-11-06', 'completed': False}]}
            with patch.object(app, 'cache_locations', return_value=[path]):
                store = app.DataStore(path)
                store.commit_snapshot({'data_kind': 'local', 'fetched_at': '2026-11-06T13:29:51+08:00',
                                       'catalog': store.catalog_view(), 'instruments': {'002281': old}})
                external = {'symbol': 'usQQQ', 'price': 99}
                with patch.object(app, 'load_cn_quotes', side_effect=ConnectionError('报价连接被拒绝')), \
                     patch.object(app, 'load_external', return_value=[external]), patch.object(app, 'load_geopolitics', return_value={}):
                    store.refresh()
            self.assertEqual(store.snapshot['instruments']['002281'], old)
            self.assertEqual(store.snapshot['external'][0], external)
            self.assertIn('报价连接被拒绝', store.state['errors']['A股初始报价'])


if __name__ == '__main__':
    unittest.main()
