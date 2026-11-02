import importlib.util,json,tempfile,datetime as dt,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('screen_server',Path(__file__).resolve().parents[1]/'local/server.py');app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
def record():return {'as_of':dt.datetime.now(app.TZ).date().isoformat(),'bars':[{'close':10}],'fundamentals':{'marketCap':100}}
class ScreeningTests(unittest.TestCase):
 def make(self,p):
  with patch.object(app,'cache_locations',return_value=[p]):return app.DataStore(p)
 def test_member_scope_rejects_foreign_etf_and_untrusted_codes(self):
  self.assertIsNone(app.screening_member({'code':'00700','name':'腾讯'}));self.assertIsNone(app.screening_member({'code':'http://bad'}));self.assertIsNone(app.screening_member({'code':'510300'}));self.assertEqual(app.screening_member({'code':'688981','name':'中芯'})['provider_symbol'],'sh688981')
 def test_only_active_user_selected_stocks_and_etfs_may_start(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.make(Path(d)/'cache.json')
   for payload in [{'source':'watch','codes':[]},{'source':'watch','codes':['600519']},{'source':'etf','codes':['002281']},{'source':'watch','codes':['002281'],'market':'US'},{'source':'etf','codes':['515980']*4}]:
    with self.assertRaises(ValueError):s.start_screening(payload)
   with patch.object(app.threading,'Thread') as thread:
    job=s.start_screening({'source':'watch','codes':['002281'],'market':'CN'});thread.return_value.start.assert_called_once();self.assertEqual(s.screen_jobs[job]['status'],'loading')
    with self.assertRaises(RuntimeError):s.start_screening({'source':'watch','codes':['002281'],'market':'CN'})
 def test_etf_pool_is_deduplicated_and_never_added_to_watchlist(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.make(Path(d)/'cache.json');s.snapshot['instruments']={'515980':{'etf':{'period':dt.datetime.now(app.TZ).date().isoformat(),'holdings':[{'code':'688981','name':'中芯'},{'code':'603019','name':'中科'}]}},'588080':{'etf':{'period':dt.datetime.now(app.TZ).date().isoformat(),'holdings':[{'code':'688981','name':'中芯'},{'code':'00700','name':'腾讯'}]}}}
   before=s.catalog_view();s.screen_jobs['test']={'status':'loading','done':0}
   with patch.object(app,'load_screening_record',side_effect=lambda m,old:(m,record())):
    s.prepare_screening('test','etf',[s.catalog['515980'],s.catalog['588080']],False)
   job=s.screen_jobs['test'];r=job['result'];self.assertEqual(job['status'],'ready');self.assertEqual(len(r['catalog']),2);self.assertEqual(len(r['origins']['688981']),2);self.assertEqual(len(r['pool_issues']),1)
   self.assertEqual(s.catalog_view(),before);self.assertNotIn('603019',s.snapshot['instruments']);self.assertIn('603019',s.screening_cache)
   self.assertIn('603019',self.make(s.cache).screening_cache)
 def test_cached_members_are_reused_and_failures_stay_visible(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.make(Path(d)/'cache.json');s.snapshot['instruments']['002281']=record();s.screen_jobs['test']={'status':'loading','done':0}
   with patch.object(app,'load_screening_record',side_effect=ValueError('provider unavailable')) as fetch:
    s.prepare_screening('test','watch',[s.catalog['002281'],s.catalog['601869']],False)
   self.assertEqual(fetch.call_count,1);r=s.screen_jobs['test']['result'];self.assertIn('002281',r['instruments']);self.assertNotIn('601869',r['instruments']);self.assertIn('provider unavailable',r['errors']['601869']['provider'])
 def test_empty_or_failed_etf_disclosure_cannot_invent_constituents(self):
  with tempfile.TemporaryDirectory() as d:
   s=self.make(Path(d)/'cache.json');s.screen_jobs['test']={'status':'loading','done':0}
   with patch.object(app,'load_holdings',side_effect=ValueError('no disclosure')):s.prepare_screening('test','etf',[s.catalog['515980']],False)
   self.assertEqual(s.screen_jobs['test']['status'],'error');self.assertIn('no disclosure',s.screen_jobs['test']['error']);self.assertNotIn('result',s.screen_jobs['test'])

class LiveQuoteFallbackTests(unittest.TestCase):
 def test_preopen_indicative_price_cannot_overwrite_confirmed_history(self):
  rec={'latest_quote':{'code':'603019','name':'中科曙光'},'bars':[{'date':'2026-10-30','open':70,'high':75,'low':69,'close':74,'vol':100,'completed':True}],'as_of':'2026-10-30','volume_unit':'股/份'}
  quote={'timestamp':'2026-11-02T09:21:00+08:00','date':'2026-11-02','code':'603019','open':0,'high':0,'low':0,'price':80,'volume':0}
  with self.assertRaisesRegex(ValueError,'竞价指示价') as failure:app.patch_quote(rec,quote)
  app.retain_valid_history(rec,failure.exception)
  self.assertEqual(len(rec['bars']),1);self.assertEqual(rec['latest_quote']['price'],74);self.assertEqual(rec['latest_quote']['timestamp'],'2026-10-30');self.assertIn('开盘前',rec['live_error'])

if __name__=='__main__':unittest.main()
