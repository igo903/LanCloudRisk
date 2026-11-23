import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('company_server',Path(__file__).resolve().parents[1]/'local/server.py')
app=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(app)
CN={'code':'002281','symbol':'002281','provider_symbol':'sz002281','name':'光迅科技','market':'CN','type':'stock','watch':True}
PROVIDERS=('stock_pingan_evidence','stock_futu_capital','stock_ifind_evidence','stock_futu_news')


class StockResearchTests(unittest.TestCase):
    def test_standard_symbols_never_infer_other_markets(self):
        for meta,expected in ((CN,'SZ.002281'),({**CN,'code':'600519','provider_symbol':'sh600519'},'SH.600519'),
                ({'market':'HK','code':'HK.00700','symbol':'00700'},'HK.00700'),
                ({'market':'US','code':'US.BRK-B','symbol':'BRK-B'},'US.BRK-B')):
            self.assertEqual(app.research_symbol(meta),expected)

    def test_pingan_preserves_missing_observation_time_and_zero(self):
        payload={'items':[{'code':'SZ002281','main_net_inflow':0,'amount':100}]}
        with patch.object(app,'pingan_json',return_value=payload) as send:
            result=app.stock_pingan_evidence(CN)
        self.assertTrue(result['time_missing']);self.assertIsNone(result['as_of'])
        self.assertEqual(result['metrics']['main_net_inflow'],0)
        self.assertEqual(send.call_args.args[1],{'codes':['SZ002281']})
        with patch.object(app,'pingan_json') as send,self.assertRaises(app.QuoteUnavailable):
            app.stock_pingan_evidence({**CN,'market':'US'})
        send.assert_not_called()

    def test_cli_uses_argument_array_and_preserves_all_anomaly_classes(self):
        content='资金分布与经纪商：无异动\n资金流向：11月20日净流出增加\n卖空：无异动'
        response={'retType':0,'errCode':0,'stock_symbol':'SZ.002281','content':content,'time_range':'2026.11.17 - 2026.11.23'}
        with patch.object(app,'futu_cli_auth_ready'),patch.object(app,'futu_cli_path',return_value='/test/futu'), \
             patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(response),'')) as run:
            result=app.stock_futu_capital(CN)
        self.assertEqual(result['content'],content)
        self.assertEqual(run.call_args.args[0][1:4],['unusual','capital-flows','SZ.002281'])
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['timeout'],40)
        for change in ({'stock_symbol':'US.AAPL'},{'errCode':403},{'content':''}):
            with patch.object(app,'futu_cli_auth_ready'),patch.object(app,'futu_cli_path',return_value='/test/futu'),patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({**response,**change}),'')),self.assertRaises(ValueError):
                app.stock_futu_capital(CN)

    def test_cli_auth_errors_do_not_echo_private_output(self):
        with patch.object(app,'futu_cli_auth_ready'),patch.object(app,'futu_cli_path',return_value='/test/futu'), \
             patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','401 secret-do-not-display')):
            with self.assertRaisesRegex(app.QuoteUnavailable,'futu auth login') as error:
                app.stock_futu_capital(CN)
        self.assertNotIn('secret',str(error.exception))

    def test_cli_status_is_checked_without_caching_account_metadata(self):
        payload={'token':{'status':'logged in'},'account':{'id':'private-fixture'}}
        with patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(payload),'')) as run:
            self.assertIsNone(app.futu_cli_auth_ready('/test/futu'))
        self.assertEqual(run.call_args.args[0],['/test/futu','auth','status','--format','json'])
        with patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','private-fixture')),self.assertRaisesRegex(app.QuoteUnavailable,'futu auth login') as error:
            app.futu_cli_auth_ready('/test/futu')
        self.assertNotIn('private-fixture',str(error.exception))

    def test_news_filters_wrong_company_and_duplicates(self):
        def item(title):return {'title':title,'text':'','url':'https://news.futunn.com/post/fixture','published_at':'2026-11-20T12:00:00+08:00'}
        with patch.object(app,'futu_public_items',return_value=[item('光迅科技产品交付'),item('光迅科技产品交付'),item('苹果业绩增长')]):
            result=app.stock_futu_news(CN)
        self.assertEqual(len(result['items']),1)
        with patch.object(app,'futu_public_items',return_value=[item('半导体板块全线上涨')]),self.assertRaises(ValueError):
            app.stock_futu_news(CN)

    def test_ifind_validates_identity_period_units_and_keeps_disclosure(self):
        row={'证券代码':'002281.SZ','报告期':'2026-09-30','披露日期':'2026-10-30','主要产品':'光模块','营收同比':25,
             '百分比单位':'%','ROIC':12,'经营活动现金流净额':123,'金额单位':'万元','币种':'CNY','原文链接':'https://example.com/report'}
        wrong=[{**row,'证券代码':'600519.SH'},{**row,'报告期':'2099-12-31'},{**row,'报告期':None}]
        with patch.object(app,'ifind_tool',return_value={'rows':[row,*wrong]}) as tool:
            result=app.stock_ifind_evidence(CN)
        self.assertEqual(tool.call_args_list[0].args[:2],('stock','get_stock_info'))
        self.assertEqual(len(tool.call_args_list),4)
        cash=next(f for f in result['facts'] if f['key']=='operating_cash')
        self.assertEqual((cash['value'],cash['unit'],cash['currency'],cash['notice_date']),('123','万元','CNY','2026-10-30'))
        with patch.object(app,'ifind_tool',return_value={'rows':[{**row,'金额单位':None,'百分比单位':None,'主要产品':None}]}),self.assertRaises(ValueError):
            app.stock_ifind_evidence(CN)

    def test_partial_outage_retains_old_evidence_without_refreshing_its_data_time(self):
        old={'symbol':'SZ.002281','checked_at':'2026-10-01T12:00:00+08:00','providers':{'futu_news':{
             'status':'ok','checked_at':'2026-10-01T12:00:00+08:00','data':{'items':[{'title':'old'}],'as_of':'2026-09-30'}}}}
        original={'bars':[{'date':'2026-11-20','close':10}],'fundamentals':{'pe':20}}
        record=copy.deepcopy(original)
        patches=[]
        try:
            for name in PROVIDERS:
                p=patch.object(app,name,side_effect=app.QuoteUnavailable('未配置'));p.start();patches.append(p)
            app.enrich_stock_research(CN,record,{'research_evidence':old},force=True)
        finally:
            for p in patches:p.stop()
        result=record.pop('research_evidence');self.assertEqual(record,original)
        source=result['providers']['futu_news']
        self.assertEqual(source['status'],'retained')
        self.assertEqual(source['data_checked_at'],'2026-10-01T12:00:00+08:00')
        self.assertEqual(source['data']['as_of'],'2026-09-30')
        self.assertEqual(result['providers']['pingan']['status'],'skipped')

    def test_etf_is_not_sent_to_company_research_and_six_hour_cache_is_reused(self):
        with patch.object(app,'stock_futu_news') as send:
            self.assertEqual(app.enrich_stock_research({**CN,'type':'etf'},{}),{})
            previous={'research_evidence':{'symbol':'SZ.002281','version':1,'checked_at':app.now(),'providers':{}}}
            current={};app.enrich_stock_research(CN,current,previous)
            self.assertEqual(current,previous)
        send.assert_not_called()

    def test_independent_research_preserves_price_timestamp_and_survives_later_price_commit(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'cache_locations',return_value=[]):
            store=app.DataStore(Path(folder)/'cache.json')
            old={'as_of':'2026-11-20','history_checked_at':'2026-11-20T16:00:00+08:00','bars':[{'date':'2026-11-20','completed':True,'close':10}]}
            store.commit_snapshot({'data_kind':'local','instruments':{'002281':old},'catalog':store.catalog_view(),'fetched_at':'2026-11-20T16:00:00+08:00'})
            evidence={'version':1,'symbol':'SZ.002281','checked_at':app.now(),'providers':{}}
            store.research_jobs['002281']={'id':'fixture','status':'loading'}
            with patch.object(app,'enrich_stock_research',return_value={'research_evidence':evidence}):
                store.refresh_research('002281','fixture')
            self.assertEqual(store.snapshot['fetched_at'],'2026-11-20T16:00:00+08:00')
            self.assertEqual(store.snapshot['instruments']['002281']['bars'],old['bars'])
            store.commit_snapshot({**store.snapshot,'instruments':{'002281':old}})
            self.assertEqual(store.snapshot['instruments']['002281']['research_evidence'],evidence)
            self.assertEqual(store.research_jobs['002281']['status'],'ready')

    def test_research_api_uses_page_session_and_reports_background_jobs(self):
        class QuietHandler(app.Handler):
            def log_message(self,*args):pass
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'cache_locations',return_value=[]):
            store=app.DataStore(Path(folder)/'cache.json')
            with app.LocalHTTPServer(('127.0.0.1',0),QuietHandler) as server:
                server.store=store
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                url='http://127.0.0.1:'+str(server.server_port)+'/api/research/refresh'
                def send(token):
                    req=urllib.request.Request(url,data=json.dumps({'code':'002281'}).encode(),headers={'Content-Type':'application/json','X-RiskLens-Token':token})
                    try:
                        with urllib.request.urlopen(req) as response:return response.status,json.load(response)
                    except urllib.error.HTTPError as response:return response.code,json.load(response)
                try:
                    with patch.object(store,'start_research',return_value=True) as start:
                        self.assertEqual(send('invalid')[0],403);start.assert_not_called()
                        status,body=send(app.TOKEN)
                        self.assertEqual((status,body['started']),(202,True));start.assert_called_once_with('002281')
                    with urllib.request.urlopen(url.replace('/research/refresh','/status')) as response:
                        self.assertEqual(json.load(response)['research_jobs'],{})
                finally:
                    server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
