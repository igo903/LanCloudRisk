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
        payload = {'chart': {'result': [{'meta': {'symbol': 'SOXX'}, 'timestamp': [1793318400], 'indicators': {'quote': [{'close': [100]}]}}]}}
        with patch.object(app, 'fetch', return_value=json.dumps(payload).encode()):
            row = app.load_global_asset(next(r for r in app.GLOBAL_ASSETS if r[0] == 'usSOXX'))
        self.assertEqual(len(row['history']), 1)
        self.assertIsNone(row['returns']['60']['value'])
        self.assertIsNone(row['returns']['250']['value'])

    def test_a50_fallback_is_the_exact_index_and_has_enough_history(self):
        quote = {'chart': {'result': [{'meta': {'symbol': 'XIN9.FGI'}, 'timestamp': [], 'indicators': {'quote': [{'close': []}]}}]}}
        dates = [(app.dt.date(2025, 10, 1)+app.dt.timedelta(days=i)).isoformat() for i in range(251)]
        history = {'data': {'code': 'XIN9', 'klines': [f'{d},100,{100+i},400,90,0,0' for i, d in enumerate(dates)]}}
        with patch.object(app, 'fetch', side_effect=[json.dumps(quote).encode(), json.dumps(history).encode()]):
            row = app.load_global_asset(next(r for r in app.GLOBAL_ASSETS if r[0] == 'A50'))
        self.assertEqual(len(row['history']), 251)
        self.assertEqual(row['returns']['250']['value'], 250.)
        self.assertIn('100.XIN9', row['history_url'])
        self.assertNotIn('CN00Y', row['history_url'])

    def test_sox_is_the_index_while_soxx_remains_the_separate_etf(self):
        sox = next(r for r in app.GLOBAL_ASSETS if r[0] == 'SOX')
        soxx = next(r for r in app.GLOBAL_ASSETS if r[0] == 'usSOXX')
        self.assertEqual(sox[2], '^SOX')
        self.assertEqual(sox[3], '点')
        self.assertEqual(soxx[2], 'SOXX')
        self.assertEqual(sox[-1] + soxx[-1], 22)

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
