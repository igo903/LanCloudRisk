#!/usr/bin/env python3
"""RiskLens local research server. Python stdlib only; binds loopback by default."""
import argparse
import concurrent.futures
import datetime as dt
import errno
import ipaddress
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
import urllib.error
import webbrowser
import html
import csv
import io
import sys
import tempfile
import struct
import uuid
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
SINA_QUOTE = 'https://hq.sinajs.cn/list='
IFIND_ENDPOINT = 'https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-{}-mcp'
MASSIVE_API = 'https://api.massive.com'
PINGAN_QUOTE = 'https://ai.stock.pingan.com/restapi/hangqing/adapter/hangQing/stockDynamic'
MARKET_KEY_SETTINGS = {
    'massive': ('Massive', 'MASSIVE_API_KEY'),
    'pingan': ('平安证券', 'PINGAN_SKILL_APIKEY'),
}
MARKET_KEY_TEST_PATHS = {'/api/ifind/test': 'ifind', '/api/market-keys/massive/test': 'massive',
                         '/api/market-keys/pingan/test': 'pingan'}
TOKEN = secrets.token_urlsafe(24)
NETWORK_LIMIT = threading.BoundedSemaphore(8)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXY_MODE = 'auto'
EXTERNAL_SYMBOLS = ['usSOXX', 'usQQQ', 'usNVDA', 'usMU', 'usTSM', 'usAVGO', 'hkHSTECH', 'usVIX']


class NoMarketRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


IFIND_OPENER = urllib.request.build_opener(NoMarketRedirect())
IFIND_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoMarketRedirect())
IFIND_LOCK = threading.Lock()
IFIND_SETTINGS_LOCK = threading.Lock()
MARKET_KEY_LOCKS = {provider: threading.Lock() for provider in MARKET_KEY_SETTINGS}
IFIND_NEXT_CALL = 0


def now():
    return dt.datetime.now(TZ).isoformat(timespec='seconds')


def local_proxy_address(host, scheme):
    proxy = urllib.request.getproxies().get(scheme)
    if not proxy or urllib.request.proxy_bypass(host):
        return None
    try:
        address = urllib.parse.urlsplit(proxy if '://' in proxy else '//' + proxy)
        name = address.hostname or ''
        if name.lower() != 'localhost' and not ipaddress.ip_address(name).is_loopback:
            return None
        port = address.port or (443 if address.scheme == 'https' else 80)
        return f'[{name}]:{port}' if ':' in name else f'{name}:{port}'
    except ValueError:
        return None


def connection_refused(exc):
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ConnectionRefusedError) or getattr(reason, 'errno', None) in (errno.ECONNREFUSED, 61, 10061)


def fetch(url):
    target = urllib.parse.urlsplit(url)
    host = target.hostname or ''
    referer = 'https://www.sse.com.cn/' if host == 'query.sse.com.cn' else 'https://fundf10.eastmoney.com/' if host.startswith(('fund', 'fundf10')) else 'https://quote.eastmoney.com/' if host.endswith('eastmoney.com') else 'https://finance.sina.com.cn/'
    headers = {
        'User-Agent': 'Mozilla/5.0 RiskLensLocal/0.9.0',
        'Referer': referer,
    }
    def read(open_url):
        # ProxyHandler mutates the request. Each route needs a fresh request.
        req = urllib.request.Request(url, headers=headers)
        with open_url(req, timeout=12) as response:
            return response.read(8 * 1024 * 1024 + 1)
    with NETWORK_LIMIT:
        direct = PROXY_MODE == 'direct'
        try:
            body = read(DIRECT_OPENER.open if direct else urllib.request.urlopen)
        except OSError as exc:
            if not connection_refused(exc):
                raise
            proxy = None if direct else local_proxy_address(target.netloc, target.scheme)
            if not proxy:
                raise ConnectionError(f'{host}：行情连接被拒绝，请检查网络或代理设置') from exc
            message = f'{host}：本机代理 {proxy} 拒绝连接'
            if PROXY_MODE == 'system':
                raise ConnectionError(message + '；请启动代理或修正系统代理设置') from exc
            try:
                body = read(DIRECT_OPENER.open)
            except OSError as direct_error:
                detail = '连接被拒绝' if connection_refused(direct_error) else str(direct_error)
                raise ConnectionError(f'{message}；直连也失败：{detail}。请启动代理或检查网络') from direct_error
    if len(body) > 8 * 1024 * 1024:
        raise ValueError('行情响应超过8MB')
    return body


def parse_quotes(raw):
    output = {}
    for sym, payload in re.findall(r'v_(\w+)="([^"]*)"', raw.decode('gb18030')):
        fields = payload.split('~')
        if len(fields) <= 30 or not re.fullmatch(r'\d{14}', fields[30]):
            continue
        price = number(fields[3])
        if price is None or price <= 0:
            continue
        try:
            stamp = dt.datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=TZ)
        except ValueError:
            continue
        def value(index):
            try:
                n = float(fields[index])
                return n if math.isfinite(n) else None
            except (ValueError, IndexError):
                return None
        raw_volume = value(6)
        amount_parts = fields[35].split('/') if len(fields) > 35 else []
        amount = number(amount_parts[-1]) if len(amount_parts) == 3 else None
        factor = 1 if fields[2].startswith(('688', '689')) else 100
        output[sym] = {'name': fields[1].strip(), 'code': fields[2], 'price': price,
                       'timestamp': stamp.isoformat(), 'date': stamp.date().isoformat(),
                       'open': value(5), 'high': value(33), 'low': value(34),
                       'volume': raw_volume * factor if raw_volume is not None else None,
                       'amount': amount, 'turnover': value(38),
                       'previous_close': value(4), 'source': '腾讯公开行情',
                       'volume_basis': '科创板原股数' if factor == 1 else '手×100股/份'}
    return output


def parse_sina_quotes(raw):
    output = {}
    for symbol, payload in re.findall(r'\bhq_str_((?:sh|sz|bj)\d{6})="([^"]*)"', raw.decode('gb18030')):
        fields = payload.split(',')
        if len(fields) < 32 or not fields[0].strip():
            continue
        try:
            stamp = dt.datetime.fromisoformat(fields[30] + 'T' + fields[31]).replace(tzinfo=TZ)
        except ValueError:
            continue
        price = number(fields[3])
        if price is None or price <= 0 or stamp > dt.datetime.now(TZ):
            continue
        output[symbol] = {'name': fields[0].strip(), 'code': symbol[2:], 'price': price,
                          'timestamp': stamp.isoformat(), 'date': stamp.date().isoformat(),
                          'open': number(fields[1]), 'high': number(fields[4]), 'low': number(fields[5]),
                          'volume': number(fields[8]), 'amount': number(fields[9]), 'turnover': None,
                          'previous_close': number(fields[2]), 'source': '新浪公开行情', 'volume_basis': '股/份'}
    return output


def ifind_settings_path():
    return Path.home() / '.risklens' / 'ifind.json'


def validate_ifind_token(value):
    token = value.strip() if isinstance(value, str) else ''
    if not re.fullmatch(r'[!-~]{1,2048}', token):
        raise ValueError('iFinD 密钥格式无效')
    return token


def read_ifind_settings(path, optional=False):
    try:
        with path.open(encoding='utf-8') as handle:
            return validate_ifind_token(json.loads(handle.read(4096))['auth_token'])
    except FileNotFoundError:
        if optional:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ValueError('无法读取本地 iFinD 密钥配置（需要有效的 auth_token）')


def ifind_token():
    saved = read_ifind_settings(ifind_settings_path(), optional=True)
    if saved:
        return saved
    token = os.environ.get('IFIND_MCP_TOKEN', '').strip()
    if token and token != 'your ifind-mcp key':
        return validate_ifind_token(token)
    config = os.environ.get('IFIND_MCP_CONFIG')
    if config:
        return read_ifind_settings(Path(config).expanduser())
    raise ValueError('未配置本地 iFinD 密钥，请在「数据与方法」配置')


def save_ifind_settings(token):
    save_private_settings(ifind_settings_path(), {'auth_token': token})


def save_private_settings(path, settings):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.stem}-', suffix='.tmp', delete=False) as handle:
            temp = Path(handle.name)
            json.dump(settings, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def ifind_connection_status():
    try:
        ifind_token()
        return {'configured': True}
    except ValueError as exc:
        return {'configured': False, 'message': str(exc)}


def market_key_path(provider):
    return Path.home() / '.risklens' / f'{provider}.json'


def validate_market_key(provider, value):
    label, _ = MARKET_KEY_SETTINGS[provider]
    key = value.strip() if isinstance(value, str) else ''
    if not re.fullmatch(r'[!-~]{1,2048}', key):
        raise ValueError(f'{label} 密钥格式无效')
    return key


def market_key(provider):
    label, env = MARKET_KEY_SETTINGS[provider]
    try:
        with market_key_path(provider).open(encoding='utf-8') as handle:
            return validate_market_key(provider, json.loads(handle.read(8192))['api_key'])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(f'无法读取本地 {label} 密钥配置') from None
    if os.environ.get(env):
        return validate_market_key(provider, os.environ[env])
    raise ValueError(f'未配置 {label} 密钥，请在「数据与方法 → 行情密钥专区」配置')


def market_key_status(provider):
    try:
        market_key(provider)
        return {'configured': True}
    except ValueError as exc:
        return {'configured': False, 'message': str(exc)}


def authenticated_market_json(label, url, headers, data=None):
    def read(opener):
        request = urllib.request.Request(url, data=data, headers=headers)
        with opener.open(request, timeout=10) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError(f'{label} 响应超过大小上限')
        return json.loads(body)

    try:
        with NETWORK_LIMIT:
            try:
                payload = read(IFIND_DIRECT if PROXY_MODE == 'direct' else IFIND_OPENER)
            except OSError as exc:
                host = urllib.parse.urlsplit(url).netloc
                if PROXY_MODE != 'auto' or not connection_refused(exc) or not local_proxy_address(host, 'https'):
                    raise
                payload = read(IFIND_DIRECT)
    except urllib.error.HTTPError as exc:
        exc.close()
        hints = {401: '请检查密钥是否有效', 403: '请检查密钥及该市场或接口的订阅权限',
                 429: '已达到请求额度，请稍后重试'}
        raise ValueError(f'{label} HTTP {exc.code}：{hints.get(exc.code, "行情接口请求失败")}') from None
    except OSError as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        hint = '连接超时' if isinstance(reason, TimeoutError) else '网络连接失败，请检查网络或代理'
        raise ValueError(f'{label}：{hint}') from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError(f'{label} 返回了无效 JSON') from None
    if not isinstance(payload, dict):
        raise ValueError(f'{label} 返回了无效数据结构')
    return payload


def massive_json(url, key=None):
    payload = authenticated_market_json('Massive', url, {
        'Authorization': 'Bearer ' + (key if key is not None else market_key('massive')),
        'Accept': 'application/json',
    })
    status = payload.get('status')
    if status not in ('OK', 'DELAYED'):
        hint = '请检查密钥及该市场的订阅权限' if status in ('NOT_AUTHORIZED', 'UNAUTHORIZED') else '接口未返回成功状态'
        raise ValueError(f'Massive：{hint}')
    return payload


def pingan_test_quote(key):
    request_id = str(uuid.uuid4())
    payload = authenticated_market_json('平安证券', PINGAN_QUOTE, {
        'X-API-Key': key, 'X-Skill-ID': 'market-query', 'X-Request-ID': request_id,
        'channelId': 'c_lite_adapgaty_a_skill', 'requestId': request_id,
        'Content-Type': 'application/json', 'Accept': 'application/json',
    }, b'{"codes":["SH600519"]}')
    if payload.get('code') not in (0, '0'):
        raise ValueError('平安证券行情接口拒绝请求，请检查密钥、权限及额度')
    data = payload.get('data')
    items = data.get('items') if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get('code') == 'SH600519':
            price = number(item.get('price'))
            if price is not None and price > 0:
                return {'price': price}
    raise ValueError('平安证券未返回可核验的样例报价')


def ifind_post(service, token, method, request_id=None, params=None, session=None, version=None):
    url = IFIND_ENDPOINT.format(service)
    data = {'jsonrpc': '2.0', 'method': method}
    if request_id is not None:
        data.update(id=request_id, params=params)
    headers = {'Authorization': token, 'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream'}
    if session:
        headers['Mcp-Session-Id'] = session
    if version:
        headers['MCP-Protocol-Version'] = version

    def read(opener):
        req = urllib.request.Request(url, json.dumps(data, ensure_ascii=False).encode('utf-8'), headers)
        with opener.open(req, timeout=6) as response:
            response_session = response.headers.get('Mcp-Session-Id')
            if request_id is None:
                return {}, response_session
            if response.headers.get_content_type() != 'text/event-stream':
                payload = response.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise ValueError('iFinD 响应超过大小上限')
                return json.loads(payload), response_session
            lines, size = [], 0
            while True:
                line = response.readline(1024 * 1024 + 1)
                size += len(line)
                if size > 1024 * 1024 or not line:
                    raise ValueError('iFinD 事件流未返回完整响应')
                if line.startswith(b'data:'):
                    lines.append(line[5:].strip())
                if not line.strip() and lines:
                    payload = json.loads(b'\n'.join(lines))
                    if isinstance(payload, dict) and payload.get('id') == request_id:
                        return payload, response_session
                    lines.clear()

    with NETWORK_LIMIT:
        try:
            response, response_session = read(IFIND_DIRECT if PROXY_MODE == 'direct' else IFIND_OPENER)
        except OSError as exc:
            host = urllib.parse.urlsplit(url).netloc
            if PROXY_MODE != 'auto' or not connection_refused(exc) or not local_proxy_address(host, 'https'):
                raise
            response, response_session = read(IFIND_DIRECT)
    if request_id is None:
        return {}, response_session
    if not isinstance(response, dict) or response.get('id') != request_id:
        raise ValueError('iFinD 返回了无法匹配的 MCP 响应')
    if 'error' in response:
        error = response['error']
        code = error.get('code') if isinstance(error, dict) else None
        raise ValueError(f'iFinD MCP {method} 失败（错误码 {code if isinstance(code, int) else "未知"}）')
    result = response.get('result')
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError(f'iFinD MCP {method} 未成功（请检查工具权限、额度或返回格式）')
    return result, response_session


def parse_ifind_quotes(result, symbols):
    # MCP can wrap structured output in text containing JSON. Ignore free-form
    # prose rather than guessing a symbol, trading timestamp or volume unit.
    wanted = {s[2:] + '.' + s[:2].upper(): s for s in symbols}
    rows = result.get('structuredContent') or result.get('content') or result
    quotes = {}

    def visit(node):
        if isinstance(node, list):
            for item in node:
                visit(item)
        if not isinstance(node, dict):
            return
        if node.get('type') == 'text' and isinstance(node.get('text'), str):
            try:
                raw = node['text'].strip()
                fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', raw, re.S | re.I)
                visit(json.loads(fenced[1] if fenced else raw))
            except (ValueError, RecursionError):
                pass
            return
        tables = node.get('tables')
        if isinstance(tables, list) and tables and isinstance(tables[0], list):
            columns = tables[0]
            # The live iFinD quote tools return a header row followed by value rows.
            if (all(isinstance(column, str) for column in columns)
                    and {'证券代码', '最新价'} <= set(columns) and len(columns) == len(set(columns))):
                for values in tables[1:]:
                    if isinstance(values, list) and len(values) == len(columns):
                        visit(dict(zip(columns, values)))
        else:
            visit(tables)
        for key in ('data', 'rows', 'quotes'):
            if isinstance(node.get(key), (list, dict)):
                visit(node[key])

        values = {**node, **node['table']} if isinstance(node.get('table'), dict) else node

        def field(*keys):
            for key in keys:
                value = values.get(key)
                if isinstance(value, list) and len(value) == 1:
                    value = value[0]
                if value is not None:
                    return value
            return None

        code = str(field('thscode', 'thsCode', 'symbol', 'code', '证券代码', '同花顺代码') or '').strip().upper()
        exact_symbol = wanted.get(code)
        symbol = exact_symbol or next((s for s in symbols if s[2:] == code), None)
        name = field('name', 'security_name', 'securityName', '证券简称', '证券名称')
        stamp = field('timestamp', 'datetime', 'tradeTime', '行情时间', '更新时间', '时间', 'time')
        day = field('date', '交易日期', '日期')
        if isinstance(day, str) and isinstance(stamp, str) and re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', stamp):
            stamp = day + 'T' + stamp
        if not symbol:
            return
        try:
            if isinstance(stamp, str) and len(stamp) >= 16:
                timestamp = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            elif isinstance(stamp, int) and 10 <= len(str(stamp)) <= 13:
                timestamp = dt.datetime.fromtimestamp(stamp / 1000 if stamp >= 10**12 else stamp, TZ)
            else:
                return
        except (ValueError, OverflowError, OSError):
            return
        timestamp = timestamp.astimezone(TZ) if timestamp.tzinfo else timestamp.replace(tzinfo=TZ)
        price = number(field('最新价', 'price', 'latest', 'latest_price', 'latestPrice'))
        volume = number(field('成交量(股)', '成交量（股）', '成交量(份)', '成交量（份）'))
        unit = field('volume_unit', '成交量单位')
        if volume is None and unit in ('股', '份', '股/份', 'shares'):
            volume = number(field('成交量', 'volume'))
        if price is None or price <= 0 or timestamp > dt.datetime.now(TZ):
            return
        quote = {'name': name.strip() if isinstance(name, str) else None, 'code': symbol[2:], 'price': price,
                 'timestamp': timestamp.isoformat(), 'date': timestamp.date().isoformat(),
                 'open': number(field('开盘价', 'open', 'openPrice')), 'high': number(field('最高价', 'high', 'highPrice')),
                 'low': number(field('最低价', 'low', 'lowPrice')), 'volume': volume, 'amount': None,
                 'turnover': None, 'previous_close': None, 'source': '同花顺 iFinD Level 1',
                 'volume_basis': '股/份' if volume is not None else None, 'qualified_code': code if exact_symbol else None}
        if quote['timestamp'] >= quotes.get(symbol, {}).get('timestamp', ''):
            quotes[symbol] = quote

    visit(rows)
    return quotes


def load_ifind_quotes(symbols, failures, token=None):
    global IFIND_NEXT_CALL
    token = ifind_token() if token is None else token
    quotes = {}
    for service in ('stock', 'fund'):
        group = [s for s in symbols if (s[2:].startswith(('15', '16', '5'))) == (service == 'fund')]
        if not group:
            continue
        try:
            result, session = ifind_post(service, token, 'initialize', 1, {
                'protocolVersion': '2025-03-26', 'capabilities': {},
                'clientInfo': {'name': 'LanCloudRisk', 'version': '0.9.0'},
            })
            version = result.get('protocolVersion') or '2025-03-26'
            ifind_post(service, token, 'notifications/initialized', session=session, version=version)
            for i in range(0, len(group), 10):
                batch = group[i:i+10]
                with IFIND_LOCK:
                    time.sleep(max(0, IFIND_NEXT_CALL - time.monotonic()))
                    IFIND_NEXT_CALL = time.monotonic() + 0.5
                    result, _ = ifind_post(service, token, 'tools/call', 2+i, {
                        'name': service + '_highfreq_quotes',
                        'arguments': {'symbols': ','.join(s[2:] + '.' + s[:2].upper() for s in batch),
                                      'indicators': '最新价,开盘价,最高价,最低价,成交量', 'data_mode': 'real_time'},
                    }, session=session, version=version)
                quotes.update(parse_ifind_quotes(result, batch))
        except urllib.error.HTTPError as exc:
            hint = {401: '密钥未获授权', 403: '访问被拒绝，请检查密钥与工具权限',
                    429: '请求受限，请检查额度与调用频率'}.get(exc.code)
            failures.append(f'{service} HTTP {exc.code}' + (f'（{hint}）' if hint else ''))
        except OSError as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            failures.append(f'{service} 连接失败（{type(reason).__name__}，{str(reason).replace(token, "[已隐藏]")[:100]}）')
        except (ValueError, RecursionError) as exc:
            failures.append(f'{service}：{str(exc).replace(token, "[已隐藏]")[:130]}')
    return quotes


def test_ifind_connection(payload):
    has_new_key = 'token' in payload
    token = validate_ifind_token(payload['token']) if has_new_key else ifind_token()
    if not IFIND_SETTINGS_LOCK.acquire(blocking=False):
        raise RuntimeError('已有 iFinD 连接测试，请稍后重试')
    try:
        checks = []
        for label, symbol in (('A 股', 'sh600519'), ('场内 ETF', 'sh510300')):
            failures = []
            quote = load_ifind_quotes([symbol], failures, token=token).get(symbol)
            checks.append({'market': label, 'symbol': symbol[2:], 'ok': bool(quote),
                           **({'timestamp': quote['timestamp'], 'price': quote['price']} if quote else
                              {'error': '；'.join(failures) or '未返回可核验的证券代码、报价时间或价格'})})
        passed = sum(check['ok'] for check in checks)
        saved = False
        save_error = None
        if passed and has_new_key:
            try:
                save_ifind_settings(token)
                saved = True
            except OSError:
                save_error = '报价测试通过，但本机无法写入密钥设置，请检查权限后重试'
        return {'result': ('ok' if passed == len(checks) else 'partial') if passed else 'failed',
                'checked_at': now(), 'checks': checks, 'saved': saved, 'save_error': save_error,
                **ifind_connection_status()}
    finally:
        IFIND_SETTINGS_LOCK.release()


def test_market_key(provider, payload):
    has_new_key = 'token' in payload
    key = validate_market_key(provider, payload['token']) if has_new_key else market_key(provider)
    lock = MARKET_KEY_LOCKS[provider]
    if not lock.acquire(blocking=False):
        raise RuntimeError(f'{MARKET_KEY_SETTINGS[provider][0]} 正在测试，请稍后重试')
    try:
        if provider == 'massive':
            probes = [(label, ticker, lambda ticker=ticker: massive_test_quote(ticker, key))
                      for label, ticker in (('美股', 'AAPL'), ('费城半导体指数', 'I:SOX'), ('比特币', 'X:BTCUSD'))]
        else:
            probes = [('A 股', '600519', lambda: pingan_test_quote(key))]
        checks = []
        for label, symbol, probe in probes:
            check = {'market': label, 'symbol': symbol}
            try:
                check.update(ok=True, **probe())
            except ValueError as exc:
                check.update(ok=False, error=str(exc))
            checks.append(check)
        passed = sum(check['ok'] for check in checks)
        saved, save_error = False, None
        if passed and has_new_key:
            try:
                save_private_settings(market_key_path(provider), {'api_key': key})
                if provider == 'massive':
                    with SEARCH_LOCK:
                        SEARCH_CACHE.clear()
                saved = True
            except OSError:
                save_error = '报价测试通过，但本机无法写入密钥设置，请检查权限后重试'
        return {'result': ('ok' if passed == len(checks) else 'partial') if passed else 'failed',
                'checked_at': now(), 'checks': checks, 'saved': saved, 'save_error': save_error,
                **market_key_status(provider)}
    finally:
        lock.release()


def canonical_quote_name(quote, expected):
    name = quote.get('name')
    if not expected or name == expected:
        return quote
    marker = re.fullmatch(r'(?:XD|XR|DR)\s*(.+)', name or '')
    if marker:
        short = marker[1]
        # On ex-dates the prefix can occupy two bytes of the vendor name field.
        if short == expected or (re.fullmatch(r'[\u3400-\u9fff]{3}', short) and expected.startswith(short)):
            return {**quote, 'name': expected, 'provider_name': name}
    if quote.get('qualified_code'):
        return {**quote, 'name': expected, 'provider_name': name}
    return quote


def load_cn_quotes(symbols, *, issues=None, minimum=None, require_bar=False):
    remaining = dict.fromkeys(symbols)
    minimum = minimum or {}
    quotes, errors, price_only = {}, {}, {}
    sources = [('腾讯报价', lambda pending, _: parse_quotes(fetch(QUOTE + ','.join(pending)))),
               ('新浪报价', lambda pending, _: parse_sina_quotes(fetch(SINA_QUOTE + ','.join(pending)))),
               ('同花顺 iFinD 报价', load_ifind_quotes)]
    for name, load in sources:
        if not remaining:
            break
        failures = []
        invalid_identity = invalid_time = 0
        try:
            result = load(list(remaining), failures)
            for symbol in list(remaining):
                quote = result.get(symbol)
                floor = minimum.get(symbol, {})
                stamp = quote.get('timestamp', '') if quote else ''
                if not quote:
                    continue
                quote = canonical_quote_name(quote, floor.get('name'))
                if (quote.get('code') != symbol[2:] or not quote.get('name')
                        or floor.get('name') and quote['name'] != floor['name']):
                    invalid_identity += 1
                    continue
                if not stamp or stamp < (floor.get('timestamp') or '') or stamp > now():
                    invalid_time += 1
                    continue
                if require_bar and quote_bar_error(quote):
                    if stamp >= price_only.get(symbol, {}).get('timestamp', ''):
                        price_only[symbol] = quote
                    continue
                quotes[symbol] = quote
                remaining.pop(symbol)
            if remaining:
                missing = '、'.join(list(remaining)[:10])
                kind = '完整累计 OHLC/成交量' if require_bar else '有效报价'
                failures.append(f'{len(remaining)} 个标的缺少{kind}（{missing}）')
            if invalid_identity:
                failures.append(f'{invalid_identity} 个标的证券身份不匹配')
            if invalid_time:
                failures.append(f'{invalid_time} 个标的报价时间缺失、过旧或晚于当前时间')
        except (OSError, ValueError) as exc:
            failures.append(str(exc)[:165])
        if failures:
            errors[name] = '；'.join(failures)
    if issues is not None:
        issues.update(errors)
    quotes.update({s: price_only[s] for s in remaining if s in price_only})
    if not quotes and remaining:
        raise ConnectionError('；'.join(f'{name}：{error}' for name, error in errors.items()))
    return quotes


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
            quote_source = quote.get('source', '腾讯公开行情')
            return code, {'source': f'{provider}（未复权）+ {quote_source}名称核验', 'as_of': bars[-1]['date'],
                          'adjustment': 'none', 'completed': bars[-1]['completed'], 'bars': bars,
                          'fundamentals': None, 'fetched_at': now(), 'latest_quote': quote,
                          'volume_unit': unit, 'provider_url': url, 'skipped_rows': skipped,
                          'history_checked_at': now(), 'history_provider': provider.removesuffix('日线'),
                          'fallback_errors': errors}
        except Exception as exc:
            errors.append(provider + ': ' + str(exc)[:160])
    raise ValueError('；'.join(errors))


def quote_bar_error(quote):
    stamp = dt.datetime.fromisoformat(quote['timestamp'])
    if stamp.time() < dt.time(9, 30) and not all((quote.get(k) or 0) > 0 for k in ('open', 'high', 'low')):
        return '开盘前尚未形成有效日K，未采用竞价指示价'
    keys = ['open', 'high', 'low', 'price', 'volume']
    if not all(isinstance(quote.get(k), (int, float)) and math.isfinite(quote[k]) for k in keys):
        return '最新报价缺少完整OHLC/累计量'
    if min(quote[k] for k in keys[:4]) <= 0 or quote['volume'] < 0 or quote['low'] > min(quote['open'], quote['price']) or quote['high'] < max(quote['open'], quote['price']):
        return '最新报价OHLC不一致'
    return None


def patch_quote(record, quote):
    """Apply the current cumulative OHLC only after history has been refreshed."""
    issue = quote_bar_error(quote)
    if issue:
        raise ValueError(quote.get('source', '报价源') + '：' + issue)
    stamp = dt.datetime.fromisoformat(quote['timestamp'])
    initial = record['latest_quote']
    if (quote['code'], quote.get('name')) != (initial['code'], initial['name']):
        raise ValueError('二次报价名称或代码未通过核验')
    if quote['date'] < record['bars'][-1]['date'] or quote['timestamp'] < initial.get('timestamp', ''):
        raise ValueError('二次报价早于已取得的行情')
    completed = stamp.hour >= 15 and dt.datetime.now(TZ) >= stamp
    live = {'date': quote['date'], 'open': quote['open'], 'high': quote['high'], 'low': quote['low'],
            'close': quote['price'], 'vol': quote['volume'], 'netFlow': None, 'completed': completed,
            'observed_at': quote['timestamp'], 'bar_source': quote.get('source', '腾讯公开行情') + '累计报价组装日K'}
    # Only combine homogeneous share/lot units; Sina uses shares. Tencent fallback
    # K-lines on the main board use lots, whereas STAR-board bars already use shares.
    if record['volume_unit'] != '股/份':
        factor = 1 if quote['code'].startswith(('688', '689')) else 100
        for b in record['bars']:
            b['vol'] *= factor
        record['volume_unit'] = '股/份'
    record['bars'] = [b for b in record['bars'] if b['date'] != live['date']] + [live]
    record.update(as_of=live['date'], completed=completed, latest_quote=quote, fetched_at=now(),
                  closed_as_of=max((b['date'] for b in record['bars'] if b.get('completed')), default=None))


def retain_valid_history(record, reason, quote=None):
    last = record['bars'][-1]
    record['live_error'] = str(reason)[:200]
    record['latest_quote'] = {'code': record['latest_quote']['code'], 'name': record['latest_quote']['name'],
                              'price': last['close'], 'date': last['date'], 'timestamp': last['date'],
                              'source': '最新有效历史日线；实时补丁未采用', 'turnover': None}
    if (quote and quote.get('source') == '同花顺 iFinD Level 1' and quote.get('date', '') >= last['date']
            and (quote['code'], quote['name']) == (record['latest_quote']['code'], record['latest_quote']['name'])):
        record['latest_quote'] = {**quote, 'quote_only': True}
    record['closed_as_of'] = max((b['date'] for b in record['bars'] if b.get('completed')), default=None)


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
    if not any(row['netFlow'] is not None and math.isfinite(row['netFlow']) for row in output):
        raise ValueError('新浪未返回有效的大单资金净额，保留已有资金历史')
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


def load_nav_history(code):
    url = 'https://fund.eastmoney.com/pingzhongdata/' + code + '.js?v=' + dt.datetime.now(TZ).strftime('%Y%m%d')
    text = fetch(url).decode('utf-8')
    match = re.search(r'var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);', text, re.S)
    if not match:
        raise ValueError('基金净值数组不可用')
    rows = {dt.datetime.fromtimestamp(r['x'] / 1000, TZ).date().isoformat(): number(r['y'])
            for r in json.loads(match.group(1)) if (number(r.get('y')) or 0) > 0}
    if not rows:
        raise ValueError('基金净值为空')
    return rows, url


def load_nav(code):
    rows, url = load_nav_history(code)
    latest = max(rows)
    return {'date': latest, 'value': rows[latest], 'source': '天天基金历史单位净值', 'url': url, 'fetched_at': now()}


ETF_FLOW_SOURCE = '上交所清算后ETF份额变化 × 天天基金前一交易日单位净值（估算）'
ETF_FLOW_LIMIT = 60


def load_sse_etf_shares(day):
    url = 'https://query.sse.com.cn/commonQuery.do?' + urllib.parse.urlencode({
        'sqlId': 'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L', 'STAT_DATE': day, 'isPagination': 'false'})
    data = json_get(url)
    rows = data.get('result') or (data.get('pageHelp') or {}).get('data') or []
    shares = {}
    for r in rows:
        code, value = r.get('SEC_CODE', ''), number(r.get('TOT_VOL'))
        if r.get('STAT_DATE') == day and re.fullmatch(r'5\d{5}', code) and value is not None and value >= 0:
            shares[code] = round(value * 10000)
    return shares


def estimate_etf_flow(dates, navs, shares):
    points = []
    for prev, day in zip(dates, dates[1:]):
        before, after, nav = shares.get(prev), shares.get(day), navs.get(prev)
        amount = round((after - before) * nav, 2) if before is not None and after is not None and nav is not None else None
        points.append({'date': day, 'previous_date': prev, 'shares': after, 'previous_shares': before,
                       'previous_nav': nav, 'netFlow': amount})
    return points


def refresh_etf_flows(records, previous, codes):
    """Fetch each exchange date once, then estimate each ETF on its own closed trading dates."""
    plans, wanted = {}, set()
    for code in codes:
        if not re.fullmatch(r'5\d{5}', code) or code not in records:
            continue
        record = records[code]
        old = previous.get(code, {}).get('etf_flow') or {}
        if old.get('rows'):
            record['etf_flow'] = old
        dates = sorted({b['date'] for b in record.get('bars', []) if b.get('completed') is True})[-(ETF_FLOW_LIMIT + 1):]
        if len(dates) < 2:
            continue
        cached = {}
        for row in old.get('rows', []):
            for date_key, value_key in [('date', 'shares'), ('previous_date', 'previous_shares')]:
                value = number(row.get(value_key))
                if row.get(date_key) and value is not None and value >= 0:
                    cached[row[date_key]] = value
        wanted.update(day for day in dates if day not in cached)
        wanted.update(dates[-2:])
        plans[code] = (dates, cached)
    if not plans:
        return

    def read_shares(day):
        try:
            return day, load_sse_etf_shares(day)
        except Exception:
            return day, {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        daily = dict(pool.map(read_shares, sorted(wanted, reverse=True)))
        nav_jobs = {code: pool.submit(load_nav_history, code) for code in plans}
        for code, (dates, cached) in plans.items():
            record = records[code]
            issues = record.setdefault('research_errors', {})
            try:
                navs, nav_url = nav_jobs[code].result()
                shares = {**cached, **{day: data[code] for day, data in daily.items() if code in data}}
                rows = estimate_etf_flow(dates, navs, shares)
                if not any(r['netFlow'] is not None for r in rows):
                    raise ValueError('份额与净值未能按相邻交易日对齐')
                record['etf_flow'] = {'source': ETF_FLOW_SOURCE, 'kind': 'share_nav_estimate', 'currency': 'CNY',
                                      'nav_url': nav_url, 'rows': rows}
                issues.pop('etf_flow', None)
            except Exception as exc:
                issues['etf_flow'] = str(exc)[:180]


# The score model lives in the browser; this layer preserves dated source evidence.
GLOBAL_ASSETS = [
    ('DXY', '美元指数', 'DX-Y.NYB', '点', 'macro', 6),
    ('CNH', '美元/离岸人民币', 'CNH=X', 'CNH/USD', 'macro', 5),
    ('US10Y', '10年期美债收益率', '^TNX', '%', 'yield', 4),
    ('DJI', '道琼斯工业指数', '^DJI', '点', 'equity', 2),
    ('IXIC', '纳斯达克综合指数', '^IXIC', '点', 'equity', 8),
    ('N225', '日经225指数', '^N225', '点', 'equity', 2),
    ('KOSPI', '韩国综合指数', '^KS11', '点', 'equity', 2),
    ('SOX', '费城半导体指数 · SOX', '^SOX', '点', 'equity', 12),
    ('usSOXX', 'iShares半导体ETF · SOXX', 'SOXX', 'USD', 'equity', 10),
    ('usQQQ', 'Invesco纳指100ETF · QQQ', 'QQQ', 'USD', 'equity', 14),
    ('hkHSTECH', '恒生科技指数', 'HSTECH.HK', '点', 'equity', 5),
    ('A50', '富时中国A50指数', 'XIN9.FGI', '点', 'equity', 3),
    ('GOLD', '黄金 · COMEX连续期货代理', 'GC=F', 'USD/盎司', 'metal', 2),
    ('SILVER', '白银 · COMEX连续期货代理', 'SI=F', 'USD/盎司', 'metal', 1),
    ('BTC', '比特币 / 美元', 'BTC-USD', 'USD', 'crypto', 4),
    ('usNVDA', '英伟达', 'NVDA', 'USD', 'equity', 0),
    ('usMU', '美光科技', 'MU', 'USD', 'equity', 0),
    ('usTSM', '台积电ADR', 'TSM', 'USD', 'equity', 0),
    ('usAVGO', '博通', 'AVGO', 'USD', 'equity', 0),
    ('usVIX', 'VIX波动率指数', '^VIX', '点', 'volatility', 0),
]


# Match the instrument itself and the market ID, including COMEX continuous contracts.
EASTMONEY_EXTERNAL = {
    'DXY': '100.UDI', 'CNH': '133.USDCNH', 'DJI': '100.DJIA', 'IXIC': '100.NDX',
    'N225': '100.N225', 'KOSPI': '100.KS11', 'A50': '100.XIN9', 'GOLD': '101.GC00Y',
    'SILVER': '101.SI00Y', 'usSOXX': '105.SOXX', 'usQQQ': '105.QQQ', 'usNVDA': '105.NVDA',
    'usMU': '105.MU', 'usTSM': '106.TSM', 'usAVGO': '105.AVGO',
}
EXTERNAL_TIMEZONES = {
    'N225': 'Asia/Tokyo', 'KOSPI': 'Asia/Seoul', 'hkHSTECH': 'Asia/Hong_Kong',
    'A50': 'Asia/Shanghai', 'CNH': 'Asia/Shanghai', 'BTC': 'UTC',
}
YAHOO_CHART_HOSTS = ('query1.finance.yahoo.com', 'query2.finance.yahoo.com')


def massive_day(instant, timezone):
    try:
        from zoneinfo import ZoneInfo
        return instant.astimezone(ZoneInfo(timezone)).date()
    except (ImportError, KeyError):
        if timezone == 'UTC':
            return instant.astimezone(dt.timezone.utc).date()
        # Standalone Python on Windows may lack IANA data. The recent US window is post-2007.
        year = instant.astimezone(dt.timezone.utc).year
        march = dt.datetime(year, 3, 8, 7, tzinfo=dt.timezone.utc)
        november = dt.datetime(year, 11, 1, 6, tzinfo=dt.timezone.utc)
        start = march + dt.timedelta(days=(6 - march.weekday()) % 7)
        end = november + dt.timedelta(days=(6 - november.weekday()) % 7)
        return instant.astimezone(dt.timezone(dt.timedelta(hours=-4 if start <= instant < end else -5))).date()


def massive_daily(ticker, key=None, days=720, current=None):
    timezone = 'UTC' if ticker.startswith('X:') else 'America/New_York'
    current = current or dt.datetime.now(dt.timezone.utc)
    today = massive_day(current, timezone)
    start = today - dt.timedelta(days=days)
    url = f'{MASSIVE_API}/v2/aggs/ticker/{urllib.parse.quote(ticker, safe=":")}/range/1/day/{start}/{today}'
    url += '?adjusted=true&sort=asc&limit=5000'
    payload = massive_json(url, key)
    if payload.get('ticker') != ticker:
        raise ValueError('Massive 日线标的身份不匹配')
    rows = {}
    results = payload.get('results')
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict) or type(item.get('t')) is not int:
            continue
        try:
            day = massive_day(dt.datetime.fromtimestamp(item['t'] / 1000, dt.timezone.utc), timezone)
        except (OverflowError, OSError, ValueError):
            continue
        prices = {field: number(item.get(key)) for field, key in (
            ('open', 'o'), ('high', 'h'), ('low', 'l'), ('close', 'c'))}
        if start <= day <= today and prices['close'] is not None and prices['close'] > 0:
            rows[day] = {'date': day.isoformat(), **prices, 'vol': number(item.get('v')),
                         'completed': day < today}
    if not rows:
        raise ValueError('Massive 未返回可核验的日线')
    return [rows[day] for day in sorted(rows)], url, timezone, payload['status'] == 'DELAYED'


def massive_test_quote(ticker, key):
    rows, _, _, delayed = massive_daily(ticker, key=key, days=10)
    return {'price': rows[-1]['close'], 'timestamp': rows[-1]['date'],
            'detail': '延迟数据' if delayed else '日线数据'}


def load_massive_global(ticker):
    rows, url, timezone, delayed = massive_daily(ticker)
    last = rows[-1]
    return {'history': [{'date': b['date'], 'close': b['close']} for b in rows],
            'timestamp': last['date'], 'timezone': timezone, 'provisional': not last['completed'],
            'delayed': delayed}, url


def read_yahoo_chart(ticker, query, parse, errors):
    for host in YAHOO_CHART_HOSTS:
        url = f'https://{host}/v8/finance/chart/{urllib.parse.quote(ticker, safe="")}?{query}'
        try:
            result = json.loads(fetch(url))['chart']['result'][0]
            if result['meta']['symbol'].upper() != ticker.upper():
                raise ValueError('标的身份不匹配')
            return parse(result), url
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
            errors.append(f'Yahoo Finance {host.split(".")[0]}：{str(exc)[:130]}')
    raise ValueError('；'.join(errors))


def fetch_json_history(url):
    # One retry for transient upstream gateway failures; never retry auth or rate-limit errors.
    for attempt in range(2):
        try:
            return json.loads(fetch(url))
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in (502, 503, 504):
                raise


def external_changes(history, kind):
    """N intervals require N+1 valid dated observations; yields use bp, not returns."""
    out = {}
    for n in (5, 20, 60, 250):
        if len(history) > n:
            a, b = history[-1]['close'], history[-n-1]['close']
            out[str(n)] = {'value': (a-b)*100 if kind == 'yield' else (a/b-1)*100,
                           'unit': 'bp' if kind == 'yield' else '%',
                           'start': history[-n-1]['date'], 'end': history[-1]['date']}
        else:
            out[str(n)] = {'value': None, 'unit': 'bp' if kind == 'yield' else '%',
                           'reason': '需要' + str(n+1) + '条有效日频观察值'}
    return out


def global_history(points, today):
    values = {}
    for date, raw in points:
        try:
            date = dt.date.fromisoformat(date).isoformat()
        except (TypeError, ValueError):
            continue
        close = number(raw)
        if close is not None and close > 0 and date <= today:
            values[date] = close
    history = [{'date': d, 'close': c} for d, c in sorted(values.items())][-756:]
    if not history:
        raise ValueError('缺少有效日线')
    return history


def parse_yahoo_global(result, kind):
    meta = result['meta']
    offset = dt.timezone(dt.timedelta(seconds=meta.get('gmtoffset', 0)))
    zone = market_timezone(meta) if meta.get('exchangeTimezoneName') else offset
    current = dt.datetime.now(zone)
    points = ((dt.datetime.fromtimestamp(stamp, zone).date().isoformat(), close)
              for stamp, close in zip(result.get('timestamp') or [], result['indicators']['quote'][0].get('close') or []))
    history = global_history(points, current.date().isoformat())
    last = history[-1]['date']
    stamp = meta.get('regularMarketTime')
    stamp = dt.datetime.fromtimestamp(stamp, zone) if stamp else None
    session = meta.get('currentTradingPeriod', {}).get('regular', {})
    return {'history': history, 'timestamp': stamp.isoformat() if stamp and stamp.date().isoformat() == last else last,
            'timezone': meta.get('exchangeTimezoneName'), 'provisional': last == current.date().isoformat()
            and (kind == 'crypto' or session.get('start', 1e20) <= current.timestamp() < session.get('end', 0))}


def external_backup(symbol):
    if symbol in EASTMONEY_EXTERNAL:
        secid = EASTMONEY_EXTERNAL[symbol]
        query = urllib.parse.urlencode({'secid': secid, 'klt': 101, 'fqt': 0, 'lmt': 600, 'beg': 0,
                                        'end': 20500101, 'fields1': 'f1,f2,f3,f4,f5,f6',
                                        'fields2': 'f51,f52,f53,f54,f55,f56,f57'})
        return '东方财富', 'https://push2his.eastmoney.com/api/qt/stock/kline/get?' + query
    if symbol == 'SOX':
        start = (dt.datetime.now(TZ).date() - dt.timedelta(days=760)).isoformat()
        return 'Nasdaq', f'https://api.nasdaq.com/api/quote/SOX/historical?assetclass=index&fromdate={start}&limit=600'
    return {
        'hkHSTECH': ('腾讯', 'https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hkHSTECH,day,,,600,qfq'),
        'US10Y': ('FRED', 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10'),
        'usVIX': ('Cboe', 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv'),
        'BTC': ('Coinbase', 'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400'),
    }[symbol]


def read_alternate_global(symbol, provider, url):
    closed_feed = provider in ('FRED', 'Cboe', 'Nasdaq')
    timezone = EXTERNAL_TIMEZONES.get(symbol, 'America/New_York')
    current = dt.datetime.now(market_timezone({'exchangeTimezoneName': timezone}))
    today = current.date().isoformat()
    if provider == '东方财富':
        market, code = EASTMONEY_EXTERNAL[symbol].split('.')
        payload = fetch_json_history(url)['data']
        if not payload or payload['code'] != code or str(payload.get('market')) != market:
            raise ValueError('跨市场历史标的身份不匹配')
        points = ((cells[0], cells[2]) for cells in (row.split(',') for row in payload['klines']) if len(cells) >= 3)
    elif provider == '腾讯':
        payload = json.loads(fetch(url))['data']['hkHSTECH']
        points = ((r[0], r[2]) for r in payload['day'] if len(r) >= 3)
    elif provider == 'Nasdaq':
        payload = json.loads(fetch(url))['data']
        if not payload or payload['symbol'] != 'SOX':
            raise ValueError('费城半导体指数身份不匹配')
        points = ((dt.datetime.strptime(r['date'], '%m/%d/%Y').date().isoformat(), r['close'].replace(',', ''))
                  for r in payload['tradesTable']['rows'])
    elif provider == 'Coinbase':
        rows = json.loads(fetch(url))
        points = ((dt.datetime.fromtimestamp(r[0], dt.timezone.utc).date().isoformat(), r[4]) for r in rows if len(r) >= 5)
    else:
        reader = csv.DictReader(io.StringIO(fetch(url).decode('utf-8-sig')))
        if provider == 'FRED':
            points = ((r['observation_date'], r['DGS10']) for r in reader)
        else:
            points = ((dt.datetime.strptime(r['DATE'], '%m/%d/%Y').date().isoformat(), r['CLOSE']) for r in reader)
    history = global_history(points, today)
    last = history[-1]['date']
    return {'history': history, 'timestamp': last, 'timezone': timezone, 'provisional': not closed_feed and last == today}


def load_global_asset(asset):
    symbol, name, ticker, unit, kind, weight = asset
    yahoo_link = 'https://finance.yahoo.com/quote/' + urllib.parse.quote(ticker, safe='') + '/'
    massive_ticker = {'SOX': 'I:SOX', 'BTC': 'X:BTCUSD'}.get(symbol)
    if symbol.startswith('us') and kind == 'equity':
        massive_ticker = ticker
    row = {'symbol': symbol, 'name': name, 'ticker': ticker, 'unit': unit, 'kind': kind, 'weight': RISK_WEIGHTS.get(symbol, weight),
           'url': 'https://massive.com/' if massive_ticker else yahoo_link, 'fetched_at': now()}
    errors = []
    record, url, provider = None, None, 'Massive' if massive_ticker else 'Yahoo Finance'
    try:
        if massive_ticker:
            record, url = load_massive_global(massive_ticker)
        else:
            record, url = read_yahoo_chart(ticker, 'range=2y&interval=1d', lambda result: parse_yahoo_global(result, kind), errors)
    except ValueError as exc:
        if massive_ticker:
            errors.append(str(exc))
    if record is None or (symbol in ('A50', 'CNH', 'hkHSTECH') and len(record['history']) < 251):
        backup_provider, backup_url = external_backup(symbol)
        try:
            backup = read_alternate_global(symbol, backup_provider, backup_url)
            # Keep each return window on one provider's price series.
            if record is None or (backup['history'][-1]['date'] >= record['history'][-1]['date']
                                  and len(backup['history']) > len(record['history'])):
                record, url, provider = backup, backup_url, backup_provider
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
            errors.append(f'{backup_provider}：{str(exc)[:150]}')
    if record is None:
        return {**row, 'error': '；'.join(errors), 'price': None, 'history': [], 'returns': external_changes([], kind), 'stale': True}
    history = record['history']
    last = history[-1]['date']
    basis = ' 日频收益率' if kind == 'yield' else ' 日频价格（非总回报）'
    if record.get('delayed'):
        basis += ' · 延迟'
    return {**row, **record, 'provider': provider, 'source': provider + basis,
            'url': yahoo_link if provider == 'Yahoo Finance' else url, 'provider_url': url, 'history_url': url,
            'history_source': provider + '日线', 'history_as_of': last, 'price': history[-1]['close'],
            'change': (history[-1]['close'] / history[-2]['close'] - 1) * 100 if len(history) > 1 and kind != 'yield' else None,
            'observation_basis': '日频观察值；比特币含周末' if kind == 'crypto' else '供应商有效日线；不插值',
            'returns': external_changes(history, kind), 'fallback_errors': errors,
            'stale': (dt.datetime.now(TZ).date() - dt.date.fromisoformat(last)).days > (2 if kind == 'crypto' else 7)}


def merge_external(previous, current):
    prior = {r['symbol']: r for r in previous}
    output = []
    for row in current:
        old = prior.get(row['symbol'], {})
        older = bool(old.get('history_as_of') and row.get('history_as_of')
                     and row['history_as_of'] < old['history_as_of'])
        if old and (row.get('error') or older):
            error = row.get('error') or '新取得的日线早于缓存日期'
            output.append({**old, 'error': error, 'retained': True})
            continue
        old_provider = old.get('provider')
        if not old_provider and not old.get('history_source') and str(old.get('source', '')).startswith('Yahoo Finance'):
            old_provider = 'Yahoo Finance'
        if (old_provider == row.get('provider') and old.get('ticker') == row.get('ticker')
                and len(old.get('history', [])) > len(row.get('history', []))
                and row.get('history') and old['history'][-1]['date'] <= row['history'][-1]['date']):
            values = {r['date']: r['close'] for r in old['history']}
            values.update({r['date']: r['close'] for r in row['history']})
            history = [{'date': d, 'close': c} for d, c in sorted(values.items())][-756:]
            row = {**row, 'history': history, 'returns': external_changes(history, row['kind']),
                   'retained_history': True, 'history_source': old.get('history_source', old.get('source')),
                   'history_url': old.get('history_url', old.get('url'))}
        output.append(row)
    return output


def load_external():
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(load_global_asset, GLOBAL_ASSETS))


QUOTE_PROVIDER_NAMES = {'腾讯公开行情': '腾讯', '新浪公开行情': '新浪', '同花顺 iFinD Level 1': '同花顺 iFinD'}


GPR_URL = 'https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.dta'


def parse_gpr_stata(raw):
    """Bounded Stata 118 fixed-width reader; no optional scientific dependencies."""
    def section(tag):
        start = raw.find(b'<' + tag + b'>')
        end = raw.find(b'</' + tag + b'>', start)
        if start < 0 or end < 0:
            raise ValueError('GPR文件缺少字段 ' + tag.decode())
        return raw[start+len(tag)+2:end]
    if section(b'release') != b'118' or section(b'byteorder') != b'LSF':
        raise ValueError('GPR仅支持Stata118小端格式')
    k, n = int.from_bytes(section(b'K'), 'little'), int.from_bytes(section(b'N'), 'little')
    if not (1 <= k <= 100 and 1 <= n <= 100000):
        raise ValueError('GPR行列数越界')
    types = struct.unpack('<' + 'H'*k, section(b'variable_types'))
    names = section(b'varnames')
    if len(names) != 129*k:
        raise ValueError('GPR变量名长度错误')
    fields, offset = {}, 0
    formats = {65526: ('d', 8), 65527: ('f', 4), 65528: ('i', 4), 65529: ('h', 2), 65530: ('b', 1)}
    for i, t in enumerate(types):
        name = names[i*129:(i+1)*129].split(b'\0')[0].decode('utf-8')
        fmt, size = (None, t) if 1 <= t <= 2045 else formats.get(t, (None, 0))
        if not size:
            raise ValueError('GPR变量类型不支持')
        fields[name] = (offset, fmt, size)
        offset += size
    required = ['DAY', 'GPRD', 'GPRD_ACT', 'GPRD_THREAT']
    if not all(x in fields for x in required):
        raise ValueError('GPR列名变更')
    body = section(b'data')
    if len(body) != offset*n:
        raise ValueError('GPR记录长度错误')
    rows = []
    for i in range(max(0, n-1500), n):
        row = {}
        for name in required:
            pos, fmt, size = fields[name]
            block = body[i*offset+pos:i*offset+pos+size]
            value = struct.unpack('<'+fmt, block)[0] if fmt else block.split(b'\0')[0].decode('utf-8')
            row[name] = value if isinstance(value, str) or math.isfinite(value) and abs(value)<1e30 else None
        day = dt.datetime.strptime(row['DAY'], '%Y%m%d').date().isoformat()
        if day <= dt.datetime.now(TZ).date().isoformat():
            rows.append({'date': day, **{name: row[name] for name in required[1:]}})
    return rows


def load_geopolitics():
    rows = parse_gpr_stata(fetch(GPR_URL))
    output = {'source': 'Caldara / Iacoviello GPR · 每周更新的日频新闻指数', 'url': 'https://www.matteoiacoviello.com/gpr.htm',
              'provider_url': GPR_URL, 'fetched_at': now(), 'as_of': rows[-1]['date'], 'sample_count': len(rows), 'items': []}
    for key, name, kind in [('GPRD_THREAT', '政治 / 地缘威胁代理', 'politics'), ('GPRD_ACT', '战争 / 冲突行动代理', 'war')]:
        means = []
        for i in range(29, len(rows)):
            window = [r[key] for r in rows[i-29:i+1]]
            if all(v is not None for v in window):
                means.append((rows[i]['date'], sum(window)/30))
        baseline = [v for d, v in means if d >= (dt.date.fromisoformat(rows[-1]['date'])-dt.timedelta(days=1095)).isoformat()]
        latest = means[-1][1] if means and means[-1][0] == rows[-1]['date'] else None
        percentile = sum(v <= latest for v in baseline)/len(baseline)*100 if latest is not None and len(baseline)>=252 else None
        output['items'].append({'kind': kind, 'name': name, 'field': key, 'value30': latest, 'score': percentile,
                                'baseline_count': len(baseline), 'weight': 10})
    output['stale'] = (dt.datetime.now(TZ).date()-dt.date.fromisoformat(output['as_of'])).days > 14
    return output


# Market-qualified IDs prevent collisions such as HK.00700 versus US.00700.
BENCHMARK_CODES = {'510300', '159338', '510500', '512100', '159915', '588080'}
BASE_WATCH_CODES = {'002281', '601869', '688825', '688981', '300033', '300390', '515980'} | BENCHMARK_CODES
WATCHLIST_DEFAULTS_VERSION = 1
RISK_WEIGHTS = {'DXY': 6, 'CNH': 5, 'US10Y': 4, 'DJI': 2, 'IXIC': 8, 'N225': 2, 'KOSPI': 2,
                'SOX': 12, 'usSOXX': 10, 'usQQQ': 14, 'hkHSTECH': 5, 'A50': 3, 'GOLD': 2, 'SILVER': 1, 'BTC': 4}
SEARCH_CACHE, SEARCH_LOCK = {}, threading.Lock()


def base_catalog():
    return {code: {'code': code, 'symbol': code, 'name': name, 'market': 'CN', 'currency': 'CNY',
                   'type': 'etf' if code.startswith(('5', '15')) else 'stock', 'listed': listed,
                   'watch': code in BASE_WATCH_CODES, 'benchmark': code in BENCHMARK_CODES,
                   'provider_symbol': SYMBOLS[code]}
            for code, name, listed in ASSETS}


def asset_id(market, symbol):
    market = str(market).upper()
    symbol = str(symbol).strip().upper()
    if market == 'CN' and re.fullmatch(r'\d{6}', symbol):
        return symbol
    if market == 'HK' and re.fullmatch(r'\d{1,5}(?:\.HK)?', symbol):
        return 'HK.' + symbol.split('.')[0].zfill(5)
    if market == 'US' and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', symbol):
        return 'US.' + symbol
    raise ValueError('市场或证券代码格式无效')


def yahoo_symbol(meta):
    if meta['market'] == 'HK':
        return str(int(meta['symbol'])).zfill(4) + '.HK'
    return meta['symbol']


def parse_security_search(raw, market='ALL'):
    match = re.search(r'v_hint\s*=\s*("(?:\\.|[^"\\])*")', raw.decode('gb18030'))
    if not match:
        raise ValueError('搜索源未返回证券列表')
    text = json.loads(match.group(1))
    rows, seen = [], set()
    for entry in text.split('^'):
        cells = entry.split('~')
        if len(cells) < 5:
            continue
        exchange, raw_symbol, name, _, kind = cells[:5]
        try:
            if exchange in ('sh', 'sz', 'bj') and kind in ('GP-A', 'ETF', 'LOF'):
                m, symbol = 'CN', raw_symbol
            elif exchange == 'hk' and kind in ('GP', 'ETF'):
                m, symbol = 'HK', raw_symbol.zfill(5)
            elif exchange == 'us' and kind in ('GP', 'ETF'):
                m = 'US'
                symbol = re.sub(r'\.(?:oq|n|a|ps|o|p)$', '', raw_symbol, flags=re.I).replace('.', '-').upper()
            else:
                continue
            code = asset_id(m, symbol)
        except ValueError:
            continue
        if code in seen or market not in ('ALL', m):
            continue
        seen.add(code)
        rows.append({'code': code, 'symbol': symbol, 'name': name[:100], 'market': m,
                     'type': 'etf' if kind in ('ETF', 'LOF') else 'stock',
                     'currency': {'CN': 'CNY', 'HK': 'HKD', 'US': 'USD'}[m],
                     'provider_symbol': exchange + raw_symbol if m != 'US' else symbol,
                     'search_source': '腾讯证券搜索', 'watch': False, 'benchmark': False})
    return rows


def search_securities(query, market='ALL'):
    if market not in ('ALL', 'CN', 'HK', 'US'):
        raise ValueError('不支持的市场')
    query = str(query).strip()
    if not 1 <= len(query) <= 50 or any(ord(c) < 32 for c in query):
        raise ValueError('请输入1至50个字符的名称或证券代码')
    key = (market, query.casefold())
    with SEARCH_LOCK:
        cached = SEARCH_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 180:
            return [dict(x) for x in cached[1]]
    rows, errors, aliases = [], [], []
    localized = bool(re.search(r'[\u3400-\u9fff]', query))
    if market in ('ALL', 'CN', 'HK') or localized:
        url = 'https://smartbox.gtimg.cn/s3/?' + urllib.parse.urlencode({'q': query, 't': 'all'})
        try:
            for row in parse_security_search(fetch(url), market):
                (aliases if row['market'] == 'US' else rows).append(row)
        except (OSError, ValueError) as exc:
            if market != 'US':
                errors.append(f'腾讯证券搜索：{exc}')
    if market == 'US' or market == 'ALL' and not re.fullmatch(r'\d{1,6}(?:\.HK)?', query, re.I):
        try:
            if localized and aliases:
                alias = next((row for row in aliases if row['name'] == query), aliases[0])
                rows.extend(row for row in search_massive_securities(alias['symbol']) if row['code'] == alias['code'])
            else:
                rows.extend(search_massive_securities(query))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        if rows:
            return rows
        raise ValueError('；'.join(errors))
    with SEARCH_LOCK:
        if len(SEARCH_CACHE) > 200:
            SEARCH_CACHE.clear()
        SEARCH_CACHE[key] = (time.monotonic(), rows)
    return rows


def massive_security(row):
    if not isinstance(row, dict):
        return None
    ticker, name = row.get('ticker'), row.get('name')
    if (row.get('market') != 'stocks' or row.get('locale') != 'us' or row.get('active') is not True
            or str(row.get('currency_name', '')).lower() != 'usd'
            or row.get('type') not in ('CS', 'ETF', 'ADRC', 'ADRP', 'ADRR', 'REIT')
            or not isinstance(ticker, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', ticker)
            or not isinstance(name, str) or not name.strip() or re.search(r'[<>\x00-\x1f]', name)):
        return None
    # Preserve the watchlist ID previously used for share classes, e.g. US.BRK-B.
    symbol = ticker.replace('.', '-')
    return {'code': asset_id('US', symbol), 'symbol': symbol, 'name': name.strip()[:100],
            'market': 'US', 'type': 'etf' if row['type'] == 'ETF' else 'stock', 'currency': 'USD',
            'provider_symbol': ticker, 'timezone': 'America/New_York', 'search_source': 'Massive',
            'watch': False, 'benchmark': False}


def search_massive_securities(query):
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9.-]{0,14}', query):
        query = query.replace('-', '.')
    params = {'market': 'stocks', 'locale': 'us', 'active': 'true', 'search': query, 'limit': 30}
    payload = massive_json(f'{MASSIVE_API}/v3/reference/tickers?' + urllib.parse.urlencode(params))
    results = payload.get('results', [])
    if not isinstance(results, list):
        raise ValueError('Massive 未返回有效的证券列表')
    rows = {meta['code']: meta for row in results if (meta := massive_security(row))}
    return sorted(rows.values(), key=lambda meta: meta['provider_symbol'] != query.upper())


def resolve_security(market, symbol):
    market = str(market).upper()
    code = asset_id(market, symbol)
    search_symbol = code.split('.', 1)[1] if market != 'CN' else code
    if market == 'US':
        ticker = search_symbol.replace('-', '.')
        candidates = search_securities(ticker, 'US')
        meta = next((r for r in candidates if r['provider_symbol'] == ticker), None)
        if not meta:
            url = f'{MASSIVE_API}/v3/reference/tickers/{urllib.parse.quote(ticker, safe="")}'
            meta = massive_security(massive_json(url).get('results'))
        if not meta or meta['provider_symbol'] != ticker:
            raise ValueError('Massive 未确认该美元股票或 ETF，请从美股搜索结果添加')
        return {**meta, 'watch': True, 'verified_at': now(), 'verified_name': meta['name']}
    candidates = search_securities(search_symbol, market)
    meta = next((r for r in candidates if r['code'] == code), None)
    if not meta:
        raise ValueError('证券搜索源未确认该标的；请从搜索结果添加')
    meta = dict(meta)
    if market == 'CN':
        quote = load_cn_quotes([meta['provider_symbol']]).get(meta['provider_symbol'])
        if not quote or quote['code'] != code or quote['name'] != meta['name']:
            raise ValueError('证券名称与报价源核验不一致')
    else:
        symbol = yahoo_symbol(meta)
        result, _ = read_yahoo_chart(symbol, 'range=5d&interval=1d', lambda row: row['meta'], [])
        if result.get('symbol', '').upper() != symbol.upper() or result.get('instrumentType') not in ('EQUITY', 'ETF'):
            raise ValueError('海外行情源未确认股票/ETF身份')
        if market == 'HK' and result.get('exchangeName') != 'HKG':
            raise ValueError('港股交易所核验失败')
        meta.update(currency=result.get('currency') or meta['currency'], provider_symbol=symbol,
                    type='etf' if result['instrumentType'] == 'ETF' else 'stock',
                    timezone=result.get('exchangeTimezoneName'), verified_name=result.get('longName') or result.get('shortName'))
    meta.update(watch=True, verified_at=now(), benchmark=code in BENCHMARK_CODES if market == 'CN' else False)
    return meta


def market_timezone(meta):
    name = meta.get('exchangeTimezoneName') or ('Asia/Hong_Kong' if str(meta.get('symbol', '')).endswith('.HK') else 'America/New_York')
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except (ImportError, KeyError):
        return dt.timezone(dt.timedelta(seconds=meta.get('gmtoffset', 0)))


def parse_international_chart(result, identity, current=None):
    meta = result['meta']
    ticker = yahoo_symbol(identity)
    if meta.get('symbol', '').upper() != ticker.upper():
        raise ValueError('海外历史数据标的身份不匹配')
    zone = market_timezone(meta)
    current = current or dt.datetime.now(dt.timezone.utc)
    today = current.astimezone(zone).date().isoformat()
    regular = meta.get('currentTradingPeriod', {}).get('regular', {})
    regular_date = dt.datetime.fromtimestamp(regular['start'], zone).date().isoformat() if regular.get('start') else None
    # HK close auction can finish after 16:00; use a conservative 10-minute buffer.
    buffer = 600 if identity['market'] == 'HK' else 120
    session_closed = bool(regular.get('end') and current.timestamp() >= regular['end'] + buffer)
    quote = result['indicators']['quote'][0]
    rows = {}
    for i, stamp in enumerate(result.get('timestamp') or []):
        day = dt.datetime.fromtimestamp(stamp, zone).date().isoformat()
        if day > today:
            continue
        values = {key: (quote.get(key) or [])[i] if i < len(quote.get(key) or []) else None for key in ('open', 'high', 'low', 'close', 'volume')}
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values.values()):
            continue
        if min(values[k] for k in ('open', 'high', 'low', 'close')) <= 0 or values['volume'] < 0:
            continue
        if values['low'] > min(values['open'], values['close']) or values['high'] < max(values['open'], values['close']):
            continue
        rows[day] = {'date': day, **{k: float(values[k]) for k in ('open', 'high', 'low', 'close')},
                     'vol': float(values['volume']), 'netFlow': None,
                     'completed': day < today or (day == regular_date and session_closed),
                     'observed_at': now(), 'bar_source': 'Yahoo日频OHLC（不含盘前盘后）'}
    bars = [r for _, r in sorted(rows.items())][-1000:]
    if not bars:
        raise ValueError('海外来源未返回经过校验的OHLC日线')
    last = bars[-1]
    stamp = meta.get('regularMarketTime')
    return {'source': 'Yahoo Finance日线（供应商价格口径）', 'as_of': last['date'],
            'closed_as_of': max((b['date'] for b in bars if b['completed']), default=None),
            'adjustment': 'provider', 'completed': last['completed'], 'bars': bars, 'volume_unit': '股/份',
            'market': identity['market'], 'currency': meta.get('currency') or identity['currency'],
            'timezone': meta.get('exchangeTimezoneName'), 'fetched_at': now(), 'history_checked_at': now(),
            'fundamentals': None, 'latest_quote': {'price': last['close'], 'code': identity['code'], 'name': identity['name'],
                'timestamp': dt.datetime.fromtimestamp(stamp, zone).isoformat() if stamp else last['observed_at'],
                'date': last['date'], 'source': 'Yahoo常规交易时段日线'},
            'international_method': '本币日线研究；不换算人民币。不模拟盘前盘后、港股每手数量及完整税费。'}


def load_international_asset(meta, previous=None):
    if meta['market'] == 'US':
        record = load_massive_stock(meta)
    else:
        errors = []
        record, url = read_yahoo_chart(yahoo_symbol(meta), 'range=5y&interval=1d&includePrePost=false',
                                       lambda result: parse_international_chart(result, meta), errors)
        record.update(provider_url=url, history_provider='Yahoo Finance', fallback_errors=errors)
    prior_dates = [b['date'] for b in record['bars'] if b['completed']]
    # Re-fetching all daily bars repairs the last previously-provisional trading session.
    target = max((b['date'] for b in (previous or {}).get('bars', []) if b.get('completed') is False and any(x['date'] == b['date'] and x['completed'] for x in record['bars'])), default=None)
    if not target:
        target = prior_dates[-2] if record['completed'] and len(prior_dates)>1 else prior_dates[-1] if prior_dates else None
    if target:
        verify_previous(record, previous, target)
    record['research_errors'] = {'flows': '该海外数据源未提供与A股可比的历史大单净额',
                                 'announcements': '海外公告原文索引尚未接入，不把缺失视为无风险'}
    for key in ('fundamentals', 'financial_history'):
        if (previous or {}).get(key):
            record[key] = previous[key]
    record['research_fetched_at'] = now()
    return meta['code'], record


def load_massive_stock(meta):
    # Existing catalog entries used Yahoo's dash for share classes, while Massive uses a dot.
    ticker = (meta.get('provider_symbol') or meta['symbol']).replace('-', '.')
    daily, url, timezone, delayed = massive_daily(ticker)
    checked_at = now()
    source = 'Massive 日线（拆股调整）' + (' · 延迟' if delayed else '')
    bars = []
    for row in daily:
        o, h, l, c, v = (row[key] for key in ('open', 'high', 'low', 'close', 'vol'))
        if None in (o, h, l, v) or min(o, c) <= 0 or not 0 < l <= min(o, c) or h < max(o, c) or v < 0:
            continue
        bars.append({**row, 'observed_at': checked_at, 'bar_source': source, 'netFlow': None})
    if not bars:
        raise ValueError('Massive 未返回可核验的美股 OHLCV 日线')
    last = bars[-1]
    return {'source': source, 'provider_url': url, 'history_provider': 'Massive', 'as_of': last['date'],
            'closed_as_of': max((b['date'] for b in bars if b['completed']), default=None),
            'adjustment': 'provider', 'completed': last['completed'], 'bars': bars, 'volume_unit': '股/份',
            'market': 'US', 'currency': 'USD', 'timezone': timezone, 'fetched_at': checked_at,
            'history_checked_at': checked_at, 'fundamentals': None,
            'latest_quote': {'price': last['close'], 'code': meta['code'], 'name': meta['name'],
                             'timestamp': last['date'], 'date': last['date'], 'source': source},
            'international_method': 'Massive 美元日线按拆股调整，非总回报。纽约当地当日的日线暂不进入确认回测。'}


def load_watch_record(meta, previous=None, quotes=None):
    if meta.get('market', 'CN') != 'CN':
        return load_international_asset(meta, previous)
    symbol = meta['provider_symbol']
    SYMBOLS[meta['code']] = symbol
    quotes = quotes if quotes is not None else load_cn_quotes([symbol], minimum={symbol: meta})
    code, record = load_asset((meta['code'], meta['name'], meta.get('listed')), quotes)
    target = max((b['date'] for b in record['bars'] if b['date'] < quotes[symbol]['date']), default=None)
    if target:
        verify_previous(record, previous, target)
    fresh_quote = None
    try:
        fresh_quote = load_cn_quotes([symbol], minimum=quotes, require_bar=True)[symbol]
        patch_quote(record, fresh_quote)
    except Exception as exc:
        # Bad or unavailable live OHLC must not erase independently validated daily history.
        retain_valid_history(record, exc, fresh_quote)
    record.update(market='CN', currency='CNY')
    return code, record


def enrich_added_cn(meta, record):
    issues = {}
    operations = [('financials', lambda: load_financials(meta['code'], meta['name'], record['latest_quote'])),
                  ('announcements', lambda: load_announcements(meta['code'])), ('flows', lambda: load_flows(meta['code']))]
    if meta['type'] == 'etf':
        operations = [('etf', lambda: load_holdings(meta['code'])), ('nav', lambda: load_nav(meta['code'])), ('announcements', lambda: load_announcements(meta['code']))]
    for key, operation in operations:
        try:
            value = operation()
            if key == 'financials':
                record['fundamentals'], record['financial_history'] = value
            else:
                record[key] = value
        except Exception as exc:
            issues[key] = str(exc)[:180]
    flows = {r['date']: r['netFlow'] for r in record.get('flows', [])}
    for bar in record['bars']:
        bar['netFlow'] = flows.get(bar['date'])
    record.update(research_errors=issues, research_fetched_at=now())
    return record


def load_international_financials(meta):
    # Annual statements only: never label annual figures as TTM or recent-quarter results.
    fields = ['annualTotalRevenue', 'annualNetIncome', 'annualNetIncomeCommonStockholders',
              'annualOperatingCashFlow', 'annualCapitalExpenditure', 'annualTotalAssets',
              'annualTotalLiabilitiesNetMinorityInterest', 'annualGrossProfit']
    url = 'https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/' + urllib.parse.quote(yahoo_symbol(meta), safe='') + '?' + urllib.parse.urlencode({
        'type': ','.join(fields), 'period1': int(time.time()-5*366*86400), 'period2': int(time.time())})
    result = json_get(url)['timeseries'].get('result') or []
    values, currencies = {}, {}
    for group in result:
        for key in fields:
            for row in group.get(key) or []:
                date = row.get('asOfDate')
                raw = row.get('reportedValue', {}).get('raw')
                if (row.get('periodType') == '12M' and isinstance(date, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date)
                        and date <= dt.datetime.now(TZ).date().isoformat() and isinstance(raw, (int, float)) and math.isfinite(raw)):
                    values.setdefault(date, {})[key] = float(raw)
                    if row.get('currencyCode'):
                        currencies.setdefault(date, set()).add(row['currencyCode'])
    periods = sorted(d for d, v in values.items() if 'annualTotalRevenue' in v and ('annualNetIncome' in v or 'annualNetIncomeCommonStockholders' in v))
    if not periods:
        raise ValueError('来源未返回同一年度的营收和净利润；财务保持缺失')
    period, current = periods[-1], values[periods[-1]]
    if len(currencies.get(period, set())) != 1:
        raise ValueError('年度财报币种缺失或不一致')
    currency = next(iter(currencies[period]))
    previous = next((d for d in reversed(periods[:-1]) if 320 <= (dt.date.fromisoformat(period)-dt.date.fromisoformat(d)).days <= 410 and currencies.get(d) == {currency}), None)
    profit = lambda v: v.get('annualNetIncomeCommonStockholders', v.get('annualNetIncome'))
    old = values.get(previous, {})
    revenue, income, ocf = current.get('annualTotalRevenue'), profit(current), current.get('annualOperatingCashFlow')
    f = {'period': period, 'period_basis': '年度累计，非TTM或最近季度', 'notice_date': None,
         'currency': currency, 'revenue': revenue, 'profit': income, 'deductProfit': None,
         'revenueGrowth': (revenue/old['annualTotalRevenue']-1)*100 if old.get('annualTotalRevenue', 0)>0 else None,
         'profitGrowth': (income/profit(old)-1)*100 if profit(old) and profit(old)>0 else None,
         'operatingCash': ocf, 'cashRatio': ocf/income if ocf is not None and income and income>0 else None,
         'freeCashFlow': ocf-abs(current['annualCapitalExpenditure']) if ocf is not None and 'annualCapitalExpenditure' in current else None,
         'debtRatio': current['annualTotalLiabilitiesNetMinorityInterest']/current['annualTotalAssets']*100 if current.get('annualTotalAssets', 0)>0 and 'annualTotalLiabilitiesNetMinorityInterest' in current else None,
         'grossMargin': current['annualGrossProfit']/revenue*100 if revenue and 'annualGrossProfit' in current else None,
         'source': 'Yahoo年度财务披露汇编 · ' + currency, 'source_url': url, 'cash_source_url': url,
         'fetched_at': now(), 'publication_date_note': '未返回公告披露日；不将最新财务回填历史回测'}
    history = [{'REPORT_DATE': d, 'TOTALOPERATEREVE': values[d].get('annualTotalRevenue'),
                'PARENTNETPROFIT': profit(values[d]), 'NETCASH_OPERATE_PK': values[d].get('annualOperatingCashFlow'),
                'currency': currency} for d in reversed(periods) if currencies.get(d) == {currency}]
    return f, history


def screening_member(holding):
    code = holding.get('code', '')
    if not isinstance(code, str) or not re.fullmatch(r'(?:00|30|60|68|43|83|87|92)\d{4}', code):
        return None
    symbol = ('sh' if code.startswith('6') else 'bj' if code.startswith(('4', '8', '92')) else 'sz') + code
    return {'code': code, 'symbol': code, 'market': 'CN', 'currency': 'CNY', 'type': 'stock',
            'name': str(holding.get('name') or code)[:100], 'provider_symbol': symbol, 'watch': False, 'benchmark': False}


def load_screening_record(meta, previous=None):
    meta = dict(meta)
    if meta['market'] == 'CN':
        quote = load_cn_quotes([meta['provider_symbol']]).get(meta['provider_symbol'])
        if not quote or quote.get('code') != meta['code']:
            raise ValueError('披露持股代码与报价身份不一致')
        meta['name'] = quote['name']
    _, record = load_watch_record(meta, previous)
    if meta['market'] == 'CN':
        try:
            record['fundamentals'], record['financial_history'] = load_financials(meta['code'], meta['name'], record['latest_quote'])
            f = record['fundamentals']
            denominator = (f.get('floatShares') or f.get('shares')) if record.get('volume_unit') == '股/份' else None
            turnovers = [100*b['vol']/denominator for b in record['bars'][-60:] if b.get('completed')] if denominator else []
            f['turnover3m'] = sum(turnovers)/len(turnovers) if len(turnovers)>=40 else None
        except Exception as exc:
            record.setdefault('research_errors', {})['financials'] = str(exc)[:200]
    else:
        try:
            record['fundamentals'], record['financial_history'] = load_international_financials(meta)
        except Exception as exc:
            record.setdefault('research_errors', {})['financials'] = str(exc)[:200]
    if record.get('live_error'):
        record.setdefault('research_errors', {})['live_quote'] = record['live_error'] + '；保留已校验历史日线'
    return meta, record


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
        self.commit_lock = threading.RLock()
        self.watch_operation_lock = threading.Lock()
        self.asset_jobs, self.job_versions = {}, {}
        self.screen_jobs, self.screening_cache = {}, {}
        self.catalog = base_catalog()
        self.watchlist_revision = 0
        self.registry_activated = False
        self.data_revision = 0
        self.state = {'busy': False, 'done': 0, 'total': len(ASSETS), 'stage': 'idle', 'errors': {}, 'last_refresh': None,
                      'price_result': None, 'quote_sources': {}, 'successful_quote_sources': [],
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

        cached_catalog = self.snapshot.get('catalog')
        if isinstance(cached_catalog, list) and len(cached_catalog) <= 200:
            for item in cached_catalog:
                try:
                    market = item['market']
                    if asset_id(market, item['symbol']) != item['code'] or item.get('type') not in ('stock', 'etf'):
                        continue
                    safe = {k: item[k] for k in ['code', 'symbol', 'market', 'type', 'name', 'currency', 'provider_symbol', 'listed', 'timezone', 'verified_name', 'verified_at'] if k in item}
                    if not isinstance(safe.get('name'), str) or len(safe['name']) > 100:
                        continue
                    if market == 'CN' and not re.fullmatch(r'(?:sh|sz|bj)\d{6}', safe.get('provider_symbol', '')):
                        continue
                    safe.update(watch=item.get('watch') is True, benchmark=item['code'] in BENCHMARK_CODES if market == 'CN' else False)
                    self.catalog[item['code']] = safe
                    if market == 'CN':
                        SYMBOLS[item['code']] = safe['provider_symbol']
                except (KeyError, TypeError, ValueError):
                    continue
            self.watchlist_revision = max(0, int(self.snapshot.get('watchlist_revision') or 0))
            self.registry_activated = True
        self.data_revision = max(0, int(self.snapshot.get('data_revision') or 0))
        cached = self.snapshot.get('screening_cache')
        if isinstance(cached, dict):
            self.screening_cache = {k:v for k,v in list(cached.items())[-200:] if isinstance(v, dict)}
        if self.registry_activated and not self.snapshot.get('watchlist_defaults_version'):
            missing = BENCHMARK_CODES - {code for code in BENCHMARK_CODES if self.catalog[code]['watch']}
            for code in missing:
                self.catalog[code]['watch'] = True
            if missing:
                self.watchlist_revision += 1
            self.commit_snapshot(self.snapshot)

    def catalog_view(self):
        return [dict(x) for x in self.catalog.values()]

    def active_metadata(self):
        with self.lock:
            return [dict(m) for m in self.catalog.values() if m.get('watch') or m.get('benchmark')]

    def check_revision(self, expected):
        if type(expected) is not int or expected != self.watchlist_revision:
            raise RuntimeError('自选列表已在其他页面更新，请同步后重试')

    def save_registry(self):
        with self.lock:
            snapshot = json.loads(json.dumps(self.snapshot))
            snapshot.update(catalog=self.catalog_view(), watchlist_revision=self.watchlist_revision, fetched_at=now())
        self.commit_snapshot(snapshot)

    def add_watch(self, market, symbol, expected_revision):
        market = str(market).upper()
        if market == 'US':
            symbol = str(symbol).replace('.', '-')
        code = asset_id(market, symbol)
        with self.watch_operation_lock:
            with self.lock:
                self.check_revision(expected_revision)
                if self.catalog.get(code, {}).get('watch'):
                    raise RuntimeError('该证券已在自选中')
                if sum(bool(m.get('watch')) for m in self.catalog.values()) >= 50:
                    raise ValueError('当前最多支持50只自选')
            meta = resolve_security(market, symbol)
            with self.lock:
                self.catalog[code] = {**self.catalog.get(code, {}), **meta, 'watch': True}
                self.watchlist_revision += 1
                if market == 'CN':
                    SYMBOLS[code] = meta['provider_symbol']
            self.save_registry()
            self.start_asset(code)
        return meta

    def remove_watch(self, code, expected_revision):
        with self.watch_operation_lock:
            with self.lock:
                self.check_revision(expected_revision)
                if code not in self.catalog or not self.catalog[code].get('watch'):
                    raise ValueError('该证券不在自选中')
                self.catalog[code]['watch'] = False
                self.watchlist_revision += 1
                self.job_versions[code] = secrets.token_hex(8)
                self.asset_jobs.pop(code, None)
            self.save_registry()

    def start_asset(self, code):
        with self.lock:
            meta = self.catalog.get(code)
            if not meta or not (meta.get('watch') or meta.get('benchmark')):
                raise ValueError('该证券不在当前自选或观察池中')
            if self.asset_jobs.get(code, {}).get('status') == 'loading':
                return False
            version = secrets.token_hex(8)
            self.job_versions[code] = version
            self.asset_jobs[code] = {'status': 'loading', 'stage': '重新获取日线', 'started_at': now()}
        threading.Thread(target=self.refresh_asset, args=(code, version), daemon=True).start()
        return True

    def refresh_asset(self, code, version):
        try:
            with self.lock:
                meta = dict(self.catalog[code])
                previous = json.loads(json.dumps(self.snapshot.get('instruments', {}).get(code, {})))
            _, record = load_watch_record(meta, previous)
            if meta['market'] == 'CN' and meta['type'] == 'etf' and previous.get('etf_flow'):
                record['etf_flow'] = previous['etf_flow']
            if meta['market'] == 'CN' and meta['type'] == 'stock' and previous.get('flows'):
                # Price refresh can finish before the independent money-flow feed.
                record['flows'] = previous['flows']
            with self.lock:
                if self.job_versions.get(code) != version or not self.catalog[code].get('watch') and not self.catalog[code].get('benchmark'):
                    return
                snapshot = {**self.snapshot, 'instruments': {**self.snapshot.get('instruments', {}), code: json.loads(json.dumps(record))}, 'fetched_at': now(), 'catalog': self.catalog_view()}
                self.asset_jobs[code]['stage'] = '补充研究数据'
            self.commit_snapshot(snapshot)
            if meta['market'] == 'CN':
                record = enrich_added_cn(meta, record)
                if meta['type'] == 'etf' and meta.get('watch'):
                    refresh_etf_flows({code: record}, {code: previous}, [code])
            else:
                try:
                    record['fundamentals'], record['financial_history'] = load_international_financials(meta)
                except Exception as exc:
                    record.setdefault('research_errors', {})['financials'] = str(exc)[:200]
            with self.lock:
                if self.job_versions.get(code) != version or not self.catalog[code].get('watch') and not self.catalog[code].get('benchmark'):
                    return
                snapshot = {**self.snapshot, 'instruments': {**self.snapshot.get('instruments', {}), code: record}, 'fetched_at': now(), 'catalog': self.catalog_view()}
            self.commit_snapshot(snapshot)
            with self.lock:
                if self.job_versions.get(code) == version:
                    self.asset_jobs[code] = {'status': 'ready', 'stage': '完成', 'finished_at': now(), 'research_gaps': record.get('research_errors', {})}
        except Exception as exc:
            with self.lock:
                if self.job_versions.get(code) == version:
                    self.asset_jobs[code] = {'status': 'error', 'error': str(exc)[:300], 'finished_at': now()}

    def start_screening(self, payload):
        source, codes, market = payload.get('source'), payload.get('codes'), payload.get('market', 'CN')
        if source not in ('watch', 'etf') or market not in ('CN', 'HK', 'US'):
            raise ValueError('请选择股票池与市场')
        if not isinstance(codes, list) or not 1 <= len(codes) <= (50 if source == 'watch' else 3) or any(not isinstance(c, str) for c in codes) or len(set(codes)) != len(codes):
            raise ValueError('请选择1至50只股票，或1至3只自选ETF')
        with self.lock:
            if any(j.get('status') == 'loading' for j in self.screen_jobs.values()):
                raise RuntimeError('已有筛选任务正在补充数据，请完成后重试')
            selected = []
            for code in codes:
                meta = self.catalog.get(code)
                if not meta or not meta.get('watch') or meta.get('type') != ('stock' if source == 'watch' else 'etf'):
                    raise ValueError('仅可从当前自选股票或自选ETF中选择')
                if meta['market'] != market or source == 'etf' and market != 'CN':
                    raise ValueError('单次筛选限同一市场；ETF披露持股当前支持A股ETF')
                selected.append(dict(meta))
            job_id = secrets.token_hex(12)
            # Bound in-memory results; an in-progress job is never evicted.
            self.screen_jobs = dict(list(self.screen_jobs.items())[-2:])
            self.screen_jobs[job_id] = {'status': 'loading', 'stage': '核验股票池', 'done': 0, 'total': 0, 'started_at': now()}
        threading.Thread(target=self.prepare_screening, args=(job_id, source, selected, payload.get('refresh') is True), daemon=True).start()
        return job_id

    def prepare_screening(self, job_id, source, selected, refresh):
        try:
            with self.lock:
                snapshot = json.loads(json.dumps(self.snapshot))
                cache = json.loads(json.dumps(self.screening_cache))
            members, origins, disclosures, issues = {}, {}, [], {}
            if source == 'watch':
                members = {m['code']: m for m in selected}
            else:
                for etf in selected:
                    disclosure = snapshot.get('instruments', {}).get(etf['code'], {}).get('etf') or {}
                    try:
                        age = (dt.datetime.now(TZ).date() - dt.date.fromisoformat(disclosure.get('period', ''))).days
                    except (ValueError, TypeError):
                        age = 9999
                    if refresh or not disclosure.get('holdings') or not 0 <= age <= 220:
                        disclosure = load_holdings(etf['code'])
                    if not disclosure.get('holdings'):
                        raise ValueError(etf['name'] + '未返回披露持股，不能构建股票池')
                    disclosures.append({'code': etf['code'], 'name': etf['name'], 'period': disclosure.get('period'),
                                        'source': disclosure.get('source'), 'url': disclosure.get('url'), 'count': len(disclosure['holdings'])})
                    for holding in disclosure['holdings']:
                        meta = screening_member(holding)
                        if not meta:
                            issues[etf['code'] + ':' + str(holding.get('code'))] = '不属于当前可核验A股股票范围，已排除'
                            continue
                        members.setdefault(meta['code'], meta)
                        origins.setdefault(meta['code'], []).append({'etf': etf['code'], 'name': etf['name'], 'weight': holding.get('weight'), 'period': disclosure.get('period')})
            if not members or len(members) > 100:
                raise ValueError('去重股票池为空或超过100只；请减少ETF数量')
            records, errors = {}, {}
            with self.lock:
                self.screen_jobs[job_id].update(total=len(members), stage='补充行情与财务')
            def prepare(meta):
                code = meta['code']
                old = snapshot.get('instruments', {}).get(code) or cache.get(code, {}).get('record') or {}
                try:
                    day = dt.date.fromisoformat(old.get('as_of', ''))
                    fresh = 0 <= (dt.datetime.now(TZ).date() - day).days <= 7
                except (ValueError, TypeError):
                    fresh = False
                if not refresh and fresh and old.get('bars') and old.get('fundamentals'):
                    return meta, old, '复用带日期的公开快照'
                try:
                    verified, record = load_screening_record(meta, old)
                    return verified, record, '本次重新获取'
                except Exception as exc:
                    if old.get('bars'):
                        retained = json.loads(json.dumps(old))
                        retained.setdefault('research_errors', {})['refresh'] = str(exc)[:200]
                        return meta, retained, '本次更新失败，保留带日期的旧快照'
                    raise
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(prepare, m): m['code'] for m in members.values()}
                for future in concurrent.futures.as_completed(futures):
                    code = futures[future]
                    try:
                        meta, record, origin = future.result()
                        members[code], records[code] = meta, record
                        members[code]['data_origin'] = origin
                        if record.get('research_errors'):
                            errors[code] = record['research_errors']
                    except Exception as exc:
                        errors[code] = {'provider': str(exc)[:250]}
                    with self.lock:
                        self.screen_jobs[job_id]['done'] += 1
            # Screening records never join WATCH_CODES or the main risk/backtest universe.
            with self.lock:
                for code, record in records.items():
                    self.screening_cache[code] = {'meta': members[code], 'record': record}
                self.screening_cache = dict(list(self.screening_cache.items())[-200:])
                saved = {**self.snapshot, 'screening_cache': self.screening_cache}
            self.commit_snapshot(saved)
            result = {'catalog': list(members.values()), 'instruments': records,
                      'benchmark_instruments': {c:r for c,r in snapshot.get('instruments', {}).items() if c in BENCHMARK_CODES},
                      'external': snapshot.get('external', []), 'origins': origins, 'disclosures': disclosures,
                      'errors': errors, 'pool_issues': issues, 'source': source, 'fetched_at': now()}
            with self.lock:
                self.screen_jobs[job_id].update(status='ready', stage='数据准备完成', result=result, finished_at=now())
        except Exception as exc:
            with self.lock:
                self.screen_jobs[job_id].update(status='error', error=str(exc)[:300], finished_at=now())

    def start(self):
        with self.lock:
            if self.state['busy']:
                return False
            self.state.update(busy=True, done=0, total=sum(bool(m.get('watch') or m.get('benchmark')) for m in self.catalog.values()),
                              errors={}, quote_sources={}, successful_quote_sources=[], price_result=None,
                              stage='准备刷新', started_at=now())
        try:
            threading.Thread(target=self.refresh, daemon=True).start()
        except RuntimeError:
            with self.lock:
                self.state.update(busy=False, stage='刷新任务未启动', price_result='failed',
                                  errors={'刷新服务': '无法启动后台刷新任务'}, last_refresh=now())
            raise
        return True

    def refresh(self):
        instruments, errors, quote_only, quote_sources = {}, {}, {}, {}
        successful_sources = set()
        prices_published = False
        try:
            with self.lock:
                previous = json.loads(json.dumps(self.snapshot))
                self.state['stage'] = '复核前一交易日收盘K线'
            active = self.active_metadata()
            cn_active = [m for m in active if m['market'] == 'CN']
            cn_assets = [(m['code'], m['name'], m.get('listed')) for m in cn_active]
            quote_symbols = [m['provider_symbol'] for m in cn_active] + [SYMBOLS[a[0]] for a in SECTOR_ASSETS]
            quote_minimum = {m['provider_symbol']: {
                'name': m['name'],
                'timestamp': previous.get('instruments', {}).get(m['code'], {}).get('latest_quote', {}).get('timestamp'),
            } for m in cn_active}
            quote_sources['初始报价'] = {}
            try:
                quotes = load_cn_quotes(quote_symbols, issues=quote_sources['初始报价'], minimum=quote_minimum)
            except OSError as exc:
                errors['A股初始报价'] = str(exc)
                quotes = {}
            with self.lock:
                self.state.update(errors=dict(errors), quote_sources={'初始报价': dict(quote_sources['初始报价'])})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(load_asset, a, quotes): a[0] for a in cn_assets} if quotes else {}
                futures.update({pool.submit(load_international_asset, m, previous.get('instruments', {}).get(m['code'])): m['code'] for m in active if m['market'] != 'CN'})
                for future in concurrent.futures.as_completed(futures):
                    code = futures[future]
                    try:
                        key, record = future.result()
                        instruments[key] = record
                    except Exception as exc:
                        errors[code] = str(exc)[:400]
                        old = previous.get('instruments', {}).get(code, {})
                        quote = quotes.get(SYMBOLS.get(code))
                        if (old.get('bars') and quote and quote.get('name') == self.catalog[code]['name']
                                and quote['timestamp'] > (old.get('latest_quote', {}).get('timestamp') or '')
                                and quote['date'] >= old.get('as_of', '')):
                            quote_only[code] = {**old, 'latest_quote': {**quote, 'quote_only': True},
                                                'live_error': errors[code]}
                    with self.lock:
                        self.state['done'] += 1
            reference = instruments.get('510300') or next((instruments[a[0]] for a in cn_assets if a[0] in instruments), None)
            reference_day = max((q['date'] for q in quotes.values()), default=dt.datetime.now(TZ).date().isoformat())
            target = max((b['date'] for b in (reference or {}).get('bars', []) if b['date'] < reference_day), default=None)
            previous_session = max(filter(None, (target, previous.get('previous_session'))), default=None)
            for code, record in instruments.items():
                if target and self.catalog[code]['market'] == 'CN':
                    verify_previous(record, previous.get('instruments', {}).get(code), target)
            with self.lock:
                self.state['stage'] = '更新当天实时日K'
            fresh_quotes, live_error = {}, None
            if quotes:
                quote_sources['二次报价'] = {}
                try:
                    fresh_quotes = load_cn_quotes(quote_symbols, issues=quote_sources['二次报价'],
                                                  minimum={**quote_minimum, **quotes}, require_bar=True)
                except OSError as exc:
                    live_error = str(exc)
                    errors['A股二次报价'] = live_error
            with self.lock:
                self.state.update(errors=dict(errors), quote_sources={phase: dict(issues) for phase, issues in quote_sources.items()})
            for code, record in instruments.items():
                if self.catalog[code]['market'] != 'CN':
                    continue
                quote = fresh_quotes.get(SYMBOLS[code])
                try:
                    if live_error:
                        raise ConnectionError(live_error)
                    if not quote:
                        raise ValueError('全部报价源均未返回该标的有效报价，详见行情源尝试')
                    patch_quote(record, quote)
                except Exception as exc:
                    retain_valid_history(record, exc, quote)
                    if not live_error:
                        errors[code + ':live'] = str(exc)[:200]
                old = previous.get('instruments', {}).get(code, {})
                for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'etf_flow', 'research_errors', 'research_fetched_at']:
                    if key in old:
                        record[key] = old[key]
            # Publish a detached price snapshot before slower research requests finish.
            # Charts can update immediately while all research keeps its own timestamps.
            price_records = {**instruments, **quote_only}
            if price_records:
                interim = {'schema_version': 4, 'data_kind': 'local', 'as_of': max(r['as_of'] for r in price_records.values()),
                           'closed_as_of': min(r.get('closed_as_of') or r['as_of'] for r in price_records.values()),
                           'previous_session': previous_session, 'fetched_at': now(), 'instruments': price_records, 'catalog': self.catalog_view(),
                           'external': previous.get('external', []), 'geopolitics': previous.get('geopolitics', {}), 'sectors': previous.get('sectors', []), 'errors': errors}
                self.commit_snapshot(json.loads(json.dumps(interim)))
                prices_published = True
                successful_sources.update(QUOTE_PROVIDER_NAMES.get(quote.get('source'))
                                          for quote in (*quotes.values(), *fresh_quotes.values()))
                successful_sources.update(record.get('history_provider') for record in instruments.values())
                successful_sources.discard(None)
            with self.lock:
                self.state.update(stage='可用行情已更新，正在补充研究数据' if prices_published else '补充财务、公告与产业证据',
                                  errors=dict(errors), successful_quote_sources=sorted(successful_sources))

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
                    for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'etf_flow', 'research_errors', 'research_fetched_at']:
                        if key in old:
                            rec[key] = old[key]
                else:
                    issues = {}
                    if self.catalog.get(code, {}).get('type') == 'etf':
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
                if self.catalog.get(code, {}).get('type') != 'etf' and age < 21600:
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
                for _ in pool.map(research, cn_assets):
                    pass
            etf_codes = [m['code'] for m in active if m['market'] == 'CN' and m['type'] == 'etf' and m.get('watch')]
            with self.lock:
                self.state['stage'] = '更新自选ETF净申赎估算'
            refresh_etf_flows(instruments, previous.get('instruments', {}), etf_codes)
            for meta in active:
                if meta['market'] != 'CN' and meta['code'] in instruments:
                    try:
                        instruments[meta['code']]['fundamentals'], instruments[meta['code']]['financial_history'] = load_international_financials(meta)
                    except Exception as exc:
                        instruments[meta['code']].setdefault('research_errors', {})['financials'] = str(exc)[:180]
            has_external_update = False
            try:
                external = merge_external(previous.get('external', []), load_external())
                fresh_external = [r for r in external if r.get('price') is not None and not r.get('error') and not r.get('retained')]
                has_external_update = bool(fresh_external)
                successful_sources.update(r['provider'] for r in fresh_external if r.get('provider'))
                for row in external:
                    if row.get('error'):
                        errors['外围:' + row['symbol']] = row['error']
            except Exception as exc:
                external = previous.get('external', [])
                errors['外围行情'] = str(exc)[:180]
            try:
                geopolitics = load_geopolitics()
            except Exception as exc:
                geopolitics = {**previous.get('geopolitics', {}), 'error': str(exc)[:180]}
                errors['geopolitics'] = str(exc)[:180]
            sectors = {s['code']: s for s in previous.get('sectors', [])}
            for a in SECTOR_ASSETS if fresh_quotes else []:
                try:
                    _, sector = load_asset(a, fresh_quotes)
                    patch_quote(sector, fresh_quotes[SYMBOLS[a[0]]])
                    if sector['as_of'] >= sectors.get(a[0], {}).get('as_of', ''):
                        sectors[a[0]] = {'code': a[0], 'name': a[1], **sector}
                        if sector.get('history_provider'):
                            successful_sources.add(sector['history_provider'])
                except Exception as exc:
                    errors['sector:' + a[0]] = str(exc)[:180]
            snapshot = {'schema_version': 4, 'data_kind': 'local',
                        'as_of': max((r['as_of'] for r in instruments.values()), default=None),
                        'closed_as_of': min((r.get('closed_as_of') or r['as_of'] for r in instruments.values()), default=None),
                        'fetched_at': now(), 'previous_session': previous_session, 'external': external, 'geopolitics': geopolitics, 'sectors': list(sectors.values()),
                        'instruments': price_records, 'catalog': self.catalog_view(), 'errors': errors}
            # Keep the previous snapshot timestamp when no price observations were retrieved.
            if price_records or has_external_update:
                self.commit_snapshot(snapshot)
                prices_published = True
        except Exception as exc:
            errors['刷新任务'] = f'{type(exc).__name__}：{str(exc)[:380]}'
        finally:
            with self.lock:
                if not prices_published and not errors:
                    errors['刷新任务'] = '各行情源未返回可发布的价格'
                price_errors = any(key != 'geopolitics' for key in errors)
                result = ('partial' if price_errors else 'ok') if prices_published else 'failed'
                self.state.update(busy=False, stage='完成', errors=errors, quote_sources=quote_sources,
                                  successful_quote_sources=sorted(successful_sources) if prices_published else [],
                                  price_result=result, last_refresh=now())

    def commit_snapshot(self, snapshot):
        with self.commit_lock:
            # Fresh market data remains usable even when local persistence is unavailable.
            with self.lock:
                if self.registry_activated or 'catalog' in snapshot:
                    self.registry_activated = True
                    enabled = {m['code'] for m in self.catalog.values() if m.get('watch') or m.get('benchmark')}
                    records = {c: r for c, r in self.snapshot.get('instruments', {}).items() if c in enabled}
                    for code, record in snapshot.get('instruments', {}).items():
                        if code not in enabled:
                            continue
                        old = records.get(code, {})
                        if record.get('latest_quote', {}).get('quote_only') and (
                                str(record['latest_quote'].get('timestamp') or '') <
                                str(old.get('latest_quote', {}).get('timestamp') or '')):
                            continue
                        if old.get('as_of') == record.get('as_of') and old.get('bars') and record.get('bars'):
                            old_bar, new_bar = old['bars'][-1], record['bars'][-1]
                            if ((old_bar.get('completed') is True and new_bar.get('completed') is False)
                                    or (old_bar.get('completed') == new_bar.get('completed')
                                        and str(old_bar.get('observed_at') or '') > str(new_bar.get('observed_at') or ''))):
                                continue
                        if (str(record.get('as_of') or '') >= str(old.get('as_of') or '')
                                and str(record.get('history_checked_at') or '') >= str(old.get('history_checked_at') or '')):
                            records[code] = record
                    self.data_revision += 1
                    snapshot = {**snapshot, 'instruments': records, 'catalog': self.catalog_view(), 'watchlist_revision': self.watchlist_revision,
                                'watchlist_defaults_version': WATCHLIST_DEFAULTS_VERSION, 'data_revision': self.data_revision}
                    snapshot['screening_cache'] = self.screening_cache
                    snapshot['as_of'] = max((r.get('as_of') for r in records.values() if r.get('as_of')), default=None)
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
    server_version = 'RiskLensLocal/0.9.0'

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

    def is_page_request(self):
        site = self.headers.get('Sec-Fetch-Site')
        if site not in (None, 'same-origin'):
            return False
        origin = self.headers.get('Origin')
        if origin is None:
            return True
        try:
            url = urllib.parse.urlsplit(origin)
            if (url.scheme not in ('http', 'https') or not url.hostname or url.username is not None
                    or url.path or url.query or url.fragment):
                return False
        except ValueError:
            return False
        # Some reverse proxies rewrite Host after the browser has verified same origin.
        return site == 'same-origin' or url.netloc.lower() == self.headers.get('Host', '').lower()

    def is_private_settings_page(self):
        host = self.headers.get('Host', '').lower()
        try:
            address = urllib.parse.urlsplit('//' + host)
            if (address.netloc != host or address.username is not None or not address.hostname
                    or (address.port or 80) != self.server.server_port):
                return False
            host_is_loopback = address.hostname == 'localhost' or ipaddress.ip_address(address.hostname).is_loopback
            return (host_is_loopback and ipaddress.ip_address(self.client_address[0]).is_loopback
                    and ipaddress.ip_address(self.server.server_address[0]).is_loopback
                    and self.headers.get('Origin') in (None, 'http://' + host))
        except ValueError:
            return False

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/session':
            if not self.is_page_request():
                return self.send({'error': '仅允许当前页面发起操作'}, status=403)
            return self.send({'token': TOKEN})
        if path == '/api/screening':
            job_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('id', [''])[0]
            with self.server.store.lock:
                job = json.loads(json.dumps(self.server.store.screen_jobs.get(job_id)))
            return self.send(job if job else {'error': '筛选任务不存在或已过期'}, status=200 if job else 404)
        if path == '/api/search':
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                rows = search_securities(params.get('q', [''])[0], params.get('market', ['ALL'])[0])
                return self.send({'results': rows, 'source': ' / '.join(sorted({r['search_source'] for r in rows})),
                                  'fetched_at': now()})
            except ValueError as exc:
                return self.send({'error': str(exc)}, status=400)
            except Exception as exc:
                return self.send({'error': str(exc)[:250]}, status=502)
        if path == '/api/status':
            with self.server.store.lock:
                result = {**self.server.store.state, 'coverage': len(self.server.store.snapshot['instruments']), 'total': sum(bool(m.get('watch') or m.get('benchmark')) for m in self.server.store.catalog.values()),
                          'as_of': self.server.store.snapshot.get('as_of'),
                          'closed_as_of': self.server.store.snapshot.get('closed_as_of'),
                          'fetched_at': self.server.store.snapshot.get('fetched_at'), 'environment': 'local-server', 'data_revision': self.server.store.data_revision, 'watchlist_revision': self.server.store.watchlist_revision, 'asset_jobs': dict(self.server.store.asset_jobs)}
            available = self.is_private_settings_page()
            result['market_keys'] = {'ifind': {'available': available, **(ifind_connection_status() if available else {})}}
            for provider in MARKET_KEY_SETTINGS:
                result['market_keys'][provider] = {'available': available, **(market_key_status(provider) if available else {})}
            result['ifind'] = result['market_keys']['ifind']
            return self.send(result)
        if path == '/api/snapshot':
            with self.server.store.lock:
                result = {**{k:v for k,v in self.server.store.snapshot.items() if k != 'screening_cache'}, 'catalog': self.server.store.catalog_view(), 'watchlist_revision': self.server.store.watchlist_revision, 'data_revision': self.server.store.data_revision}
                result['external'] = [{**r, 'weight': RISK_WEIGHTS.get(r.get('symbol'), 0), 'returns': external_changes(r.get('history', []), r.get('kind'))} for r in result.get('external', [])]
            return self.send(result)
        # Deliberate static allowlist: never serve source, cache, credentials or .git.
        name = 'index.html' if path == '/' else path.lstrip('/')
        if name not in ['index.html', 'styles.css', 'workbench.css', 'data.js', 'engine.js', 'app.js',
                        'views.js', 'research-views.js', 'strategy-views.js', 'local-connector.js', 'bootstrap.js',
                        'lightweight-charts.js', 'lightweight-charts.LICENSE.txt', 'interactive-chart.js', 'evidence-views.js',
                        'user-state.js', 'record-ui.js', 'themes.css', 'watchlist-manager.js', 'watchlist-manager.css', 'factor-screening.js', 'factor-screening.css', 'risk-engine.js', 'risk-views.js', 'risk.css', 'market-cards.js', 'market-cards.css', 'wyckoff-engine.js', 'wyckoff-views.js', 'wyckoff.css']:
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
        key_provider = MARKET_KEY_TEST_PATHS.get(self.path)
        if not key_provider and self.path not in ('/api/refresh', '/api/watchlist/add', '/api/watchlist/remove', '/api/watchlist/refresh', '/api/screening/start'):
            return self.send({'error': 'Not found'}, status=404)
        if not self.is_page_request():
            return self.send({'error': '仅允许当前页面发起操作'}, status=403)
        if key_provider and not self.is_private_settings_page():
            return self.send({'error': '请在运行服务的电脑上通过本地地址打开工作台，再配置行情密钥'}, status=403)
        if not secrets.compare_digest(self.headers.get('X-RiskLens-Token', '').encode('utf-8'), TOKEN.encode('ascii')):
            return self.send({'error': '页面会话已失效，请刷新页面后重试', 'code': 'stale_session'}, status=403)
        if self.path == '/api/refresh':
            try:
                started = self.server.store.start()
            except RuntimeError:
                return self.send({'error': '本地服务无法启动后台刷新任务，请重启后重试'}, status=503)
            return self.send({'started': started}, status=202 if started else 200)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 4096:
                raise ValueError('请求体长度无效')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('需要JSON对象')
            if key_provider:
                return self.send(test_ifind_connection(payload) if key_provider == 'ifind' else test_market_key(key_provider, payload))
            store = self.server.store
            if self.path == '/api/screening/start':
                return self.send({'id': store.start_screening(payload)}, status=202)
            if self.path == '/api/watchlist/add':
                asset = store.add_watch(payload.get('market'), payload.get('symbol'), payload.get('expected_revision'))
                return self.send({'asset': asset, 'revision': store.watchlist_revision, 'started': True}, status=202)
            if self.path == '/api/watchlist/remove':
                store.remove_watch(payload.get('code'), payload.get('expected_revision'))
                return self.send({'removed': payload['code'], 'revision': store.watchlist_revision})
            started = store.start_asset(payload.get('code'))
            return self.send({'started': started}, status=202 if started else 200)
        except RuntimeError as exc:
            return self.send({'error': str(exc)}, status=409)
        except (ValueError, TypeError, KeyError) as exc:
            return self.send({'error': str(exc)}, status=400)
        except Exception as exc:
            if key_provider:
                label = 'iFinD' if key_provider == 'ifind' else MARKET_KEY_SETTINGS[key_provider][0]
                return self.send({'error': f'{label} 测试未完成，请检查本机连接后重试'}, status=502)
            return self.send({'error': str(exc)[:300]}, status=502)


def main():
    global PROXY_MODE
    parser = argparse.ArgumentParser(description='观澜本地数据环境（只读行情，不连接券商）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cache', default=str(Path.home() / '.risklens' / 'market-cache.json'))
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-refresh', action='store_true')
    parser.add_argument('--proxy-mode', choices=('auto', 'system', 'direct'), default='auto',
                        help='auto：本机代理拒绝连接时重试直连；system：只按系统设置；direct：始终直连')
    parser.add_argument('--strictPort', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    PROXY_MODE = args.proxy_mode
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
