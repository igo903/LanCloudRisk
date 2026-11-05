import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('etf_flow_server', Path(__file__).resolve().parents[1] / 'local/server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class EtfFlowTests(unittest.TestCase):
    def test_exchange_units_and_requested_date(self):
        with patch.object(app, 'json_get', return_value={'result': [
            {'STAT_DATE': '2026-11-05', 'SEC_CODE': '515980', 'TOT_VOL': '100.25'},
            {'STAT_DATE': '2026-11-04', 'SEC_CODE': '588080', 'TOT_VOL': '90'},
            {'STAT_DATE': '2026-11-05', 'SEC_CODE': '510300', 'TOT_VOL': 'bad'},
        ]}):
            self.assertEqual(app.load_sse_etf_shares('2026-11-05'), {'515980': 1002500})

    def test_prior_nav_and_no_bridging_missing_share_sessions(self):
        dates = ['2026-11-02','2026-11-03','2026-11-04','2026-11-05']
        rows = app.estimate_etf_flow(dates, dict(zip(dates, [1.25, 2, 3, 4])),
                                    {dates[0]: 100, dates[1]: 80, dates[3]: 60})
        self.assertEqual([r['netFlow'] for r in rows], [-25, None, None])
        self.assertEqual(app.estimate_etf_flow(dates[:2], {dates[0]: 1}, {dates[0]: 50, dates[1]: 50})[0]['netFlow'], 0)

    def test_batch_queries_each_day_once_and_retains_previous_on_failure(self):
        dates = ['2026-11-04','2026-11-05']
        codes = ['515980', '588080']
        records = {code: {'bars': [{'date': day, 'completed': True} for day in dates] +
                          [{'date':'2026-11-06','completed':False}]} for code in codes}
        with patch.object(app, 'load_sse_etf_shares', side_effect=lambda day: dict.fromkeys(codes, 100 if day == dates[0] else 150)) as request, \
             patch.object(app, 'load_nav_history', return_value=({dates[0]: 2}, 'https://fund.eastmoney.com/')):
            app.refresh_etf_flows(records, {}, codes)
        self.assertCountEqual([c.args[0] for c in request.call_args_list], dates)
        self.assertEqual(records[codes[0]]['etf_flow']['rows'][0]['netFlow'], 100)
        previous = records
        records = {code: {'bars': previous[code]['bars']} for code in codes}
        with patch.object(app, 'load_sse_etf_shares', return_value={}), patch.object(app, 'load_nav_history', side_effect=ValueError('missing')):
            app.refresh_etf_flows(records, previous, codes)
        self.assertEqual(records[codes[0]]['etf_flow'], previous[codes[0]]['etf_flow'])
        self.assertIn('etf_flow', records[codes[0]]['research_errors'])
