import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('briefing_server', Path(__file__).resolve().parents[1] / 'local/server.py')
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def instant(days=0):
    return app.dt.datetime.now(app.TZ) - app.dt.timedelta(days=days, minutes=1)


def item(title, body='', days=0, url='https://news.futunn.com/post/fixture'):
    return {'title': title, 'text': body, 'published_at': instant(days).isoformat(), 'url': url, 'source': 'fixture'}


def component(score=50, coverage=1):
    return {'score': score, 'coverage': coverage, 'as_of': instant().date().isoformat(), 'reason': 'fixture', 'items': [], 'errors': []}


class SinaFallbackTests(unittest.TestCase):
    def test_us_history_calls_sina_only_after_the_other_five_sources_fail(self):
        meta = {'code': 'US.AAPL', 'symbol': 'AAPL', 'name': '苹果', 'market': 'US', 'currency': 'USD'}
        for name in ('load_futu_stock', 'load_tencent_stock', 'read_yahoo_chart', 'load_ifind_stock', 'load_massive_stock'):
            p = patch.object(app, name, side_effect=ValueError(name + ' unavailable'))
            p.start(); self.addCleanup(p.stop)
        record = {'bars': [], 'as_of': instant(1).date().isoformat(), 'history_provider': '新浪', 'completed': False}
        trace = []
        with patch.object(app, 'load_sina_stock', return_value=record) as sina:
            _, result = app.traced_quote_call(trace.append, '日线', 'US.AAPL', app.load_international_asset, meta)
        self.assertEqual(result['history_provider'], '新浪')
        self.assertEqual([r['provider'] for r in trace], ['富途', '腾讯', 'Yahoo Finance', '同花顺 iFinD', 'Massive', '新浪'])
        sina.assert_called_once_with(meta)

    def test_etf_daily_sina_is_last_and_keeps_its_share_volume_units(self):
        date = instant(1).date().isoformat()
        quote = {'code': '159338', 'name': 'ETF', 'timestamp': date+'T15:00:00+08:00', 'date': date}
        rows = [{'day': date, 'open': '10', 'high': '11', 'low': '9', 'close': '10', 'volume': '1234'}]
        def fetch(url):
            if url.startswith(app.TENCENT):
                raise ValueError('腾讯 unavailable')
            return ('var _=('+json.dumps(rows)+');').encode()
        trace = []
        with patch.object(app, 'pingan_daily', side_effect=app.QuoteUnavailable('未配置')), \
             patch.object(app, 'ifind_daily', side_effect=app.QuoteUnavailable('未配置')), patch.object(app, 'fetch', side_effect=fetch):
            _, record = app.traced_quote_call(trace.append, '日线', '159338', app.load_asset,
                                              ('159338', 'ETF', None), {'sz159338': quote})
        self.assertEqual(record['history_provider'], '新浪')
        self.assertEqual(record['volume_unit'], '股/份')
        self.assertEqual(record['bars'][0]['vol'], 1234)
        self.assertEqual([r['provider'] for r in trace], ['平安证券日线', '同花顺 iFinD 日线', '腾讯日线', '新浪日线'])

    def test_sina_forex_uses_close_and_preserves_usdcnh_direction(self):
        date = instant(1).date().isoformat()
        raw = ('var("'+date+',6.70,6.60,6.80,6.72,|");').encode()
        with patch.object(app, 'fetch', return_value=raw) as fetch:
            result, _ = app.load_sina_global('CNH', 'CNH=X', False)
        self.assertEqual(result['history'], [{'date': date, 'close': 6.72}])
        self.assertIn('fx_susdcnh', fetch.call_args.args[0])
        with patch.object(app, 'fetch') as fetch:
            with self.assertRaises(app.QuoteUnavailable):
                app.load_sina_global('A50', 'XIN9.FGI', False)
            fetch.assert_not_called()

    def test_sina_jsonp_does_not_execute_callback_or_script_text(self):
        for raw in (b'var({"__ERROR":3});', b'var(__import__("os"));', b'var([]); malicious()'):
            with self.assertRaises(ValueError):
                app.sina_jsonp(raw)

    def test_duplicate_sina_dates_cannot_inflate_risk_history(self):
        date = instant(1).date().isoformat()
        row = date+',6.70,6.60,6.80,6.72,'
        with patch.object(app, 'fetch', return_value=('var("'+row+'|'+row+'|");').encode()):
            with self.assertRaisesRegex(ValueError, '日期重复'):
                app.load_sina_global('CNH', 'CNH=X', False)

    def test_sina_search_verifies_exchange_and_excludes_non_equity_categories(self):
        rows = 'sz159338,203,159338,sz159338,中证A500ETF国泰;AAPL,41,aapl,aapl,苹果;BTCAAPL,71,btcaapl,btcaapl,暗盘;KAP.L,41,kap.l,kap.l,伦敦股'
        raw = ('var var='+json.dumps(rows, ensure_ascii=True)+';').encode()
        with patch.object(app, 'fetch', return_value=raw):
            cn = app.search_sina_securities('159338', 'CN')
            us = app.search_sina_securities('aapl', 'US')
        self.assertEqual((cn[0]['code'], cn[0]['type']), ('159338', 'etf'))
        self.assertEqual([r['code'] for r in us], ['US.AAPL'])


class BriefingTests(unittest.TestCase):
    def test_news_research_can_refresh_without_changing_failed_price_snapshot_time(self):
        geo = {'method': 'public-evidence-v2', 'items': [{'kind': 'politics', 'news': [item('新的出口许可线索')]}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'cache_locations', return_value=[]), \
             patch.object(app, 'load_cn_quotes', side_effect=ConnectionError('all price sources unavailable')), \
             patch.object(app, 'load_external', return_value=[]), patch.object(app, 'load_geopolitics', return_value=geo):
            store = app.DataStore(Path(folder)/'cache.json')
            old_time = instant(1).isoformat()
            store.snapshot['fetched_at'] = old_time
            store.refresh()
        self.assertEqual(store.snapshot['fetched_at'], old_time)
        self.assertEqual(store.snapshot['geopolitics'], geo)
        self.assertEqual(store.state['price_result'], 'failed')

    def test_public_feed_decodes_html_and_literal_unicode_without_future_or_stale_rows(self):
        raw = {'code': 0, 'data': [
            {'title': r'\u003cb\u003e出口政策\u003c/b\u003e', 'desc': '<script>unsafe()</script>新闻正文',
             'publish_time': int(instant().timestamp()), 'url': 'https://news.futunn.com/post/1'},
            {'title': 'future', 'publish_time': int(instant(-1).timestamp()), 'url': 'https://example.test'},
            {'title': 'stale', 'publish_time': int(instant(10).timestamp()), 'url': 'https://example.test'},
            {'title': 'unsafe', 'publish_time': int(instant().timestamp()), 'url': 'javascript:bad()'}]}
        with patch.object(app, 'fetch', return_value=json.dumps(raw).encode()) as fetch:
            rows = app.futu_public_items('news_search', '半导体', 10, 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['title'], rows[0]['text']), ('出口政策', '新闻正文'))
        self.assertIn('news_type=1', fetch.call_args.args[0])
        self.assertIn('user_agent', fetch.call_args.kwargs)

    def test_news_deduplicates_and_caps_headline_only_coverage(self):
        rows = [item('美国新增制裁影响半导体出口许可的报道'+str(i), url='https://news.futunn.com/post/'+str(i)) for i in range(3)]
        with patch.object(app, 'futu_public_items', return_value=rows):
            result = app.briefing_news('politics')
        self.assertEqual(len(result['items']), 3)
        self.assertEqual(result['score'], 80)
        self.assertEqual(result['coverage'], .5)
        self.assertIsNone(app.news_cue(item('辟谣：未发生导弹袭击'), 'war'))
        self.assertEqual(app.news_cue(item('双方达成停火协议'), 'war')[0], 25)
        self.assertNotEqual(app.news_cue(item('双方停火谈判破裂，冲突加剧'), 'war')[0], 25)

    def test_community_filters_spam_duplicates_and_preserves_other_symbols_on_failure(self):
        rows = [item('关税政策风险可能影响需求下滑，市场正在下跌。研究理由'+str(i)) for i in range(6)]
        rows += [rows[0], item('加群免费荐股，关税风险现在下跌'), item('🚀🚀'), item('普通公司经营数据没有相关主题')]
        with patch.object(app, 'futu_public_items', side_effect=[rows, ValueError('HTTP 503')]):
            result = app.briefing_community('politics')
        self.assertEqual(result['post_count'], 6)
        self.assertEqual(result['counts']['bearish'], 6)
        self.assertEqual(result['symbols'][1]['status'], 'error')
        self.assertEqual(len(result['top_opinions']), 3)
        self.assertEqual(result['score'], 100)
        self.assertEqual(app.comment_label('监管风险让我不看好后市'), 'bearish')
        self.assertEqual(app.comment_label('出口问题缓和，不看空后续行情'), 'bullish')

    def test_too_few_community_posts_never_become_a_confident_zero(self):
        with patch.object(app, 'futu_public_items', return_value=[item('出口许可风险仍需核实，先保持观察和研究')]):
            result = app.briefing_community('politics')
        self.assertIsNone(result['score'])
        self.assertEqual(result['coverage'], 0)

    def gap_fixture(self):
        today = app.dt.datetime.now(app.TZ).date()
        rows = [{'date': (today-app.dt.timedelta(days=121-i)).isoformat(), 'close': 100-i*.3, 'completed': True} for i in range(121)]
        catalog = [{'code': str(i), 'name': '样本'+str(i), 'type': 'stock', 'market': 'CN', 'watch': True} for i in range(3)]
        f = {'notice_date': instant(2).date().isoformat(), 'profit': 1, 'deductProfit': 1, 'operatingCash': 1, 'revenueGrowth': 1}
        return {m['code']: {'bars': copy.deepcopy(rows), 'fundamentals': dict(f)} for m in catalog}, catalog

    def test_gap_excludes_provisional_prices_and_does_not_call_declines_mispricing(self):
        instruments, catalog = self.gap_fixture()
        first = app.briefing_gap(instruments, catalog)
        for record in instruments.values():
            record['bars'].append({'date': app.dt.datetime.now(app.TZ).date().isoformat(), 'close': 99999, 'completed': False})
        result = app.briefing_gap(instruments, catalog)
        self.assertEqual(result, first)
        self.assertEqual(result['score'], 100)
        self.assertTrue(all('待核实' in r['classification'] for r in result['items']))
        instruments['0']['fundamentals']['notice_date'] = instant(300).date().isoformat()
        self.assertIsNone(app.briefing_gap(instruments, catalog)['items'][0]['financial_health'])

    def test_three_source_coverage_and_errors_are_preserved_without_gpr_or_review(self):
        gap = {**component(40), 'sample_count': 3, 'scope_count': 3, 'items': []}
        community = {**component(80), 'post_count': 10, 'counts': {'bullish': 2, 'bearish': 8, 'neutral': 0}}
        with patch.object(app, 'briefing_news', side_effect=ValueError('news HTTP 503')), \
             patch.object(app, 'briefing_community', return_value=community), patch.object(app, 'briefing_gap', return_value=gap):
            result = app.load_geopolitics({}, [])
        self.assertEqual(result['method'], 'public-evidence-v2')
        for row in result['items']:
            self.assertAlmostEqual(row['coverage'], .6)
            self.assertAlmostEqual(row['score'], (80*25+40*35)/60)
            self.assertEqual([p['id'] for p in row['components']], ['news', 'community', 'gap'])
            self.assertIn('503', row['components'][0]['reason'])
            self.assertNotIn('review', row)

    def test_missing_every_source_keeps_scores_missing_and_full_uncertainty(self):
        with patch.object(app, 'briefing_news', side_effect=ValueError('unavailable')), \
             patch.object(app, 'briefing_community', side_effect=ValueError('unavailable')):
            result = app.load_geopolitics({}, [])
        for row in result['items']:
            self.assertIsNone(row['score'])
            self.assertEqual((row['coverage'], row['low'], row['high']), (0, 0, 100))
            self.assertIn('证据覆盖不足', row['conclusion'])


if __name__ == '__main__':
    unittest.main()
