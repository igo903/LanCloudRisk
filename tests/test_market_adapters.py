import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('adapter_server', Path(__file__).resolve().parents[1] / 'local/server.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def encoded(value):
    return json.dumps(value).encode()


class MarketAdapterTests(unittest.TestCase):
    def test_cn_quote_failures_and_malformed_payloads_follow_all_six_sources(self):
        symbol = 'sz159338'
        quote = {'code':'159338','name':'ETF','price':1,'timestamp':'2026-11-20T15:00:00+08:00'}
        trace = []
        with patch.object(app, 'fetch', return_value=b'fixture'), \
             patch.object(app, 'parse_quotes', side_effect=TypeError('bad payload')), \
             patch.object(app, 'load_eastmoney_quotes', side_effect=ValueError('HTTP 503')), \
             patch.object(app, 'load_pingan_quotes', side_effect=app.QuoteUnavailable('未配置密钥')), \
             patch.object(app, 'load_ifind_quotes', return_value={}), \
             patch.object(app, 'load_futu_cn_quotes', side_effect=ValueError('HTTP 401')), \
             patch.object(app, 'parse_sina_quotes', return_value={symbol:quote}):
            result = app.traced_quote_call(trace.append, '报价', symbol, app.load_cn_quotes, [symbol])
        self.assertEqual(result[symbol], quote)
        self.assertEqual([r['provider'] for r in trace], ['腾讯报价','东方财富报价','平安证券报价','同花顺 iFinD 报价','富途报价','新浪报价'])
        self.assertEqual([r['status'] for r in trace], ['failed','failed','skipped','failed','failed','ok'])
        self.assertIn('503', trace[1]['detail'])

    def test_search_and_add_foreign_follow_same_six_sources(self):
        searches = ('search_futu_securities','search_yahoo_securities','search_tencent_securities',
                    'search_eastmoney_securities','search_ifind_securities','search_sina_securities')
        resolves = ('resolve_futu_security','resolve_yahoo_security','resolve_tencent_security',
                    'resolve_eastmoney_security','resolve_ifind_security','resolve_sina_security')
        for market, symbol, currency in (('US','AAPL','USD'),('HK','00700','HKD')):
            meta = {'code':market+'.'+symbol,'symbol':symbol,'name':'fixture','market':market,'currency':currency,'type':'stock'}
            for mode, methods in (('search',searches),('resolve',resolves)):
                attempts, patches = [], []
                def load(name, index):
                    def call(*args):
                        attempts.append(name)
                        if index<5:
                            raise ValueError('unavailable')
                        return [dict(meta)] if mode=='search' else dict(meta)
                    return call
                try:
                    for i, name in enumerate(methods):
                        p=patch.object(app,name,side_effect=load(name,i)); p.start(); patches.append(p)
                    with patch.object(app,'SEARCH_CACHE',{}):
                        result = app.search_securities(symbol,market) if mode=='search' else app.resolve_security(market,symbol)
                    self.assertTrue(result)
                    self.assertEqual(attempts,list(methods))
                finally:
                    for p in reversed(patches):
                        p.stop()

    def test_cn_search_uses_same_six_source_order(self):
        attempts, patches = [], []
        methods = ('search_tencent_securities','search_eastmoney_securities','search_pingan_securities',
                   'search_ifind_securities','search_futu_securities','search_sina_securities')
        def load(name,index):
            def call(*args):
                attempts.append(name)
                return [app.cn_security_meta('sz159338','ETF','新浪')] if index==5 else []
            return call
        try:
            for i,name in enumerate(methods):
                p=patch.object(app,name,side_effect=load(name,i)); p.start(); patches.append(p)
            with patch.object(app,'SEARCH_CACHE',{}):
                result=app.search_securities('159338','CN')
            self.assertEqual(result[0]['code'],'159338')
            self.assertEqual(attempts,list(methods))
        finally:
            for p in reversed(patches):
                p.stop()

    def test_eastmoney_decimal_prices_lot_volume_timestamp_and_market_identity(self):
        row = {'f13':0,'f43':1.003,'f44':1.009,'f45':.995,'f46':1.008,'f47':123,
               'f48':12300,'f57':'159338','f58':'ETF','f59':3,'f60':1.02,'f86':1795160076}
        with patch.object(app,'fetch',return_value=encoded({'rc':0,'data':row})):
            quote=app.eastmoney_quote('sz159338')
        self.assertEqual((quote['price'],quote['volume']),(1.003,12300))
        self.assertEqual(quote['timestamp'],app.public_time(row['f86']).isoformat())
        for fields in ({'f86':None},{'f57':'159919'},{'f13':1}):
            with self.subTest(fields=fields), patch.object(app,'fetch',return_value=encoded({'rc':0,'data':{**row,**fields}})), self.assertRaises(ValueError):
                app.eastmoney_quote('sz159338')

    def test_eastmoney_daily_units_and_current_day_confirmation(self):
        today=app.quote_day(app.dt.datetime.now(app.dt.timezone.utc),'Asia/Shanghai').isoformat()
        payload={'rc':0,'data':{'code':'159338','market':0,'klines':[today+',1,1.1,1.2,.9,123']}}
        with patch.object(app,'fetch',return_value=encoded(payload)) as fetch:
            rows,url,adjustment,unit=app.eastmoney_cn_daily('sz159338')
        self.assertEqual((rows[0]['vol'],adjustment,unit),(12300,'none','股/份'))
        self.assertFalse(rows[0]['completed'])
        self.assertIn('fqt=0',url)
        payload['data']['market']=1
        with patch.object(app,'fetch',return_value=encoded(payload)), self.assertRaisesRegex(ValueError,'同一市场'):
            app.eastmoney_cn_daily('sz159338')
        payload={'rc':0,'data':{'code':'XIN9','market':100,'klines':[today+',1,1.1,1.2,.9,-']}}
        with patch.object(app,'fetch',return_value=encoded(payload)):
            rows,_=app.eastmoney_daily('100.XIN9','Asia/Shanghai',close_only=True)
            self.assertIsNone(rows[0]['vol'])
            with self.assertRaises(ValueError):
                app.eastmoney_daily('100.XIN9','Asia/Shanghai')

    def test_eastmoney_search_filters_wrong_markets_bonds_and_preserves_etfs(self):
        samples=[
            {'Code':'AAPL','Name':'苹果','QuoteID':'105.AAPL','Classify':'UsStock','SecurityType':20},
            {'Code':'SOXX','Name':'半导体ETF-iShares','QuoteID':'105.SOXX','Classify':'UsStock','SecurityType':7},
            {'Code':'AAPL25','Name':'Apple Notes due 2025','QuoteID':'105.AAPL25','Classify':'UsStock','SecurityType':7},
            {'Code':'02800','Name':'盈富基金','QuoteID':'116.02800','Classify':'HK','SecurityType':6},
            {'Code':'159338','Name':'ETF','QuoteID':'1.159338','Classify':'Fund','SecurityType':8},
            {'Code':'000700','Name':'韩国股','QuoteID':'177.000700','Classify':'Foreign','SecurityType':7}]
        with patch.object(app,'eastmoney_search_rows',return_value=samples):
            self.assertEqual([r['code'] for r in app.search_eastmoney_securities('fixture','US')],['US.AAPL','US.SOXX'])
            self.assertEqual([r['code'] for r in app.search_eastmoney_securities('fixture','HK')],['HK.02800'])
            self.assertEqual(app.search_eastmoney_securities('fixture','CN'),[])
        with self.assertRaisesRegex(ValueError,'不匹配'):
            app.eastmoney_secid({'market':'HK','symbol':'00700','eastmoney_secid':'105.00700'})

    def test_eastmoney_a50_rejects_futures_proxy_and_cnh_rejects_onshore(self):
        asset=next(a for a in app.GLOBAL_ASSETS if a[0]=='A50')
        future={'Code':'CN00Y','Name':'富时中国A50','QuoteID':'109.CN00Y','Classify':'Futures','SecurityTypeName':'期货'}
        index={'Code':'XIN9','Name':'富时中国A50','QuoteID':'100.XIN9','Classify':'UniversalIndex','SecurityTypeName':'指数'}
        with patch.object(app,'eastmoney_search_rows',return_value=[future,index]), \
             patch.object(app,'eastmoney_daily',return_value=([{'date':'2026-11-20','close':100,'completed':True}],'fixture')) as daily:
            record,_=app.load_eastmoney_global(asset)
        self.assertEqual(daily.call_args.args[0],'100.XIN9')
        self.assertEqual(record['history'][0]['close'],100)
        for symbol,rows in (('A50',[future]),('CNH',[{'Name':'美元兑离岸人民币','Code':'USDCNY','QuoteID':'133.USDCNY','Classify':'FOREX'}])):
            with patch.object(app,'eastmoney_search_rows',return_value=rows), self.assertRaises(app.QuoteUnavailable):
                app.load_eastmoney_global(next(a for a in app.GLOBAL_ASSETS if a[0]==symbol))

    def test_futu_cn_snapshot_uses_official_path_and_rejects_date_only_time(self):
        row={'code':'SZ.159338','sc_name':'ETF','last_price':1.1,'open_price':1,'high_price':1.2,'low_price':.9,
             'volume':123,'update_time':'2026-11-20 15:00:00'}
        with patch.object(app,'futu_json',return_value={'data':{'snapshot_list':[row]}}) as fetch:
            quotes=app.load_futu_cn_quotes(['sz159338'],[])
        self.assertEqual(fetch.call_args.args[0],app.FUTU_API+'/snapshot')
        self.assertEqual(fetch.call_args.kwargs['data'],{'code_list':['SZ.159338']})
        self.assertEqual(quotes['sz159338']['volume'],123)
        with patch.object(app,'futu_json',return_value={'data':{'snapshot_list':[{**row,'update_time':'2026-11-20'}]}}):
            self.assertEqual(app.load_futu_cn_quotes(['sz159338'],[]),{})

    def test_ifind_stock_uses_stock_tool_and_hk_requires_qualified_identity(self):
        row={'证券代码':'600519.SH','日期':'2026-11-20','开盘价':10,'最高价':11,'最低价':9,'收盘价':10,'成交量':100,'成交量单位':'股','复权方式':'不复权'}
        with patch.object(app,'ifind_tool',return_value={'rows':[row]}) as tool:
            app.ifind_daily('sh600519')
        self.assertEqual(tool.call_args.args[:2],('stock','get_stock_performance'))
        self.assertTrue(app.ifind_matches({'code':'700.HK'},'00700','HK'))
        self.assertFalse(app.ifind_matches({'code':'00700.SZ'},'00700','HK'))

    def test_tencent_hk_keeps_native_volume_and_today_provisional(self):
        today=app.quote_day(app.dt.datetime.now(app.dt.timezone.utc),'Asia/Hong_Kong').isoformat()
        meta={'market':'HK','symbol':'02800','code':'HK.02800','name':'盈富基金','currency':'HKD','type':'etf'}
        data={'data':{'hk02800':{'qfqday':[[today,'10','10','11','9','123']], 'qt':{'hk02800':['delay','ETF','02800']}}}}
        with patch.object(app,'fetch',return_value=encoded(data)):
            record=app.load_tencent_stock(meta)
        self.assertEqual(record['bars'][0]['vol'],123)
        self.assertFalse(record['completed'])
        self.assertIn('原始单位',record['volume_unit'])
        data['data']['hk02800']['qt']['hk02800'][2]='00700'
        with patch.object(app,'fetch',return_value=encoded(data)), self.assertRaisesRegex(ValueError,'身份'):
            app.load_tencent_stock(meta)


if __name__ == '__main__':
    unittest.main()
