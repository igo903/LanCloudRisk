import importlib.util
import io
from email.message import Message
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

    def test_partial_sources_query_only_uncovered_symbols_and_keep_attempts(self):
        a, b, c = 'sz002281', 'sh601869', 'sh588080'
        stamp = '2026-10-30T13:20:00+08:00'
        quotes = {s: {'code': s[2:], 'name': s, 'timestamp': stamp, 'price': 10} for s in (a, b, c)}
        issues = {}
        with patch.object(app, 'fetch', return_value=b'fixture') as fetch, \
             patch.object(app, 'parse_quotes', return_value={a: quotes[a]}), \
             patch.object(app, 'parse_sina_quotes', return_value={b: quotes[b]}), \
             patch.object(app, 'load_ifind_quotes', return_value={c: quotes[c]}) as ifind:
            self.assertEqual(app.load_cn_quotes([a, b, c], issues=issues), quotes)
        self.assertEqual(fetch.call_args.args[0], app.SINA_QUOTE + b + ',' + c)
        self.assertEqual(ifind.call_args.args[0], [c])
        self.assertEqual(list(issues), ['腾讯报价', '新浪报价'])

    def test_ex_dividend_prefix_and_truncation_preserve_identity(self):
        symbol = 'sh601869'
        quote = {'code': '601869', 'name': 'XD长飞光', 'timestamp': '2026-11-10T15:00:00+08:00'}
        with patch.object(app, 'now', return_value='2026-11-10T16:00:00+08:00'), \
             patch.object(app, 'fetch', return_value=b'fixture'), \
             patch.object(app, 'parse_quotes', return_value={symbol: quote}):
            result = app.load_cn_quotes([symbol], minimum={symbol: {'name': '长飞光纤'}})[symbol]
        self.assertEqual((result['name'], result['provider_name']), ('长飞光纤', 'XD长飞光'))
        self.assertEqual(app.canonical_quote_name({'name': 'XD长光华'}, '长飞光纤')['name'], 'XD长光华')

    def test_ifind_json_rpc_uses_distinct_stock_fund_tools_and_preserves_volume_basis(self):
        def provider(service, token, method, request_id=None, params=None, **kwargs):
            if method == 'initialize':
                return {'protocolVersion': '2025-03-26'}, 'fixture-session'
            if method == 'notifications/initialized':
                return {}, None
            self.assertEqual(kwargs['session'], 'fixture-session')
            self.assertEqual(params['name'], service + '_highfreq_quotes')
            self.assertEqual(params['arguments']['data_mode'], 'real_time')
            fields = {'证券代码': params['arguments']['symbols'], '证券简称': '测试证券',
                      '行情时间': '2026-10-30 13:20:00', '最新价': 10, '开盘价': 9,
                      '最高价': 11, '最低价': 8, '成交量': 123}
            if service == 'fund':
                fields['成交量单位'] = '份'
            return {'structuredContent': {'rows': [fields]}}, None
        with patch.object(app, 'ifind_token', return_value='fixture-token'), \
             patch.object(app, 'ifind_post', side_effect=provider), patch.object(app.time, 'sleep'):
            quotes = app.load_ifind_quotes(['sz002281', 'sh588080'], [])
        self.assertIsNone(quotes['sz002281']['volume'])
        self.assertIn('缺少完整', app.quote_bar_error(quotes['sz002281']))
        self.assertEqual(quotes['sh588080']['volume'], 123)
        self.assertIsNone(app.quote_bar_error(quotes['sh588080']))

    def test_ifind_mcp_accepts_the_matching_event_stream_response(self):
        stream = io.BytesIO(b'data: {"jsonrpc":"2.0","method":"notifications/message"}\n\n'
                            b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}\n\n')
        stream.headers = Message()
        stream.headers['Content-Type'] = 'text/event-stream'
        stream.headers['Mcp-Session-Id'] = 'test-session'
        with patch.object(app, 'PROXY_MODE', 'system'), patch.object(app.IFIND_OPENER, 'open', return_value=stream) as send:
            result = app.ifind_post('stock', 'test-token', 'tools/call', 2, {}, session='test-session')
        request = send.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'test-token')
        self.assertEqual(request.get_header('Mcp-session-id'), 'test-session')
        self.assertEqual(result, ({'content': []}, 'test-session'))

    def test_all_sources_fail_with_named_errors_and_keep_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'market-cache.json'
            with patch.object(app, 'cache_locations', return_value=[path]), \
                 patch.object(app, 'fetch', side_effect=ConnectionError('连接被拒绝')), \
                 patch.object(app, 'load_external', return_value=[]), patch.object(app, 'load_geopolitics', return_value={}):
                store = app.DataStore(path)
                old = store.snapshot
                store.refresh()
        self.assertEqual(store.state['price_result'], 'failed')
        self.assertIs(store.snapshot, old)
        self.assertEqual(list(store.state['quote_sources']['初始报价']), ['腾讯报价', '新浪报价', '同花顺 iFinD 报价'])
        self.assertIn('未配置', store.state['quote_sources']['初始报价']['同花顺 iFinD 报价'])

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
