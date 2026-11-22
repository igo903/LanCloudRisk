import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('priority_server', Path(__file__).resolve().parents[1] / 'local/server.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)
US = {'code': 'US.AAPL', 'symbol': 'AAPL', 'name': '苹果', 'market': 'US', 'currency': 'USD', 'type': 'stock'}


def quote(symbol, name='ETF'):
    return {'code': symbol[2:], 'name': name, 'price': 10, 'date': '2026-11-20',
            'timestamp': '2026-11-20T15:00:00+08:00', 'open': 10, 'high': 11, 'low': 9, 'volume': 100}


def daily(day='2026-11-20'):
    return [{'date': day, 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'vol': 100, 'completed': True}]


class SourcePriorityTests(unittest.TestCase):
    def test_all_requested_etfs_stop_at_pingan(self):
        symbols = ['sz159338', 'sz159915', 'sz159919', 'sz159901', 'sz159922', 'sh588080', 'sh515980']
        with patch.object(app, 'load_pingan_quotes', return_value={s: quote(s) for s in symbols}) as pingan, \
             patch.object(app, 'load_ifind_quotes') as ifind, patch.object(app, 'fetch') as public:
            result = app.load_cn_quotes(symbols)
        self.assertEqual(set(result), set(symbols))
        self.assertEqual(pingan.call_args.args[0], symbols)
        ifind.assert_not_called()
        public.assert_not_called()

    def test_etf_partial_fallback_requests_only_uncovered_symbols_in_order(self):
        a, b, c = 'sz159338', 'sz159915', 'sz159919'
        trace = []
        with patch.object(app, 'load_pingan_quotes', return_value={a: quote(a)}), \
             patch.object(app, 'load_ifind_quotes', return_value={b: quote(b)}) as ifind, \
             patch.object(app, 'parse_quotes', return_value={c: quote(c)}), \
             patch.object(app, 'fetch', return_value=b'fixture') as fetch:
            result = app.traced_quote_call(trace.append, '报价', 'ETF', app.load_cn_quotes, [a, b, c])
        self.assertEqual(ifind.call_args.args[0], [b, c])
        self.assertEqual(fetch.call_args.args[0], app.QUOTE + c)
        self.assertEqual(set(result), {a, b, c})
        self.assertEqual([(r['provider'], r['status']) for r in trace],
                         [('平安证券报价', 'partial'), ('同花顺 iFinD 报价', 'partial'), ('腾讯报价', 'ok')])

    def test_missing_timestamp_never_becomes_a_current_pingan_quote(self):
        item = {'code': 'SZ159338', 'name': 'ETF', 'price': 10, 'volume': 100}
        failures = []
        with patch.object(app, 'pingan_json', return_value={'items': [item]}):
            self.assertEqual(app.load_pingan_quotes(['sz159338'], failures), {})
        self.assertIn('行情时间', failures[0])
        with patch.object(app, 'pingan_json', return_value={'items': [{**item, 'timestamp': '2026-11-20T14:30:00+08:00'}]}):
            result = app.load_pingan_quotes(['sz159338'], [])['sz159338']
        self.assertEqual((result['volume'], result['timestamp']), (100, '2026-11-20T14:30:00+08:00'))

    def test_pingan_batches_at_five_and_retains_successful_batches(self):
        symbols = ['sz' + str(159330 + i) for i in range(6)]
        item = {'code': symbols[0].upper(), 'name': 'ETF', 'price': 10, 'timestamp': '2026-11-20T14:30:00+08:00'}
        with patch.object(app, 'pingan_json', side_effect=[{'items': [item]}, ValueError('平安证券 HTTP 429')]) as send:
            failures = []
            result = app.load_pingan_quotes(symbols, failures)
        self.assertEqual(set(result), {symbols[0]})
        self.assertEqual([len(c.args[1]['codes']) for c in send.call_args_list], [5, 1])
        self.assertIn('429', failures[0])

    def test_pingan_daily_preserves_its_declared_adjustment_and_share_units(self):
        payload = {'items': [{'code': 'SZ159338', 'period': 'day', 'adjust': 'qfq',
                              'bars': [{'date': '2026-11-20', 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 100}]}]}
        with patch.object(app, 'pingan_json', return_value=payload) as send:
            bars, url, adjustment, units = app.pingan_daily('sz159338')
        self.assertEqual((adjustment, units, bars[0]['vol']), ('qfq', '股/份', 100))
        self.assertEqual(send.call_args.args, (app.PINGAN_KLINE, {'code': 'SZ159338', 'last_n': 640}))
        payload['items'][0]['code'] = 'SZ159919'
        with patch.object(app, 'pingan_json', return_value=payload), self.assertRaisesRegex(ValueError, '同一标的'):
            app.pingan_daily('sz159338')

    def test_etf_history_fallback_and_stale_history_rejection(self):
        trace = []
        symbol = 'sz159338'
        row = quote(symbol)
        tencent = {'data': {symbol: {'day': [['2026-11-20', '10', '10', '11', '9', '100']]}}}
        with patch.object(app, 'pingan_daily', return_value=(daily('2026-11-19'), 'pingan', 'qfq', '股/份')), \
             patch.object(app, 'ifind_daily', side_effect=ValueError('同花顺无日线')), \
             patch.object(app, 'fetch', return_value=json.dumps(tencent).encode()) as fetch:
            _, record = app.traced_quote_call(trace.append, '日线', symbol, app.load_asset,
                                              ('159338', 'ETF', None), {symbol: row}, {'as_of': '2026-11-20'})
        self.assertEqual(record['history_provider'], '腾讯')
        self.assertIn('gtimg.cn', fetch.call_args.args[0])
        self.assertEqual([r['provider'] for r in trace], ['平安证券日线', '同花顺 iFinD 日线', '腾讯日线'])
        self.assertIn('早于缓存', trace[0]['detail'])

    def test_ifind_parses_structured_daily_table_and_rejects_nav_and_unknown_units(self):
        columns = ['证券代码', '日期', '开盘价', '最高价', '最低价', '收盘价', '成交量', '成交量单位', '复权方式']
        values = ['159338.SZ', '2026-11-20', 10, 11, 9, 10, 100, '份', '不复权']
        result = {'content': [{'type': 'text', 'text': json.dumps({'tables': [columns, values]})}]}
        with patch.object(app, 'ifind_tool', return_value=result) as tool:
            bars, _, adjustment, _ = app.ifind_daily('sz159338')
        self.assertEqual((bars[0]['vol'], adjustment), (100, 'none'))
        self.assertEqual(tool.call_args.args[:2], ('fund', 'get_fund_market_performance'))
        for key, value in [('成交量单位', '未知'), ('复权方式', None), ('证券代码', '159919.SZ'), ('收盘价', None)]:
            row = dict(zip(columns, values)); row[key] = value
            with self.subTest(key=key), patch.object(app, 'ifind_tool', return_value={'rows': [row]}):
                with self.assertRaises(ValueError):
                    app.ifind_daily('sz159338')

    def test_short_pingan_history_does_not_shrink_an_existing_research_window(self):
        rows = daily('2026-11-19') + daily('2026-11-20')
        previous = {'as_of': '2026-11-20', 'bars': rows}
        symbol = 'sz159338'
        with patch.object(app, 'pingan_daily', return_value=(daily(), 'pingan', 'qfq', '股/份')), \
             patch.object(app, 'ifind_daily', return_value=(rows, 'ifind', 'none', '股/份')), \
             patch.object(app, 'fetch') as public:
            _, record = app.load_asset(('159338', 'ETF', None), {symbol: quote(symbol)}, previous)
        self.assertEqual(record['history_provider'], '同花顺 iFinD')
        self.assertEqual(len(record['bars']), 2)
        self.assertIn('仅返回 1 根日线', record['fallback_errors'][0])
        public.assert_not_called()

    def test_malformed_pingan_payload_is_a_named_error(self):
        with patch.object(app, 'authenticated_market_json', return_value={'code': 0, 'data': []}):
            with self.assertRaisesRegex(ValueError, '无效行情数据结构'):
                app.pingan_json(app.PINGAN_QUOTE, {'codes': ['SZ159338']}, 'fixture-key')

    def test_ifind_column_arrays_inherit_security_identity(self):
        result = {'data': [{'thscode': 'AAPL.O', 'adjust': 'none', 'volume_unit': 'shares', 'table': {
            'time': ['2026-11-19', '2026-11-20'], 'open': [10, 10], 'high': [11, 11],
            'low': [9, 9], 'close': [10, 10], 'volume': [100, 200]}}]}
        with patch.object(app, 'ifind_tool', return_value=result):
            bars, _, _, _ = app.ifind_daily('AAPL', 'US')
        self.assertEqual([r['vol'] for r in bars], [100, 200])

    def test_us_history_attempts_five_sources_and_stops_at_first_valid_source(self):
        methods = ('load_futu_stock', 'load_tencent_stock', 'read_yahoo_chart', 'load_ifind_stock', 'load_massive_stock')
        patches = []
        for name in methods:
            p = patch.object(app, name, side_effect=ValueError(name + ' unavailable'))
            patches.append(p.start()); self.addCleanup(p.stop)
        patches[-1].side_effect = None
        patches[-1].return_value = app.daily_stock_record(US, daily(), 'fixture', 'America/New_York', 'Massive', 'fixture', 'provider', '')
        trace = []
        _, record = app.traced_quote_call(trace.append, '美股', 'US.AAPL', app.load_international_asset, US)
        self.assertEqual([r['provider'] for r in trace], ['富途', '腾讯', 'Yahoo Finance', '同花顺 iFinD', 'Massive'])
        self.assertEqual(record['history_provider'], 'Massive')
        patches[1].side_effect = None
        patches[1].return_value = {**patches[-1].return_value, 'history_provider': '腾讯'}
        for mocked in patches:
            mocked.reset_mock()
        _, record = app.load_international_asset(US)
        self.assertEqual(record['history_provider'], '腾讯')
        for mocked in patches[2:]:
            mocked.assert_not_called()

    def test_us_search_and_add_verify_use_tencent_before_yahoo(self):
        with patch.object(app, 'search_futu_securities', side_effect=ValueError('富途 unavailable')), \
             patch.object(app, 'search_tencent_securities', return_value=[US]), \
             patch.object(app, 'search_yahoo_securities') as yahoo, patch.object(app, 'SEARCH_CACHE', {}):
            self.assertEqual(app.search_securities('AAPL', 'US'), [US])
            yahoo.assert_not_called()
        with patch.object(app, 'resolve_futu_security', side_effect=ValueError('富途 unavailable')), \
             patch.object(app, 'resolve_tencent_security', return_value=dict(US)), \
             patch.object(app, 'resolve_yahoo_security') as yahoo:
            self.assertEqual(app.resolve_security('US', 'AAPL')['code'], US['code'])
            yahoo.assert_not_called()

    def test_a50_and_usdcnh_use_futu_verified_codes_without_derivative_substitution(self):
        for symbol, code, name, kind in [('A50', 'GLOBAL.TESTA50', '富时中国A50指数', 'IDX'),
                                          ('CNH', 'FX.USDCNH', '美元/离岸人民币', 'FOREX')]:
            row = {'code': code, 'sc_name': name, 'sec_type': kind}
            with self.subTest(symbol=symbol), patch.object(app, 'futu_json', return_value={'data': {'items': [row]}}), \
                 patch.object(app, 'futu_daily', return_value=(daily(), 'futu')) as load, \
                 patch.object(app, 'fetch') as public:
                asset = next(a for a in app.GLOBAL_ASSETS if a[0] == symbol)
                result = app.load_global_asset(asset)
                self.assertEqual(result['provider'], '富途')
                load.assert_called_once_with(code, 'Asia/Shanghai', adjusted=False)
                public.assert_not_called()
            with patch.object(app, 'futu_json', return_value={'data': {'items': [{**row, 'sec_type': 'FUTURE'}]}}):
                with self.assertRaises(app.QuoteUnavailable):
                    app.futu_exact_external(symbol)

    def test_forex_rejects_onshore_or_reverse_pair(self):
        for code in ('FX.USDCNY', 'FX.CNHUSD'):
            with patch.object(app, 'futu_json', return_value={'data': {'items': [
                    {'code': code, 'sc_name': '美元/离岸人民币', 'sec_type': 'FOREX'}]}}):
                with self.assertRaises(app.QuoteUnavailable):
                    app.futu_exact_external('CNH')

    def test_tencent_uses_verified_exchange_suffix_and_keeps_today_provisional(self):
        current = app.dt.datetime.now(app.dt.timezone.utc)
        today = app.quote_day(current, 'America/New_York').isoformat()
        qt = [''] * 60; qt[0], qt[2], qt[35] = 'delay', 'AAPL.OQ', 'USD'
        symbol = 'usAAPL.OQ'
        payload = {'data': {symbol: {'qfqday': [[today, '10', '10', '11', '9', '100']], 'qt': {symbol: qt}}}}
        with patch.object(app, 'resolve_tencent_security', return_value={**US, 'tencent_symbol': symbol}), \
             patch.object(app, 'fetch', return_value=json.dumps(payload).encode()) as fetch:
            record = app.load_tencent_stock(US)
        self.assertIn('usAAPL.OQ', fetch.call_args.args[0])
        self.assertFalse(record['completed'])
        self.assertIn('延迟', record['source'])
        qt[2] = 'MSFT.OQ'
        with patch.object(app, 'fetch', return_value=json.dumps(payload).encode()), self.assertRaisesRegex(ValueError, '身份不匹配'):
            app.load_tencent_stock({**US, 'tencent_symbol': symbol})


if __name__ == '__main__':
    unittest.main()
