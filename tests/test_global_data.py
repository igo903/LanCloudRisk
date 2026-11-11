import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import json

spec = importlib.util.spec_from_file_location('risk_server', Path(__file__).resolve().parents[1] / 'local' / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def fixture():
    names = ['DAY', 'GPRD', 'GPRD_ACT', 'GPRD_THREAT']
    tag = lambda n, b: b'<' + n + b'>' + b + b'</' + n + b'>'
    values = [('20261025', 100., 50., 200.), ('20261026', 120., 60., 230.)]
    body = b''.join(d.encode() + struct.pack('<fff', *v) for d, *v in values)
    return (tag(b'release', b'118') + tag(b'byteorder', b'LSF') + tag(b'K', (4).to_bytes(2, 'little')) +
            tag(b'N', (2).to_bytes(8, 'little')) + tag(b'variable_types', struct.pack('<HHHH', 8, 65527, 65527, 65527)) +
            tag(b'varnames', b''.join(n.encode().ljust(129, b'\0') for n in names)) + tag(b'data', body))


class GlobalDataTests(unittest.TestCase):
    def test_n_period_change_requires_n_plus_one_samples(self):
        rows = [{'date': f'd{i}', 'close': 100+i} for i in range(251)]
        result = app.external_changes(rows, 'equity')
        self.assertAlmostEqual(result['250']['value'], 250.)
        self.assertEqual(result['250']['start'], 'd0')
        self.assertAlmostEqual(result['60']['value'], (350/290-1)*100)
        self.assertIsNone(app.external_changes(rows[:250], 'equity')['250']['value'])

    def test_history_failure_preserves_previous_window_and_refreshes_last_price(self):
        history = [{'date': (app.dt.date(2025, 10, 1)+app.dt.timedelta(days=i)).isoformat(), 'close': 100+i} for i in range(251)]
        old = {'symbol': 'A50', 'ticker': 'XIN9.FGI', 'kind': 'equity', 'history': history}
        fresh = {'symbol': 'A50', 'ticker': 'XIN9.FGI', 'kind': 'equity', 'history': [{'date': history[-1]['date'], 'close': 360}], 'history_error': '503'}
        merged = app.merge_external([old], [fresh])[0]
        self.assertTrue(merged['retained_history'])
        self.assertEqual(merged['history'][-1]['close'], 360)
        self.assertEqual(merged['returns']['250']['value'], 260.)
        self.assertEqual(merged['history_error'], '503')

    def test_yield_is_bp_change_not_investment_return(self):
        rows = [{'date': str(i), 'close': 4.} for i in range(61)]
        rows[-1]['close'] = 4.5
        r = app.external_changes(rows, 'yield')['60']
        self.assertEqual(r['value'], 50.)
        self.assertEqual(r['unit'], 'bp')

    def test_stata_binary_is_parsed_without_pandas(self):
        rows = app.parse_gpr_stata(fixture())
        self.assertEqual(rows[-1]['date'], '2026-10-26')
        self.assertEqual(rows[-1]['GPRD_THREAT'], 230.)
        self.assertEqual(rows[-1]['GPRD_ACT'], 60.)

    def test_stata_schema_and_truncation_fail_closed(self):
        for raw in [fixture().replace(b'118', b'119', 1), fixture().replace(b'LSF', b'MSF', 1), fixture()[:-13], fixture().replace(b'GPRD_THREAT', b'NOT_A_FIELD')]:
            with self.assertRaises((ValueError, struct.error)):
                app.parse_gpr_stata(raw)

    def test_one_quote_does_not_manufacture_250_day_history(self):
        payload = {'status': 'DELAYED', 'ticker': 'SOXX', 'results': [{'t': 1793318400000, 'c': 100}]}
        with patch.object(app, 'massive_json', return_value=payload), patch.object(app, 'fetch', side_effect=ValueError('Yahoo HTTP 403')) as public:
            row = app.load_global_asset(next(r for r in app.GLOBAL_ASSETS if r[0] == 'usSOXX'))
        self.assertEqual(public.call_count, 2)
        self.assertEqual(row['provider'], 'Massive')
        self.assertIn('延迟', row['source'])
        self.assertEqual(len(row['history']), 1)
        self.assertIsNone(row['returns']['60']['value'])
        self.assertIsNone(row['returns']['250']['value'])

    def test_a50_keeps_the_cached_exact_index_when_sources_cannot_supply_it(self):
        quote = {'chart': {'result': [{'meta': {'symbol': 'XIN9.FGI'}, 'timestamp': [], 'indicators': {'quote': [{'close': []}]}}]}}
        old = {'symbol': 'A50', 'ticker': 'XIN9.FGI', 'kind': 'equity', 'provider': 'Yahoo Finance',
               'history_as_of': '2026-10-30', 'history': [{'date': '2026-10-30', 'close': 100}]}
        with patch.object(app, 'fetch', return_value=json.dumps(quote).encode()) as fetch:
            row = app.load_global_asset(next(r for r in app.GLOBAL_ASSETS if r[0] == 'A50'))
        self.assertEqual(fetch.call_count, 2)
        kept = app.merge_external([old], [row])[0]
        self.assertTrue(kept['retained'])
        self.assertEqual(kept['history'], old['history'])

    def test_yahoo_403_tries_the_second_chart_host(self):
        asset = next(r for r in app.GLOBAL_ASSETS if r[0] == 'DJI')
        blocked = app.urllib.error.HTTPError('https://query1.finance.yahoo.com', 403, 'Forbidden', None, None)
        payload = {'chart': {'result': [{'meta': {'symbol': '^DJI'}, 'timestamp': [1793318400],
                                        'indicators': {'quote': [{'close': [100]}]}}]}}
        with patch.object(app, 'fetch', side_effect=[blocked, json.dumps(payload).encode()]) as fetch:
            row = app.load_global_asset(asset)
        self.assertEqual(row['provider'], 'Yahoo Finance')
        self.assertIn('query2.finance.yahoo.com', fetch.call_args.args[0])
        self.assertTrue(any('403' in error for error in row['fallback_errors']))
        self.assertNotIn('error', row)

    def test_global_fallback_records_three_sources_in_order_and_checks_identity(self):
        asset = next(r for r in app.GLOBAL_ASSETS if r[0] == 'DJI')
        blocked = app.urllib.error.HTTPError('https://finance.yahoo.com', 403, 'Forbidden', None, None)
        payload = {'status': 'DELAYED', 'ticker': 'I:DJI', 'results': [{'t': 1793318400000, 'c': 102}]}
        attempts = []
        with patch.object(app, 'futu_daily', side_effect=app.QuoteUnavailable('未配置密钥')), \
             patch.object(app, 'fetch', side_effect=blocked), patch.object(app, 'massive_json', return_value=payload):
            row = app.traced_quote_call(attempts.append, '外围日线', 'DJI', app.load_global_asset, asset)
        self.assertEqual((row['provider'], row['price']), ('Massive', 102))
        self.assertEqual([(entry['provider'], entry['status']) for entry in attempts],
                         [('富途', 'skipped'), ('Yahoo Finance', 'failed'), ('Massive', 'ok')])
        self.assertIn('403', attempts[1]['detail'])
        with patch.object(app, 'fetch', side_effect=blocked), patch.object(app, 'massive_json', return_value={**payload, 'ticker': 'I:SOX'}):
            row = app.load_global_asset(asset)
        self.assertIn('身份不匹配', row['error'])

    def test_old_futu_index_history_falls_through_to_a_newer_source(self):
        asset = next(r for r in app.GLOBAL_ASSETS if r[0] == 'SOX')
        attempts = []
        old = [{'date': '2026-11-06', 'close': 100, 'completed': True}]
        newer = {'history': [{'date': '2026-11-09', 'close': 102}]}
        with patch.object(app, 'futu_daily', return_value=(old, app.FUTU_API)) as futu, \
             patch.object(app, 'read_yahoo_chart', return_value=(newer, 'https://finance.yahoo.com')):
            row = app.traced_quote_call(attempts.append, '外围日线', 'SOX', app.load_global_asset, asset, '2026-11-09')
        futu.assert_called_once_with('US..SOX', 'America/New_York', adjusted=False)
        self.assertEqual((row['provider'], row['price']), ('Yahoo Finance', 102))
        self.assertEqual([(entry['provider'], entry['status']) for entry in attempts],
                         [('富途', 'failed'), ('Yahoo Finance', 'ok')])
        self.assertIn('早于缓存日期', attempts[0]['detail'])

    def test_futu_daily_uses_official_date_pagination_and_stops_before_fallback(self):
        current = app.dt.datetime.fromisoformat('2026-11-10T12:00:00+00:00')
        def row(date, close):
            return {'date': date, 'open': close, 'high': close, 'low': close, 'close': close, 'volume': 100}
        pages = [
            {'data': {'kline_list': [row(20261110, 102)], 'next_time': 1779163199999},
             'pagination': {'has_more': True, 'next_page_token': 'a'*48}},
            {'data': {'kline_list': [row(20261109, 101)]}, 'pagination': {'has_more': False}},
        ]
        with patch.object(app, 'futu_json', side_effect=pages) as futu:
            bars, _ = app.futu_daily('HK.00700', 'Asia/Hong_Kong', current=current)
        self.assertEqual([(bar['date'], bar['completed']) for bar in bars],
                         [('2026-11-09', True), ('2026-11-10', False)])
        self.assertIn('page_token='+'a'*48, futu.call_args.args[0])
        with patch.object(app, 'futu_daily', return_value=(bars, app.FUTU_API)), patch.object(app, 'fetch') as yahoo:
            global_row = app.load_global_asset(next(r for r in app.GLOBAL_ASSETS if r[0] == 'hkHSTECH'))
        self.assertEqual(global_row['provider'], '富途')
        self.assertTrue(global_row['provisional'])
        yahoo.assert_not_called()

    def test_new_provider_history_is_not_blended_with_the_old_provider(self):
        old = {'symbol': 'GOLD', 'ticker': 'GC=F', 'kind': 'metal', 'provider': 'Yahoo Finance',
               'history_as_of': '2026-10-30', 'history': [{'date': '2026-10-29', 'close': 90}, {'date': '2026-10-30', 'close': 95}]}
        fresh = {'symbol': 'GOLD', 'ticker': 'GC=F', 'kind': 'metal', 'provider': '东方财富',
                 'history_as_of': '2026-10-30', 'history': [{'date': '2026-10-30', 'close': 100}]}
        self.assertEqual(app.merge_external([old], [fresh]), [fresh])
        stale = {**fresh, 'history_as_of': '2026-10-29'}
        retained = app.merge_external([old], [stale])[0]
        self.assertTrue(retained['retained'])
        self.assertEqual(retained['provider'], 'Yahoo Finance')

    def test_sox_is_the_index_while_soxx_remains_the_separate_etf(self):
        sox = next(r for r in app.GLOBAL_ASSETS if r[0] == 'SOX')
        soxx = next(r for r in app.GLOBAL_ASSETS if r[0] == 'usSOXX')
        self.assertEqual(sox[2], '^SOX')
        self.assertEqual(sox[3], '点')
        self.assertEqual(soxx[2], 'SOXX')
        self.assertEqual(sox[-1] + soxx[-1], 22)
        current = app.dt.datetime.fromisoformat('2026-11-10T03:00:00+00:00')
        for ticker, expected_date, completed in (('I:SOX', '2026-11-09', False),
                                                  ('X:BTCUSD', '2026-11-10', False)):
            stamp = current - app.dt.timedelta(hours=1)
            payload = {'status': 'DELAYED', 'ticker': ticker, 'results': [{'t': int(stamp.timestamp() * 1000), 'c': 100}]}
            with patch.object(app, 'massive_json', return_value=payload):
                rows, url, _, _ = app.massive_daily(ticker, current=current)
            self.assertIn('/ticker/' + ticker + '/', url)
            self.assertEqual((rows[-1]['date'], rows[-1]['completed']), (expected_date, completed))

    def test_sox_uses_the_exact_massive_index_after_yahoo_fails(self):
        sox = next(r for r in app.GLOBAL_ASSETS if r[0] == 'SOX')
        payload = {'status': 'DELAYED', 'ticker': 'I:SOX', 'results': [{'t': 1793318400000, 'c': 10100.24}]}
        with patch.object(app, 'massive_json', return_value=payload) as massive, \
             patch.object(app, 'fetch', side_effect=ValueError('Yahoo HTTP 403')):
            row = app.load_global_asset(sox)
        self.assertIn('/ticker/I:SOX/', massive.call_args.args[0])
        self.assertEqual((row['provider'], row['price']), ('Massive', 10100.24))

    def test_wrong_ticker_identity_cannot_be_used(self):
        with patch.object(app, 'fetch', return_value=b'{"chart":{"result":[{"meta":{"symbol":"WRONG"}}]}}'):
            row = app.load_global_asset(app.GLOBAL_ASSETS[0])
        self.assertTrue(row['error'])
        self.assertEqual(row['history'], [])

    def test_percentiles_require_at_least_252_rolling_samples(self):
        with patch.object(app, 'fetch', return_value=fixture()):
            row = app.load_geopolitics()
        self.assertTrue(all(r['score'] is None for r in row['items']))


if __name__ == '__main__':
    unittest.main()
