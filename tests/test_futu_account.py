import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('private_account',Path(__file__).resolve().parents[1]/'local/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
ACCOUNT={'account_id':'fixture-account-1','acc_type':'margin','name':'测试账户','security_firm':'FUTUSECURITIES','enable_market':[1,2,4]}


class FutuAccountTests(unittest.TestCase):
    def test_command_allowlist_prevents_trade_commands_and_validates_identifiers(self):
        with patch.object(app.subprocess,'run') as run:
            for action in ('buy','sell','cancel','modify','order place','snapshot'):
                with self.assertRaises(app.AccountReadError):app.futu_account_command('/test/futu',action)
            with self.assertRaises(app.AccountReadError):app.futu_account_command('/test/futu','funds',account='--help',currency='BAD')
            run.assert_not_called()
        with patch.object(app.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'[]','')) as run:
            app.futu_account_command('/test/futu','orders',account='fixture-account-1',market='US')
        self.assertEqual(run.call_args.args[0],['/test/futu','order','list','--account','fixture-account-1','--market','US','--format','json','--source','skill'])
        self.assertNotIn('shell',run.call_args.kwargs)

    def test_auth_probe_stops_before_any_account_reads_when_not_logged_in(self):
        with patch.object(app,'futu_cli_path',return_value=None),self.assertRaises(app.AccountReadError) as error:
            app.futu_account_connection()
        self.assertEqual(error.exception.code,'cli_missing')
        calls=[]
        def provider(exe,action):
            calls.append(action);return {'token':{'status':'not logged in'}} if action=='auth' else None
        with patch.object(app,'futu_cli_path',return_value='/test/futu'),patch.object(app,'futu_account_command',side_effect=provider),self.assertRaises(app.AccountReadError) as error:
            app.futu_account_connection()
        self.assertEqual(error.exception.code,'login_required');self.assertEqual(calls,['version','auth'])

    def test_permission_denial_is_not_described_as_missing_token_or_echoed(self):
        result=subprocess.CompletedProcess([],1,'','PERMISSION_DENIED private-token-do-not-print')
        with patch.object(app.subprocess,'run',return_value=result),self.assertRaises(app.AccountReadError) as error:
            app.futu_account_command('/test/futu','list')
        self.assertEqual(error.exception.code,'permission_denied')
        self.assertNotIn('private-token',str(error.exception));self.assertNotIn('未登录',str(error.exception))

    def test_funds_never_substitute_cash_or_margin_buying_power_for_available_funds(self):
        value=app.normalize_account_funds({'currency':'HKD','cash':'0','power':'123456.789012345','total_assets':'10'},'HKD')
        self.assertIsNone(value['available_funds']);self.assertEqual(value['cash'],'0')
        self.assertEqual(value['power'],'123456.789012345')
        with self.assertRaises(app.AccountReadError):app.normalize_account_funds({'currency':'USD','cash':'10'},'HKD')

    def test_positions_keep_native_currency_and_honor_invalid_profit_cost_flags(self):
        rows=[{'code':'US.AAPL','qty':'1.23456789','currency':'USD','cost_price':'10','cost_price_valid':False,'pl_val':'20','pl_val_valid':False},
              {'code':'HK.00700','qty':'-2','currency':'HKD','cost_price':'5','cost_price_valid':True,'pl_ratio':'0','pl_ratio_valid':True},
              {'code':'US.MSFT','qty':'0.00'},{'code':'US.BAD','qty':None}]
        value=app.normalize_account_positions(rows)
        self.assertEqual([r['currency'] for r in value['items']],['USD','HKD'])
        self.assertEqual(value['items'][0]['qty'],'1.23456789');self.assertIsNone(value['items'][0]['cost_price'])
        self.assertIsNone(value['items'][0]['pl_val']);self.assertEqual(value['items'][1]['pl_ratio'],'0')
        self.assertEqual(value['skipped'],1)

    def test_today_pending_orders_use_exchange_day_and_exclude_terminal_or_old_orders(self):
        current=app.dt.datetime.fromisoformat('2026-11-23T05:30:00+00:00')
        stamp=lambda text:int(app.dt.datetime.fromisoformat(text).timestamp()*1000000)
        base={'order_id':'fixture-order','symbol':'US.AAPL','qty':'10','filled_qty':'2','status':'FILLED_PART','create_time':stamp('2026-11-23T05:10:00+00:00')}
        rows=[base,base,{**base,'order_id':'old','create_time':stamp('2026-11-23T03:10:00+00:00')},
              *[{**base,'order_id':status,'status':status} for status in ('FILLED_ALL','CANCELLED_PART','CANCELLED_ALL','FAILED')],
              {**base,'order_id':'wrong-market','symbol':'HK.00700'},{**base,'order_id':'unknown','status':'NEW_UNKNOWN'},
              {**base,'order_id':'missing-time','create_time':'2026-11-23 00:10:00'}]
        value=app.normalize_pending_orders(rows,'US',current=current)
        self.assertEqual(len(value['items']),1);self.assertEqual(value['items'][0]['remaining_qty'],'8')
        self.assertEqual(value['local_date'],'2026-11-23');self.assertEqual(value['unclassified'],3)
        hk={**base,'symbol':'HK.00700','create_time':stamp('2026-11-23T03:10:00+00:00')}
        self.assertEqual(len(app.normalize_pending_orders([hk],'HK',current=current)['items']),1)

    def test_missing_filled_qty_stays_missing_and_timestamp_units_are_supported(self):
        current=app.dt.datetime.fromisoformat('2026-11-23T05:30:00+00:00')
        seconds=int((current-app.dt.timedelta(minutes=1)).timestamp())
        for multiplier in (1,1000,1000000,1000000000):
            row={'order_id':'test','symbol':'US.AAPL','qty':10,'status':'SUBMITTED','create_time':seconds*multiplier}
            value=app.normalize_pending_orders([row],'US',current=current)['items'][0]
            self.assertIsNone(value['filled_qty']);self.assertIsNone(value['remaining_qty'])

    def test_nanosecond_day_boundary_does_not_round_into_next_day(self):
        instant=app.dt.datetime.fromisoformat('2026-11-23T05:00:00+00:00')
        stamp=int(instant.timestamp())*1000000000-1
        parsed=app.account_order_time(stamp)
        self.assertEqual(app.account_market_day(parsed,'US'),'2026-11-22')
        self.assertIsNone(app.account_order_time(float('inf')))
        self.assertIsNone(app.account_order_time(float('nan')))

    def test_selected_account_is_verified_and_sections_fail_independently(self):
        calls=[]
        def provider(exe,action,**kwargs):
            calls.append((action,kwargs))
            if action=='list':return [ACCOUNT]
            if action=='funds':raise app.AccountReadError('permission_denied','资金权限不足')
            if action=='positions':return []
            return []
        with patch.object(app,'futu_account_connection',return_value='/test/futu'),patch.object(app,'futu_account_command',side_effect=provider):
            with self.assertRaises(app.AccountReadError):app.read_futu_account({'action':'snapshot','account_id':'not-authorized'})
            self.assertEqual([name for name,_ in calls],['list']);calls.clear()
            result=app.read_futu_account({'action':'snapshot','account_id':ACCOUNT['account_id'],'currency':'HKD'})
        self.assertFalse(result['sections']['funds']['ok']);self.assertTrue(result['sections']['positions']['ok'])
        self.assertEqual({k for k in result['sections'] if k.startswith('orders:')},{'orders:HK','orders:US','orders:HKCC'})
        self.assertTrue(all(kwargs.get('account')==ACCOUNT['account_id'] for action,kwargs in calls if action!='list'))

    def test_account_api_requires_session_and_keeps_data_out_of_cache_and_public_get(self):
        class Quiet(app.Handler):
            def log_message(self,*args):pass
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'cache_locations',return_value=[]):
            cache=Path(folder)/'cache.json';store=app.DataStore(cache)
            with app.LocalHTTPServer(('127.0.0.1',0),Quiet) as server:
                server.store=store;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base='http://127.0.0.1:'+str(server.server_port)
                def request(token='',origin=None,post=True):
                    headers={'Content-Type':'application/json','X-RiskLens-Token':token}
                    if origin:headers['Origin']=origin
                    req=urllib.request.Request(base+'/api/account/read',data=json.dumps({'action':'list'}).encode() if post else None,headers=headers)
                    try:
                        with urllib.request.urlopen(req) as response:return response.status,json.load(response),response.headers
                    except urllib.error.HTTPError as response:return response.code,json.load(response),response.headers
                try:
                    before=json.dumps(store.snapshot)
                    with patch.object(app,'read_futu_account',return_value={'accounts':[{'account_id':'private-fixture'}]}) as read:
                        self.assertEqual(request()[0],403);self.assertEqual(request(app.TOKEN,'https://evil.example')[0],403);read.assert_not_called()
                        self.assertEqual(request(app.TOKEN,post=False)[0],404)
                        status,value,headers=request(app.TOKEN,base)
                        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
                        self.assertEqual(value['accounts'][0]['account_id'],'private-fixture')
                    self.assertEqual(json.dumps(store.snapshot),before)
                    self.assertFalse(cache.exists())
                finally:server.shutdown();thread.join()


if __name__=='__main__':unittest.main()
