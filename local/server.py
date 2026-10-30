#!/usr/bin/env python3
"""RiskLens local research server. Python stdlib only; binds loopback by default."""
import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import html
import csv
import io
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
ASSETS = [
    ('002281', '光迅科技', None), ('601869', '长飞光纤', None),
    ('688825', '长鑫科技', '2026-07-27'), ('688981', '中芯国际', None),
    ('300033', '同花顺', None), ('300390', '天华新能', None),
    ('515980', '人工智能ETF华富', None), ('588080', '科创50ETF易方达', None),
    ('510300', '沪深300ETF华泰柏瑞', None), ('159338', '中证A500ETF国泰', None),
    ('510500', '中证500ETF南方', None), ('512100', '中证1000ETF南方', None),
    ('159915', '创业板ETF易方达', None),
]
SYMBOLS = {c: ('sh' if c.startswith(('5', '6')) else 'sz') + c for c, _, _ in ASSETS}
SECTOR_ASSETS = [('512480', '半导体ETF国联安', None), ('515880', '通信ETF国泰', None)]
SYMBOLS.update({c: 'sh' + c for c, _, _ in SECTOR_ASSETS})
SINA = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData'
TENCENT = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
QUOTE = 'https://qt.gtimg.cn/q='
TOKEN = secrets.token_urlsafe(24)
NETWORK_LIMIT = threading.BoundedSemaphore(8)
EXTERNAL_SYMBOLS = ['usSOXX', 'usQQQ', 'usNVDA', 'usMU', 'usTSM', 'usAVGO', 'hkHSTECH', 'usVIX']


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def fetch(url):
    host = urllib.parse.urlsplit(url).hostname or ''
    referer = 'https://fundf10.eastmoney.com/' if host.startswith(('fund', 'fundf10')) else 'https://quote.eastmoney.com/' if host.endswith('eastmoney.com') else 'https://finance.sina.com.cn/'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 RiskLensLocal/0.4.2',
        'Referer': referer,
    })
    with NETWORK_LIMIT:
        with urllib.request.urlopen(req, timeout=12) as response:
            body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('行情响应超过8MB')
    return body


def parse_quotes(raw):
    output = {}
    for sym, payload in re.findall(r'v_(\w+)="([^"]*)"', raw.decode('gb18030')):
        fields = payload.split('~')
        if len(fields) <= 30 or not re.fullmatch(r'\d{14}', fields[30]):
            continue
        price = float(fields[3])
        if not math.isfinite(price) or price <= 0:
            continue
        stamp = dt.datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=TZ)
        def value(index):
            try:
                n = float(fields[index])
                return n if math.isfinite(n) else None
            except (ValueError, IndexError):
                return None
        raw_volume = value(6)
        amount_parts = fields[35].split('/') if len(fields) > 35 else []
        amount = float(amount_parts[-1]) if len(amount_parts) == 3 else None
        factor = 1 if fields[2].startswith(('688', '689')) else 100
        output[sym] = {'name': fields[1], 'code': fields[2], 'price': price,
                       'timestamp': stamp.isoformat(), 'date': stamp.date().isoformat(),
                       'open': value(5), 'high': value(33), 'low': value(34),
                       'volume': raw_volume * factor if raw_volume is not None else None,
                       'amount': amount, 'turnover': value(38),
                       'previous_close': float(fields[4]), 'source': '腾讯公开行情',
                       'volume_basis': '科创板原股数' if factor == 1 else '手×100股/份'}
    return output


def parse_sina(raw):
    text = raw.decode('utf-8')
    match = re.search(r'=\s*\((\[.*\])\)\s*;?\s*$', text, re.S)
    if not match:
        raise ValueError('新浪未返回日线数组')
    rows = json.loads(match.group(1))
    if not rows:
        raise ValueError('新浪日线为空')
    return [{'date': r['day'][:10], **{k: float(r[k]) for k in ['open', 'high', 'low', 'close']},
             'vol': float(r['volume']), 'netFlow': None} for r in rows]


def parse_tencent(raw, symbol):
    record = json.loads(raw)['data'][symbol]
    # Request raw day explicitly. Never label fallback day data as qfq.
    rows = record.get('day')
    if not rows:
        raise ValueError('腾讯未返回未复权day数据')
    return [{'date': r[0], 'open': float(r[1]), 'close': float(r[2]),
             'high': float(r[3]), 'low': float(r[4]), 'vol': float(r[5]), 'netFlow': None}
            for r in rows]


def clean_bars(rows, listed, quote):
    output, skipped = [], 0
    current = dt.datetime.now(TZ)
    quote_time = dt.datetime.fromisoformat(quote['timestamp'])
    for row in rows:
        day = dt.date.fromisoformat(row['date']).isoformat()
        if day > quote['date'] or (listed and day < listed):
            skipped += 1
            continue
        values = [row[k] for k in ['open', 'high', 'low', 'close', 'vol']]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('存在非有限OHLC或成交量')
        if min(values[:4]) <= 0:
            skipped += 1  # Known zero-filled prelisting provider placeholders.
            continue
        if row['vol'] < 0 or row['low'] > min(row['open'], row['close']) or row['high'] < max(row['open'], row['close']):
            raise ValueError('OHLC/成交量关系无效')
        row = dict(row)
        row['completed'] = day < quote['date'] or (quote_time.hour >= 15 and current >= quote_time)
        row['observed_at'] = quote['timestamp'] if day == quote['date'] else now()
        output.append(row)
    output.sort(key=lambda r: r['date'])
    if len({r['date'] for r in output}) != len(output):
        raise ValueError('日线日期重复')
    if not output:
        raise ValueError('没有经过校验的收盘日线')
    return output, skipped


def load_asset(asset, quotes, previous=None):
    code, expected, listed = asset
    symbol = SYMBOLS[code]
    quote = quotes.get(symbol)
    if not quote or quote['code'] != code or quote['name'] != expected:
        raise ValueError('行情名称或代码未通过核验，停止该标的取数')
    errors = []
    sources = [
        ('新浪日线', SINA + '?' + urllib.parse.urlencode({'symbol': symbol, 'scale': 240, 'ma': 'no', 'datalen': 640})),
        ('腾讯日线', TENCENT + '?' + urllib.parse.urlencode({'param': symbol + ',day,,,640,'})),
    ]
    for provider, url in sources:
        try:
            body = fetch(url)
            rows = parse_sina(body) if provider == '新浪日线' else parse_tencent(body, symbol)
            bars, skipped = clean_bars(rows, listed, quote)
            # Tencent native K-line volume has board-specific units. Keep them explicit;
            # only ratios use this field. No absolute share-volume calculations are exposed.
            unit = '股/份' if provider == '新浪日线' else '供应商原始单位（仅计算量比）'
            return code, {'source': provider + '（未复权）+ 腾讯名称核验', 'as_of': bars[-1]['date'],
                          'adjustment': 'none', 'completed': bars[-1]['completed'], 'bars': bars,
                          'fundamentals': None, 'fetched_at': now(), 'latest_quote': quote,
                          'volume_unit': unit, 'provider_url': url, 'skipped_rows': skipped,
                          'history_checked_at': now(), 'fallback_errors': errors}
        except Exception as exc:
            errors.append(provider + ': ' + str(exc)[:160])
    raise ValueError('；'.join(errors))


def patch_quote(record, quote):
    """Apply the current cumulative OHLC only after history has been refreshed."""
    keys = ['open', 'high', 'low', 'price', 'volume']
    if not all(isinstance(quote.get(k), (int, float)) and math.isfinite(quote[k]) for k in keys):
        raise ValueError('最新报价缺少完整OHLC/累计量')
    if min(quote[k] for k in keys[:4]) <= 0 or quote['low'] > min(quote['open'], quote['price']) or quote['high'] < max(quote['open'], quote['price']):
        raise ValueError('最新报价OHLC不一致')
    stamp = dt.datetime.fromisoformat(quote['timestamp'])
    completed = stamp.hour >= 15 and dt.datetime.now(TZ) >= stamp
    live = {'date': quote['date'], 'open': quote['open'], 'high': quote['high'], 'low': quote['low'],
            'close': quote['price'], 'vol': quote['volume'], 'netFlow': None, 'completed': completed,
            'observed_at': quote['timestamp'], 'bar_source': '腾讯累计实时报价组装日K'}
    # Only combine homogeneous share/lot units; Sina uses shares. Tencent fallback
    # K-lines on the main board use lots, whereas STAR-board bars already use shares.
    if record['volume_unit'] != '股/份':
        factor = 1 if quote['code'].startswith(('688', '689')) else 100
        for b in record['bars']:
            b['vol'] *= factor
        record['volume_unit'] = '股/份'
    if live['date'] < record['bars'][-1]['date']:
        return  # Never overwrite a newer history endpoint with a delayed quote.
    record['bars'] = [b for b in record['bars'] if b['date'] != live['date']] + [live]
    record.update(as_of=live['date'], completed=completed, latest_quote=quote, fetched_at=now(),
                  closed_as_of=max((b['date'] for b in record['bars'] if b.get('completed')), default=None))


def verify_previous(record, previous, target):
    fresh = next((b for b in record['bars'] if b['date'] == target), None)
    old = next((b for b in (previous or {}).get('bars', []) if b['date'] == target), None)
    changed = bool(fresh and old and any(fresh[k] != old[k] for k in ['open', 'high', 'low', 'close', 'vol']))
    status = 'missing' if not fresh else 'repaired' if changed or (old and old.get('completed') is False) else 'verified'
    record['previous_close_check'] = {'date': target, 'status': status, 'checked_at': record['history_checked_at'],
                                      'source': record['source'], 'changed': changed,
                                      'old_close': old.get('close') if old else None,
                                      'new_close': fresh.get('close') if fresh else None}
    if fresh:
        fresh['completed'] = True
        fresh['finality_basis'] = '本次重新获取历史日线；已越过该交易日'


def json_get(url):
    return json.loads(fetch(url).decode('utf-8'))


def report_rows(code, name, count=16):
    base = 'https://datacenter.eastmoney.com/securities/api/data/v1/get' if name == 'RPT_F10_FINANCE_MAINFINADATA' else 'https://datacenter-web.eastmoney.com/api/data/v1/get'
    url = base + '?' + urllib.parse.urlencode({'reportName': name, 'columns': 'ALL',
          'filter': '(SECURITY_CODE="' + code + '")', 'pageSize': count, 'sortColumns': 'REPORT_DATE', 'sortTypes': -1})
    data = json_get(url)
    rows = (data.get('result') or {}).get('data') or []
    today = dt.datetime.now(TZ).date().isoformat()
    valid = [r for r in rows if r.get('SECURITY_CODE') == code and r.get('NOTICE_DATE') and r['NOTICE_DATE'][:10] <= today and r['REPORT_DATE'][:10] <= today]
    if not valid:
        raise ValueError(name + '未返回已公开报告')
    return valid, url


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def ratio(a, b):
    a, b = number(a), number(b)
    return a / b if a is not None and b is not None and b != 0 else None


def ttm(rows, key, period):
    by_date = {r['REPORT_DATE'][:10]: r for r in rows}
    latest = by_date.get(period, {})
    if period.endswith('12-31'):
        return number(latest.get(key))
    year = int(period[:4])
    values = [number(latest.get(key)), number(by_date.get(str(year - 1) + '-12-31', {}).get(key)),
              number(by_date.get(str(year - 1) + period[4:], {}).get(key))]
    return values[0] + values[1] - values[2] if all(v is not None for v in values) else None


def load_financials(code, expected, quote):
    rows, url = report_rows(code, 'RPT_F10_FINANCE_MAINFINADATA')
    r = rows[0]
    if r.get('SECURITY_NAME_ABBR') != expected or r.get('CURRENCY') != 'CNY':
        raise ValueError('财务主体名称/人民币口径未通过核验')
    period = r['REPORT_DATE'][:10]
    f = {'source': '东方财富财务指标（公司披露汇编）', 'source_url': url, 'period': period,
         'notice_date': r['NOTICE_DATE'][:10], 'currency': 'CNY', 'fetched_at': now(),
         'revenue': number(r.get('TOTALOPERATEREVE')), 'profit': number(r.get('PARENTNETPROFIT')),
         'deductProfit': number(r.get('KCFJCXSYJLR')), 'revenueGrowth': number(r.get('TOTALOPERATEREVETZ')),
         'profitGrowth': number(r.get('PARENTNETPROFITTZ')), 'roe': number(r.get('ROEJQ')),
         'roic': number(r.get('ROIC')), 'grossMargin': number(r.get('XSMLL')), 'netMargin': number(r.get('XSJLL')),
         'operatingCash': number(r.get('NETCASH_OPERATE_PK')), 'debtRatio': number(r.get('ZCFZL')),
         'debt': ratio(r.get('INTEREST_DEBT_RATIO'), 100), 'assets': number(r.get('TOTAL_ASSETS_PK')),
         'inventoryDays': number(r.get('CHZZTS')), 'receivableDays': number(r.get('YSZKZZTS')),
         'currentRatio': number(r.get('LD')), 'rdCapitalization': number(r.get('RCAPITAL_RATIO')),
         'shares': number(r.get('TOTAL_SHARE')), 'floatShares': number(r.get('A_FREE_SHARE')), 'bps': number(r.get('BPS')),
         'sbcRevenue': None, 'conceptRevenue': None, 'epsRevision': None, 'forwardRevenue': None}
    f['cashRatio'] = ratio(f['operatingCash'], f['profit']) if (f['profit'] or 0) > 0 else None
    f['profitTTM'] = ttm(rows, 'PARENTNETPROFIT', period)
    f['revenueTTM'] = ttm(rows, 'TOTALOPERATEREVE', period)
    cap = quote['price'] * f['shares'] if f['shares'] else None
    f.update(marketCap=ratio(cap, 1e8), pe=ratio(cap, f['profitTTM']) if (f['profitTTM'] or 0) > 0 else None,
             pb=ratio(quote['price'], f['bps']) if (f['bps'] or 0) > 0 else None,
             ps=ratio(cap, f['revenueTTM']) if (f['revenueTTM'] or 0) > 0 else None,
             valuation_date=quote['timestamp'], valuation_basis='最新报价×最近披露总股本；PB=报价/BPS；PE/PS按TTM重建')
    year = int(period[:4])
    annual = {v['REPORT_DATE'][:10]: v for v in rows if v['REPORT_DATE'][5:10] == '12-31'}
    end = str(year - 1) + '-12-31'
    begin = str(year - 4) + '-12-31'
    for key, out in [('TOTALOPERATEREVE', 'revenueCagr'), ('PARENTNETPROFIT', 'profitCagr')]:
        a, b = annual.get(begin, {}).get(key), annual.get(end, {}).get(key)
        f[out] = ((b / a) ** (1 / 3) - 1) * 100 if number(a) is not None and number(b) is not None and a > 0 and b > 0 else None
    a, b = annual.get(begin, {}).get('XSJLL'), annual.get(end, {}).get('XSJLL')
    f['marginDelta'] = b - a if number(a) is not None and number(b) is not None else None
    growths = [v['PARENTNETPROFITTZ'] / 100 for v in annual.values() if number(v.get('PARENTNETPROFITTZ')) is not None]
    f['epsStd'] = (sum((g - sum(growths) / len(growths)) ** 2 for g in growths) / len(growths)) ** .5 if len(growths) >= 3 else None
    # Parent-profit growth dispersion is distinct from EPS growth dispersion.
    # Keep the exact model field unavailable rather than substitute another metric.
    f['profitGrowthStd'] = f.pop('epsStd')
    annual_eps = sorted((k, v.get('EPSJB')) for k, v in annual.items() if number(v.get('EPSJB')) is not None)
    eps_growths = [b[1] / a[1] - 1 for a, b in zip(annual_eps, annual_eps[1:]) if a[1] > 0 and b[1] > 0 and int(b[0][:4]) - int(a[0][:4]) == 1]
    f['epsStd'] = (sum((g - sum(eps_growths) / len(eps_growths)) ** 2 for g in eps_growths) / len(eps_growths)) ** .5 if len(eps_growths) >= 3 else None
    history = [{k: v.get(k) for k in ['REPORT_DATE', 'NOTICE_DATE', 'TOTALOPERATEREVE', 'PARENTNETPROFIT',
                'TOTALOPERATEREVETZ', 'PARENTNETPROFITTZ', 'ROEJQ', 'XSMLL', 'NETCASH_OPERATE_PK', 'ZCFZL']}
               for v in rows[:12]]
    try:
        cash, cash_url = report_rows(code, 'RPT_DMSK_FN_CASHFLOW')
        match = next((v for v in cash if v['REPORT_DATE'][:10] == period), None)
        capex = number(match.get('CONSTRUCT_LONG_ASSET')) if match else None
        f['capex'] = capex
        f['freeCashFlow'] = f['operatingCash'] - capex if f['operatingCash'] is not None and capex is not None else None
        ocf_ttm, capex_ttm = ttm(cash, 'NETCASH_OPERATE', period), ttm(cash, 'CONSTRUCT_LONG_ASSET', period)
        f['freeCashFlowTTM'] = ocf_ttm - capex_ttm if ocf_ttm is not None and capex_ttm is not None else None
        f['cash_source_url'] = cash_url
    except Exception as exc:
        f['cash_error'] = str(exc)[:160]
    return f, history


def load_announcements(code):
    url = 'https://np-anotice-stock.eastmoney.com/api/security/ann?' + urllib.parse.urlencode({
        'sr': -1, 'page_size': 8, 'page_index': 1, 'ann_type': 'F' if code.startswith(('5', '15')) else 'A', 'stock_list': code})
    data = json_get(url)
    output = []
    for r in (data.get('data') or {}).get('list', []):
        if not any(c.get('stock_code') == code for c in r.get('codes', [])):
            continue
        day = r.get('notice_date', '')[:10]
        if day > dt.datetime.now(TZ).date().isoformat():
            continue
        output.append({'date': day, 'title': r['title'], 'categories': [v.get('column_name') for v in r.get('columns', [])],
                       'url': 'https://data.eastmoney.com/notices/detail/' + code + '/' + r['art_code'] + '.html',
                       'source': '公司公告（东方财富索引）'})
    return output


def load_flows(code):
    symbol = SYMBOLS[code]
    url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_lscjfb?' + urllib.parse.urlencode({
        'page': 1, 'num': 170, 'sort': 'opendate', 'asc': 0, 'daima': symbol})
    rows = json_get(url)
    if not isinstance(rows, list):
        raise ValueError('新浪资金流未返回数组')
    output = []
    for r in rows:
        if not r.get('opendate'):
            continue
        jumbo, large = number(r.get('r0_net')), number(r.get('r1_net'))
        output.append({'date': r['opendate'][:10], 'netFlow': jumbo + large if jumbo is not None and large is not None else None,
                       'totalNetFlow': number(r.get('netamount')), 'source': '新浪超大单+大单净额（交易分类，非机构身份）'})
    return output


def load_holdings(code):
    current = dt.datetime.now(TZ)
    url = 'https://fundf10.eastmoney.com/FundArchivesDatas.aspx?' + urllib.parse.urlencode({
        'type': 'jjcc', 'code': code, 'topline': 10, 'year': current.year, 'month': ((current.month - 1) // 3) * 3,
        'rt': .5})
    text = fetch(url).decode('utf-8')
    stamp = re.search(r'截止至：.*?(\d{4}-\d{2}-\d{2})', text)
    tbody = re.search(r'<tbody>(.*?)</tbody>', text, re.S)
    if not stamp or not tbody:
        raise ValueError('未返回可解析的ETF季报前十大持股')
    holdings = []
    for tr in re.findall(r'<tr>(.*?)</tr>', tbody.group(1), re.S):
        cells = [html.unescape(re.sub(r'<[^>]*>', '', td)).strip() for td in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if len(cells) < 9 or not re.fullmatch(r'\d{6}', cells[1]):
            continue
        holdings.append({'code': cells[1], 'name': cells[2], 'weight': float(cells[6].replace('%', ''))})
    if not holdings:
        raise ValueError('ETF持仓列表为空')
    return {'period': stamp.group(1), 'holdings': holdings, 'top10_weight': sum(r['weight'] for r in holdings[:10]),
            'source': '基金季报（天天基金汇编）', 'url': url, 'fetched_at': now()}


def load_nav(code):
    url = 'https://fund.eastmoney.com/pingzhongdata/' + code + '.js?v=' + dt.datetime.now(TZ).strftime('%Y%m%d')
    text = fetch(url).decode('utf-8')
    match = re.search(r'var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);', text, re.S)
    if not match:
        raise ValueError('基金净值数组不可用')
    rows = json.loads(match.group(1))
    if not rows:
        raise ValueError('基金净值为空')
    latest = rows[-1]
    return {'date': dt.datetime.fromtimestamp(latest['x'] / 1000, TZ).date().isoformat(),
            'value': latest['y'], 'source': '天天基金历史单位净值', 'url': url, 'fetched_at': now()}


def load_external():
    raw = fetch(QUOTE + ','.join(EXTERNAL_SYMBOLS)).decode('gb18030')
    output = []
    for symbol, payload in re.findall(r'v_(\w+)="([^"]*)"', raw):
        f = payload.split('~')
        if len(f) < 35 or not f[3]:
            continue
        stamp = f[30]
        try:
            price, previous = float(f[3]), float(f[4])
            if price <= 0 or previous <= 0:
                continue
        except ValueError:
            continue
        source_day = stamp[:10].replace('/', '-')
        try:
            lag = (dt.datetime.now(TZ).date() - dt.date.fromisoformat(source_day)).days
        except ValueError:
            lag = 999
        output.append({'symbol': symbol, 'name': f[1], 'price': price, 'change': (price / previous - 1) * 100,
                       'timestamp': stamp, 'timezone': '香港时间' if symbol.startswith('hk') else '美东市场时间',
                       'stale': lag > (1 if symbol.startswith('hk') else 4), 'source': '腾讯跨市场行情',
                       'url': 'https://gu.qq.com/' + symbol})
    return output


def cache_locations(requested):
    """Keep the existing cache, with writable per-user alternatives."""
    home = Path.home()
    app_data = (home / 'Library' / 'Application Support' / 'RiskLens' if sys.platform == 'darwin'
                else home / '.local' / 'share' / 'risklens')
    user = str(os.getuid()) if hasattr(os, 'getuid') else os.environ.get('USERNAME', 'local')
    candidates = [Path(requested).expanduser(), app_data / 'market-cache.json']
    try:
        candidates.append(Path(tempfile.gettempdir()) / ('risklens-' + user) / 'market-cache.json')
    except OSError:
        pass
    return list(dict.fromkeys(candidates))


class DataStore:
    def __init__(self, cache):
        self.requested_cache = Path(cache).expanduser()
        self.cache = self.requested_cache
        self.lock = threading.Lock()
        self.state = {'busy': False, 'done': 0, 'total': len(ASSETS), 'stage': 'idle', 'errors': {}, 'last_refresh': None,
                      'cache_path': str(self.cache), 'cache_warning': None, 'cache_persisted': False}
        self.snapshot = {'data_kind': 'local', 'instruments': {}, 'as_of': None}
        # A previous run may have switched directories. Resume its newest valid cache.
        for candidate in cache_locations(self.requested_cache):
            try:
                obj = json.loads(candidate.read_text(encoding='utf-8'))
                if (isinstance(obj, dict) and obj.get('data_kind') == 'local' and isinstance(obj.get('instruments'), dict)
                        and (not self.snapshot.get('fetched_at') or str(obj.get('fetched_at') or '') > self.snapshot['fetched_at'])):
                    self.snapshot, self.cache = obj, candidate
                    self.state.update(last_refresh=obj.get('fetched_at'), cache_path=str(candidate), cache_persisted=True)
            except (OSError, ValueError, TypeError):
                continue
        if self.cache != self.requested_cache:
            self.state['cache_warning'] = '已使用备用缓存目录：' + str(self.cache.parent)

    def start(self):
        with self.lock:
            if self.state['busy']:
                return False
            self.state.update(busy=True, done=0, errors={}, started_at=now())
        threading.Thread(target=self.refresh, daemon=True).start()
        return True

    def refresh(self):
        instruments, errors = {}, {}
        try:
            with self.lock:
                previous = json.loads(json.dumps(self.snapshot))
                self.state['stage'] = '复核前一交易日收盘K线'
            quotes = parse_quotes(fetch(QUOTE + ','.join(SYMBOLS.values())))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(load_asset, a, quotes): a[0] for a in ASSETS}
                for future in concurrent.futures.as_completed(futures):
                    code = futures[future]
                    try:
                        key, record = future.result()
                        instruments[key] = record
                    except Exception as exc:
                        errors[code] = str(exc)[:400]
                    with self.lock:
                        self.state['done'] += 1
            reference = instruments.get('510300') or next(iter(instruments.values()), None)
            reference_day = max((q['date'] for q in quotes.values()), default=dt.datetime.now(TZ).date().isoformat())
            target = max((b['date'] for b in (reference or {}).get('bars', []) if b['date'] < reference_day), default=None)
            for code, record in instruments.items():
                if target:
                    verify_previous(record, previous.get('instruments', {}).get(code), target)
            with self.lock:
                self.state['stage'] = '更新当天实时日K'
            fresh_quotes = parse_quotes(fetch(QUOTE + ','.join(SYMBOLS.values())))
            for code, record in instruments.items():
                try:
                    patch_quote(record, fresh_quotes[SYMBOLS[code]])
                except Exception as exc:
                    record['live_error'] = str(exc)[:200]
                    errors[code + ':live'] = str(exc)[:200]
                old = previous.get('instruments', {}).get(code, {})
                for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'research_errors', 'research_fetched_at']:
                    if key in old:
                        record[key] = old[key]
            # Publish a detached price snapshot before slower research requests finish.
            # Charts can update immediately while all research keeps its own timestamps.
            if instruments:
                interim = {'schema_version': 4, 'data_kind': 'local', 'as_of': max(r['as_of'] for r in instruments.values()),
                           'closed_as_of': min(r.get('closed_as_of') or r['as_of'] for r in instruments.values()),
                           'previous_session': target, 'fetched_at': now(), 'instruments': instruments,
                           'external': previous.get('external', []), 'sectors': previous.get('sectors', []), 'errors': errors}
                self.commit_snapshot(json.loads(json.dumps(interim)))
            with self.lock:
                self.state['stage'] = '补充财务、公告与产业证据'

            def research(asset):
                code, expected, _ = asset
                if code not in instruments:
                    return
                rec = instruments[code]
                old = previous.get('instruments', {}).get(code, {})
                age = 1e9
                try:
                    age = (dt.datetime.now(TZ) - dt.datetime.fromisoformat(old['research_fetched_at'])).total_seconds()
                except (KeyError, ValueError, TypeError):
                    pass
                if age < 21600 and not old.get('research_errors'):
                    for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'research_errors', 'research_fetched_at']:
                        if key in old:
                            rec[key] = old[key]
                else:
                    issues = {}
                    if code.startswith(('5', '15')):
                        try:
                            rec['etf'] = load_holdings(code)
                        except Exception as exc:
                            issues['holdings'] = str(exc)[:180]
                        try:
                            rec['announcements'] = load_announcements(code)
                        except Exception as exc:
                            issues['announcements'] = str(exc)[:180]
                        try:
                            rec['nav'] = load_nav(code)
                        except Exception as exc:
                            issues['nav'] = str(exc)[:180]
                    else:
                        for key, operation in [('financials', lambda: load_financials(code, expected, rec['latest_quote'])),
                                               ('announcements', lambda: load_announcements(code)), ('flows', lambda: load_flows(code))]:
                            try:
                                value = operation()
                                if key == 'financials':
                                    rec['fundamentals'], rec['financial_history'] = value
                                else:
                                    rec[key] = value
                            except Exception as exc:
                                issues[key] = str(exc)[:180]
                                # Preserve research with its original timestamps on a partial outage.
                                for old_key in {'financials': ['fundamentals', 'financial_history'], 'announcements': ['announcements'], 'flows': ['flows']}[key]:
                                    if old_key in old:
                                        rec[old_key] = old[old_key]
                    rec['research_errors'] = issues
                    rec['research_fetched_at'] = now()
                if not code.startswith(('5', '15')) and age < 21600:
                    try:
                        rec['flows'] = load_flows(code)
                    except Exception as exc:
                        rec.setdefault('research_errors', {})['flows'] = str(exc)[:180]
                flow_map = {r['date']: r['netFlow'] for r in rec.get('flows', [])}
                for bar in rec['bars']:
                    bar['netFlow'] = flow_map.get(bar['date'])
                f = rec.get('fundamentals')
                if f and f.get('shares'):
                    cap = rec['latest_quote']['price'] * f['shares']
                    f.update(marketCap=cap / 1e8, pe=ratio(cap, f.get('profitTTM')) if (f.get('profitTTM') or 0) > 0 else None,
                             ps=ratio(cap, f.get('revenueTTM')) if (f.get('revenueTTM') or 0) > 0 else None,
                             pb=ratio(rec['latest_quote']['price'], f.get('bps')) if (f.get('bps') or 0) > 0 else None,
                             valuation_date=rec['latest_quote']['timestamp'])
                    denominator = f.get('floatShares') or f['shares']
                    turnovers = [100 * b['vol'] / denominator for b in rec['bars'][-60:] if b.get('completed')]
                    f['turnover3m'] = sum(turnovers) / len(turnovers) if len(turnovers) >= 40 else None
                    f['turnover_basis'] = '成交股数/最近披露流通A股本' if f.get('floatShares') else '成交股数/最近披露总股本'

            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                for _ in pool.map(research, ASSETS):
                    pass
            try:
                external = load_external()
            except Exception as exc:
                external = previous.get('external', [])
                errors['external'] = str(exc)[:180]
            sectors = []
            for a in SECTOR_ASSETS:
                try:
                    _, sector = load_asset(a, fresh_quotes)
                    patch_quote(sector, fresh_quotes[SYMBOLS[a[0]]])
                    sectors.append({'code': a[0], 'name': a[1], **sector})
                except Exception as exc:
                    errors['sector:' + a[0]] = str(exc)[:180]
            snapshot = {'schema_version': 4, 'data_kind': 'local',
                        'as_of': max((r['as_of'] for r in instruments.values()), default=None),
                        'closed_as_of': min((r.get('closed_as_of') or r['as_of'] for r in instruments.values()), default=None),
                        'fetched_at': now(), 'previous_session': target, 'external': external, 'sectors': sectors,
                        'instruments': instruments, 'errors': errors}
            # Preserve the previous successful cache only if every request failed.
            if instruments:
                self.commit_snapshot(snapshot)
        except Exception as exc:
            errors['provider'] = str(exc)[:400]
        finally:
            with self.lock:
                self.state.update(busy=False, stage='完成', errors=errors, last_refresh=now())

    def commit_snapshot(self, snapshot):
        # Fresh market data remains usable even when local persistence is unavailable.
        with self.lock:
            self.snapshot = snapshot
        payload = json.dumps(snapshot, ensure_ascii=False)
        candidates = list(dict.fromkeys([self.cache] + cache_locations(self.requested_cache)))
        failure = None
        for candidate in candidates:
            temp = None
            try:
                candidate.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                # A unique 0600 file avoids stale/root-owned market-cache.tmp files
                # and collisions between two local processes. Replace atomically.
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                                 prefix='.market-cache-', suffix='.tmp',
                                                 dir=candidate.parent, delete=False) as handle:
                    temp = Path(handle.name)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, candidate)
                self.cache = candidate
                warning = ('已自动使用备用缓存目录：' + str(candidate.parent)
                           if candidate != self.requested_cache else None)
                with self.lock:
                    self.state.update(cache_path=str(candidate), cache_warning=warning, cache_persisted=True)
                return
            except OSError as exc:
                failure = exc
            finally:
                if temp is not None:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
        warning = '行情已更新，但缓存无法写入；当前数据保留在内存，重启后需重新获取。' + str(failure)
        with self.lock:
            self.state.update(cache_path=None, cache_warning=warning, cache_persisted=False)


class Handler(BaseHTTPRequestHandler):
    server_version = 'RiskLensLocal/0.4.2'

    def log_message(self, format, *args):
        if self.path not in ['/api/status', '/api/snapshot']:
            super().log_message(format, *args)

    def send(self, payload, kind='application/json; charset=utf-8', status=200):
        body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/status':
            with self.server.store.lock:
                result = {**self.server.store.state, 'coverage': len(self.server.store.snapshot['instruments']),
                          'as_of': self.server.store.snapshot.get('as_of'),
                          'closed_as_of': self.server.store.snapshot.get('closed_as_of'),
                          'fetched_at': self.server.store.snapshot.get('fetched_at'), 'environment': 'local-server'}
            return self.send(result)
        if path == '/api/snapshot':
            with self.server.store.lock:
                result = self.server.store.snapshot
            return self.send(result)
        # Deliberate static allowlist: never serve source, cache, credentials or .git.
        name = 'index.html' if path == '/' else path.lstrip('/')
        if name not in ['index.html', 'styles.css', 'workbench.css', 'data.js', 'engine.js', 'app.js',
                        'views.js', 'research-views.js', 'strategy-views.js', 'local-connector.js', 'bootstrap.js',
                        'lightweight-charts.js', 'lightweight-charts.LICENSE.txt', 'interactive-chart.js', 'evidence-views.js']:
            return self.send({'error': 'Not found'}, status=404)
        embedded = globals().get('EMBEDDED_ASSETS', {})
        if name in embedded:
            text = embedded[name]
        else:
            text = (ROOT / 'dist' / name).read_text(encoding='utf-8')
        if name == 'index.html':
            text = text.replace('</head>', '<script>window.RISKLENS_LOCAL=true;window.RISKLENS_TOKEN=' + json.dumps(TOKEN) + ';</script></head>')
        kind = 'text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/plain' if name.endswith('.txt') else 'application/javascript'
        self.send(text.encode(), kind + '; charset=utf-8')

    def do_POST(self):
        if self.path != '/api/refresh':
            return self.send({'error': 'Not found'}, status=404)
        origin = self.headers.get('Origin')
        host = self.headers.get('Host', '')
        if self.headers.get('X-RiskLens-Token') != TOKEN or (origin and urllib.parse.urlsplit(origin).netloc != host):
            return self.send({'error': '仅允许当前页面发起刷新'}, status=403)
        started = self.server.store.start()
        self.send({'started': started}, status=202 if started else 200)


def main():
    parser = argparse.ArgumentParser(description='观澜本地数据环境（只读行情，不连接券商）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cache', default=str(Path.home() / '.risklens' / 'market-cache.json'))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-refresh', action='store_true')
    parser.add_argument('--strictPort', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    store = DataStore(args.cache)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = store
    if not args.no_refresh:
        store.start()
    url = 'http://' + args.host + ':' + str(args.port) + '/'
    print('观澜本地研究环境：' + url, flush=True)
    print('先复核前日收盘，再刷新当天K线；财务、资金、公告及ETF证据按来源更新。Ctrl+C停止。', flush=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()




if __name__ == '__main__':
    main()
