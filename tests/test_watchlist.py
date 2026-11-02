import importlib.util
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('watch_server',Path(__file__).resolve().parents[1]/'local/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
META={'code':'US.NVDA','symbol':'NVDA','name':'英伟达','market':'US','currency':'USD','type':'stock','provider_symbol':'NVDA','watch':True}
def fixture():
    stamp=lambda x:int(dt.datetime.fromisoformat(x).timestamp())
    return {'meta':{'symbol':'NVDA','currency':'USD','exchangeTimezoneName':'America/New_York','currentTradingPeriod':{'regular':{'start':stamp('2026-11-02T14:30:00+00:00'),'end':stamp('2026-11-02T21:00:00+00:00')}}},'timestamp':[stamp('2026-10-30T13:30:00+00:00'),stamp('2026-11-02T14:30:00+00:00')],'indicators':{'quote':[{'open':[100,101],'high':[102,103],'low':[99,100],'close':[101,102],'volume':[123,234]}]}}

class WatchlistTests(unittest.TestCase):
    def store(self,path):
        with patch.object(app,'cache_locations',return_value=[Path(path)]):return app.DataStore(path)
    def test_search_filters_security_types_and_preserves_market_identity(self):
        rows='sh~600519~贵州茅台~gzmt~GP-A^hk~00700~腾讯控股~txkg~GP^us~nvda.oq~英伟达~ywd~GP^us~brk.b.n~伯克希尔~bk~GP^sh~000001~上证指数~sz~ZS^hk~12345~认购证~rg~QZ^sz~200001~B股~bg~GP-B'
        raw=('v_hint='+json.dumps(rows,ensure_ascii=True)+';').encode()
        result=app.parse_security_search(raw)
        self.assertEqual([r['code'] for r in result],['600519','HK.00700','US.NVDA','US.BRK-B'])
        self.assertEqual(len(app.parse_security_search(raw,'HK')),1)
    def test_invalid_identifiers_cannot_be_provider_urls(self):
        for market,symbol in [('US','https://evil'),('CN','600519/..'),('HK','700?'),('OTHER','NVDA')]:
            with self.assertRaises(ValueError):app.asset_id(market,symbol)
    def test_intraday_us_bar_is_provisional_after_dst_change(self):
        r=app.parse_international_chart(fixture(),META,dt.datetime.fromisoformat('2026-11-02T20:59:00+00:00'))
        self.assertTrue(r['bars'][0]['completed']);self.assertFalse(r['bars'][-1]['completed'])
        self.assertEqual(r['closed_as_of'],'2026-10-30')
        r=app.parse_international_chart(fixture(),META,dt.datetime.fromisoformat('2026-11-02T21:02:00+00:00'))
        self.assertTrue(r['completed'])
    def test_half_day_uses_provider_session_end_and_hk_auction_buffer(self):
        f=fixture();f['meta']['currentTradingPeriod']['regular']['end']-=3*3600
        self.assertTrue(app.parse_international_chart(f,META,dt.datetime.fromisoformat('2026-11-02T18:02:00+00:00'))['completed'])
        f['meta'].update(symbol='0700.HK',exchangeTimezoneName='Asia/Hong_Kong');meta={**META,'code':'HK.00700','symbol':'00700','market':'HK','currency':'HKD'}
        f['timestamp'][-1]=int(dt.datetime.fromisoformat('2026-11-02T01:30:00+00:00').timestamp())
        f['meta']['currentTradingPeriod']['regular']={'start':f['timestamp'][-1],'end':int(dt.datetime.fromisoformat('2026-11-02T08:00:00+00:00').timestamp())}
        self.assertFalse(app.parse_international_chart(f,meta,dt.datetime.fromisoformat('2026-11-02T08:02:00+00:00'))['completed'])
        self.assertTrue(app.parse_international_chart(f,meta,dt.datetime.fromisoformat('2026-11-02T08:10:00+00:00'))['completed'])
    def test_bad_ohlc_and_wrong_ticker_fail_closed(self):
        f=fixture();f['meta']['symbol']='AAPL'
        with self.assertRaises(ValueError):app.parse_international_chart(f,META)
        f=fixture();f['indicators']['quote'][0]['high']=[1,1]
        with self.assertRaises(ValueError):app.parse_international_chart(f,META)
    def test_registry_persists_revisions_duplicate_and_stale_edits_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cache.json';store=self.store(p)
            with patch.object(app,'resolve_security',return_value=META),patch.object(store,'start_asset'):
                store.add_watch('US','NVDA',0)
                with self.assertRaises(RuntimeError):store.add_watch('US','NVDA',1)
            with self.assertRaises(RuntimeError):store.remove_watch('US.NVDA',0)
            restored=self.store(p);self.assertTrue(restored.catalog['US.NVDA']['watch']);self.assertEqual(restored.watchlist_revision,1)
            restored.remove_watch('US.NVDA',1)
            self.assertFalse(self.store(p).catalog['US.NVDA']['watch'])
    def test_remove_preserves_benchmark_and_late_jobs_cannot_resurrect_stock(self):
        with tempfile.TemporaryDirectory() as folder:
            store=self.store(Path(folder)/'cache.json');store.catalog['US.NVDA']=META.copy()
            store.commit_snapshot({'data_kind':'local','catalog':store.catalog_view(),'instruments':{'US.NVDA':{'as_of':'2026-10-30'},'588080':{'as_of':'2026-10-30'}}})
            stale=json.loads(json.dumps(store.snapshot));store.remove_watch('US.NVDA',0);store.remove_watch('588080',1);store.commit_snapshot(stale)
            self.assertNotIn('US.NVDA',store.snapshot['instruments']);self.assertIn('588080',store.snapshot['instruments'])
            self.assertTrue(store.catalog['588080']['benchmark']);self.assertFalse(store.catalog['588080']['watch'])
    def test_concurrent_snapshot_merge_preserves_newer_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            store=self.store(Path(folder)/'cache.json');store.catalog['US.NVDA']=META.copy()
            store.commit_snapshot({'catalog':store.catalog_view(),'instruments':{'US.NVDA':{'as_of':'2026-10-30','history_checked_at':'2026-11-02T10:00:00','bars':[1]}}})
            store.commit_snapshot({'catalog':store.catalog_view(),'instruments':{'002281':{'as_of':'2026-10-30'},'US.NVDA':{'as_of':'2026-10-29','history_checked_at':'2026-11-01T10:00:00'}}})
            self.assertEqual(store.snapshot['instruments']['US.NVDA']['bars'],[1])
    def test_external_short_windows_and_weight_budget(self):
        rows=[{'date':str(i),'close':100+i} for i in range(21)]
        r=app.external_changes(rows,'equity');self.assertAlmostEqual(r['5']['value'],(120/115-1)*100);self.assertAlmostEqual(r['20']['value'],20)
        self.assertIsNone(app.external_changes(rows[:5],'equity')['5']['value'])
        self.assertEqual(sum(app.RISK_WEIGHTS.values()),80);self.assertEqual(sum(a[-1] for a in app.GLOBAL_ASSETS),80)
    def test_financials_require_annual_periods_and_consistent_currency(self):
        def row(key,currency='USD',period='12M'):
            return {key:[{'asOfDate':'2026-01-31','currencyCode':currency,'periodType':period,'reportedValue':{'raw':100}}]}
        with patch.object(app,'json_get',return_value={'timeseries':{'result':[row('annualTotalRevenue'),row('annualNetIncome')]}}):
            f,_=app.load_international_financials(META);self.assertIsNone(f['notice_date']);self.assertIn('非TTM',f['period_basis']);self.assertEqual(f['currency'],'USD')
        for rows in [[row('annualTotalRevenue'),row('annualNetIncome','HKD')],[row('annualTotalRevenue',period='3M'),row('annualNetIncome')]]:
            with patch.object(app,'json_get',return_value={'timeseries':{'result':rows}}):
                with self.assertRaises(ValueError):app.load_international_financials(META)

if __name__=='__main__':unittest.main()
