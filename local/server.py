#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RiskLens local research server. Python stdlib only; binds loopback by default."""
import argparse
import base64
import concurrent.futures
import contextvars
import datetime as dt
import errno
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
import html
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
    ('510050', '上证50ETF华夏', None), ('159919', '沪深300ETF嘉实', None),
    ('588000', '科创50ETF华夏', None), ('159901', '深证100ETF易方达', None),
    ('159922', '中证500ETF嘉实', None),
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
FUTU_API = 'https://webapi.futunn.com/api/v1.0/quote'
PINGAN_QUOTE = 'https://ai.stock.pingan.com/restapi/hangqing/adapter/hangQing/stockDynamic'
PINGAN_KLINE = 'https://ai.stock.pingan.com/restapi/hangqing/adapter/hangQing/klineData'
TENCENT_US_KLINE = 'https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get'
EASTMONEY_QUOTE = 'https://push2.eastmoney.com/api/qt/stock/get'
EASTMONEY_KLINE = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
EASTMONEY_SEARCH = 'https://searchapi.eastmoney.com/api/suggest/get'
QUOTE_PRIORITIES = {
    'CN': ('腾讯', '东方财富', '平安证券', '同花顺 iFinD', '富途', '新浪'),
    'FOREIGN': ('富途', 'Yahoo Finance', '腾讯', '东方财富', '同花顺 iFinD', '新浪'),
}
MARKET_KEY_SETTINGS = {
    'futu': ('富途', 'FUTU_API_KEY'),
    'massive': ('Massive', 'MASSIVE_API_KEY'),
    'pingan': ('平安证券', 'PINGAN_SKILL_APIKEY'),
}
MARKET_KEY_TEST_PATHS = {'/api/ifind/test': 'ifind', '/api/market-keys/massive/test': 'massive',
                         '/api/market-keys/pingan/test': 'pingan', '/api/market-keys/futu/test': 'futu'}
TOKEN = secrets.token_urlsafe(24)
NETWORK_LIMIT = threading.BoundedSemaphore(8)
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXY_MODE = 'auto'
EXTERNAL_SYMBOLS = ['usSOXX', 'usQQQ', 'usNVDA', 'usMU', 'usTSM', 'usAVGO', 'hkHSTECH', 'usVIX']
QUOTE_TRACE = contextvars.ContextVar('quote_trace', default=None)
QUOTE_READ_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError)


class QuoteUnavailable(ValueError):
    pass


def trace_quote(provider, status, detail='', *, elapsed=None):
    scope = QUOTE_TRACE.get()
    if scope is not None:
        record, stage, target = scope
        record({'stage': stage, 'target': target, 'provider': provider, 'status': status,
                'at': now(), 'detail': detail[:300], 'elapsed_ms': elapsed})


def traced_quote_call(record, stage, target, load, *args, **kwargs):
    token = QUOTE_TRACE.set((record, stage, target))
    try:
        return load(*args, **kwargs)
    finally:
        QUOTE_TRACE.reset(token)


def first_quote_source(routes, errors, *, date_of=None, not_before=None):
    for provider, load in routes:
        started = time.monotonic()
        prior_errors = len(errors)
        try:
            result = load()
            if not result:
                raise ValueError('未返回可用数据')
            if not_before and (day := date_of(result)) < not_before:
                raise ValueError(f'最近日线 {day} 早于缓存日期 {not_before}')
        except QUOTE_READ_ERRORS as exc:
            status = 'skipped' if isinstance(exc, QuoteUnavailable) else 'failed'
            message = str(exc) or type(exc).__name__
            errors.append(f'{provider}：{message}')
            trace_quote(provider, status, '；'.join(errors[prior_errors:-1] + [message]),
                        elapsed=round((time.monotonic()-started)*1000))
        else:
            trace_quote(provider, 'ok', '；'.join(errors[prior_errors:]),
                        elapsed=round((time.monotonic()-started)*1000))
            return provider, result
    raise ValueError('；'.join(errors))


def unsupported_quote(reason):
    raise QuoteUnavailable(reason)


def quote_routes(market, loaders):
    order = QUOTE_PRIORITIES['CN' if market == 'CN' else 'FOREIGN']
    return tuple((provider, loaders.get(provider) or
                  (lambda p=provider: unsupported_quote(p + '暂无该品种的已核验适配'))) for provider in order)


def quote_source_label(provider, suffix):
    return provider + (' ' if provider == '同花顺 iFinD' else '') + suffix


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


def fetch(url, *, user_agent=None):
    target = urllib.parse.urlsplit(url)
    host = target.hostname or ''
    referer = 'https://www.sse.com.cn/' if host == 'query.sse.com.cn' else 'https://fundf10.eastmoney.com/' if host.startswith(('fund', 'fundf10')) else 'https://quote.eastmoney.com/' if host.endswith('eastmoney.com') else 'https://finance.sina.com.cn/'
    headers = {
        'User-Agent': user_agent or 'Mozilla/5.0 RiskLensLocal/0.9.0',
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
    raise QuoteUnavailable('未配置本地 iFinD 密钥，请在「数据与方法」配置')


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


def read_market_settings(provider):
    try:
        with market_key_path(provider).open(encoding='utf-8') as handle:
            settings = json.loads(handle.read(16384))
            if not isinstance(settings, dict) or 'api_key' not in settings:
                raise ValueError('密钥配置结构无效')
            return settings
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        label = MARKET_KEY_SETTINGS[provider][0]
        raise ValueError(f'无法读取本地 {label} 密钥配置') from None


def market_key(provider):
    label, env = MARKET_KEY_SETTINGS[provider]
    settings = read_market_settings(provider)
    if settings is not None:
        return validate_market_key(provider, settings['api_key'])
    token = os.environ.get(env) or (os.environ.get('FUTU_ACCESS_TOKEN') if provider == 'futu' else None)
    if token:
        return validate_market_key(provider, token)
    raise QuoteUnavailable(f'未配置 {label} 密钥，请在「数据与方法 → 行情密钥专区」配置')


def market_key_status(provider):
    try:
        if provider == 'futu':
            return {'configured': True, 'auth_mode': futu_credentials()['mode']}
        market_key(provider)
        return {'configured': True}
    except ValueError as exc:
        return {'configured': False, 'message': str(exc)}


def authenticated_market_json(label, url, headers, data=None, *, unauthorized_hint='请检查密钥是否有效'):
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
        hints = {401: unauthorized_hint, 403: '请检查密钥及该市场或接口的订阅权限',
                 429: '已达到请求额度，请稍后重试'}
        raise ValueError(f'{label} HTTP {exc.code}：{hints.get(exc.code, "行情接口请求失败")}') from None
    except OSError as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        code = getattr(reason, 'errno', None)
        if isinstance(reason, TimeoutError):
            hint = '连接超时'
        elif isinstance(reason, ConnectionRefusedError):
            hint = '连接被拒绝，请检查网络或代理'
        elif isinstance(reason, socket.gaierror):
            hint = '域名解析失败，请检查 DNS 或代理'
        else:
            hint = '网络连接失败，请检查网络或代理'
        if isinstance(code, int):
            hint += f'（Errno {code}）'
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


def validate_futu_credentials(settings):
    mode, key = settings.get('mode'), settings.get('api_key')
    if isinstance(key, str) and '-----BEGIN ' in key:
        raise ValueError('请选 AppKey 签名模式，将私钥填入 PEM 栏，并填写对应的 AppKey ID')
    key = validate_market_key('futu', key)
    if mode == 'oauth':
        return {'mode': 'oauth', 'api_key': key}
    if mode != 'appkey':
        raise ValueError('请选择富途认证方式')
    pem = settings.get('private_key')
    pem = pem.strip().replace('\r\n', '\n') if isinstance(pem, str) else ''
    if len(pem) > 8192 or not re.fullmatch(r'-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY)-----\n[\w+/=\n]+\n-----END \1-----', pem, re.ASCII):
        raise ValueError('请粘贴完整的无口令 PEM 私钥，包含 BEGIN 和 END 两行')
    algorithm = settings.get('algorithm')
    if algorithm not in ('RSA-SHA256', 'Ed25519'):
        raise ValueError('富途签名算法应为 RSA-SHA256 或 Ed25519')
    return {'mode': mode, 'api_key': key, 'algorithm': algorithm, 'private_key': pem + '\n'}


def futu_credentials(token=None):
    settings = read_market_settings('futu') if token is None else None
    if settings is not None and 'mode' in settings:
        return validate_futu_credentials(settings)
    signing_file = os.environ.get('FUTU_SIGNING_KEY_FILE')
    if token is None:
        if settings is not None:
            token = settings['api_key']
        else:
            token = os.environ.get('FUTU_API_KEY')
            if not token:
                token = os.environ.get('FUTU_ACCESS_TOKEN')
                signing_file = None
        if not token:
            raise QuoteUnavailable('未配置富途密钥，请在「数据与方法 → 行情密钥专区」配置')
    key = validate_market_key('futu', token)
    if signing_file:
        return {'mode': 'appkey', 'api_key': key, 'signing_file': signing_file,
                'algorithm': os.environ.get('FUTU_SIGNING_ALGORITHM', 'RSA-SHA256')}
    return {'mode': 'oauth', 'api_key': key}


def futu_openssl():
    """Find a supported signer even when macOS launchers omit Homebrew from PATH."""
    override = os.environ.get('FUTU_OPENSSL_PATH')
    candidates = [str(Path(override).expanduser())] if override else [shutil.which('openssl')]
    if not override and sys.platform == 'darwin':
        prefix = os.environ.get('HOMEBREW_PREFIX')
        if prefix:
            candidates.append(str(Path(prefix) / 'opt/openssl@3/bin/openssl'))
        candidates.extend(['/opt/homebrew/opt/openssl@3/bin/openssl',
                           '/usr/local/opt/openssl@3/bin/openssl'])
    for executable in dict.fromkeys(candidate for candidate in candidates if candidate):
        try:
            result = subprocess.run([executable, 'version'], capture_output=True, check=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        version = re.match(rb'OpenSSL\s+(\d+)\.', result.stdout)
        if version and int(version[1]) >= 3:
            return executable
    hint = ('FUTU_OPENSSL_PATH 未指向可运行的 OpenSSL 3 或更新版本，请修正后重启服务'
            if override else '未找到可运行的 OpenSSL 3 或更新版本；Mac 请运行 brew install openssl@3 后重启服务')
    raise QuoteUnavailable('富途本机签名环境不可用：' + hint)


def futu_private_key_algorithm(executable, path):
    # Inspect only the public SPKI; never include OpenSSL stderr or key contents in errors.
    try:
        public = subprocess.run([executable, 'pkey', '-in', path, '-passin', 'pass:',
                                 '-pubout', '-outform', 'DER'],
                                capture_output=True, check=True, timeout=5).stdout
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or b'').lower()
        if b'permission denied' in error:
            raise QuoteUnavailable('富途私钥文件无读取权限，请检查运行服务的账户权限') from None
        if b'no such file' in error:
            raise QuoteUnavailable('富途私钥文件不存在，请检查 FUTU_SIGNING_KEY_FILE') from None
        raise QuoteUnavailable('富途 PEM 私钥无法解析：请粘贴完整、无口令的私钥；不要粘贴公钥或字面的 \\n') from None
    if len(public) == 44 and public.startswith(bytes.fromhex('302a300506032b6570032100')):
        return 'Ed25519'
    if bytes.fromhex('06092a864886f70d010101') in public[:32]:
        return 'RSA-SHA256'
    raise QuoteUnavailable('富途私钥类型不支持：请使用 Ed25519 或普通 RSA 私钥')


def futu_headers(url, credentials, body):
    key = credentials['api_key']
    if credentials['mode'] == 'oauth':
        return {'Authorization': 'Bearer ' + key}
    algorithm = credentials['algorithm']
    if algorithm not in ('RSA-SHA256', 'Ed25519'):
        raise QuoteUnavailable('富途签名算法应为 RSA-SHA256 或 Ed25519')
    executable = futu_openssl()
    timestamp = str(time.time_ns() // 1_000_000)
    target = urllib.parse.urlsplit(url)
    digest = hashlib.sha256(body).hexdigest() if body is not None else ''
    payload = '\n'.join((timestamp, 'POST' if body is not None else 'GET', target.path, target.query, digest)).encode()
    try:
        with tempfile.TemporaryDirectory() as folder:
            if 'private_key' in credentials:
                with tempfile.NamedTemporaryFile(mode='w', encoding='ascii', dir=folder, delete=False) as handle:
                    handle.write(credentials['private_key'])
                    path = handle.name
            else:
                path = str(Path(credentials['signing_file']).expanduser().resolve())
            actual_algorithm = futu_private_key_algorithm(executable, path)
            if algorithm != actual_algorithm:
                raise QuoteUnavailable(f'富途签名算法不匹配：私钥是 {actual_algorithm}，当前选择 {algorithm}；'
                                       f'请在页面与富途 AppKey 控制台均选择 {actual_algorithm}')
            if algorithm == 'RSA-SHA256':
                command = [executable, 'dgst', '-sha256', '-sign', path, '-passin', 'pass:']
            elif algorithm == 'Ed25519':
                message = Path(folder) / 'request'
                message.write_bytes(payload)
                command = [executable, 'pkeyutl', '-sign', '-rawin', '-inkey', path, '-passin', 'pass:', '-in', str(message)]
            signature = subprocess.run(command, input=payload if algorithm == 'RSA-SHA256' else None,
                                       capture_output=True, check=True, timeout=5).stdout
    except subprocess.TimeoutExpired:
        raise QuoteUnavailable('富途本机签名超时，请检查 OpenSSL 是否能正常运行') from None
    except subprocess.CalledProcessError:
        raise QuoteUnavailable(f'富途本机 {algorithm} 签名执行失败：请检查 OpenSSL 的算法提供程序配置') from None
    except OSError:
        raise QuoteUnavailable('富途本机签名文件无法读写或 OpenSSL 无法启动，请检查临时目录与程序权限') from None
    return {'X-Api-Key': key, 'Authorization': base64.b64encode(signature).decode('ascii'),
            'X-Timestamp': timestamp, 'X-Nonce': secrets.token_hex(16)}


def futu_json(url, key=None, data=None):
    body = json.dumps(data, separators=(',', ':')).encode() if data is not None else None
    credentials = futu_credentials() if key is None else key
    headers = futu_headers(url, credentials, body)
    hint = ('请检查 AppKey ID 是否已启用，控制台公钥是否与私钥匹配，以及服务端时钟'
            if credentials['mode'] == 'appkey' else '请检查 OAuth 访问令牌是否有效或过期。AppKey 请用签名模式')
    payload = authenticated_market_json('富途', url, {**headers, 'Accept': 'application/json',
                                                       'Content-Type': 'application/json'}, body, unauthorized_hint=hint)
    code = payload.get('ret_code')
    if code not in (0, '0'):
        reason = {'-7': '标的未识别', '-8': '接口不支持此市场或品种', '-9': '行情权限不足',
                  '-10': '无数据', '-11': '触发限频', '-12': '历史行情额度不足',
                  '-12006': '请求时间偏差过大，请校准部署端时钟',
                  '-12102': '访问令牌已过期'}.get(str(code), '行情接口返回业务错误')
        safe_code = str(code) if re.fullmatch(r'-?\d{1,9}', str(code)) else '未知'
        raise ValueError(f'富途 {safe_code}：{reason}')
    return payload


def pingan_json(url, data, key=None):
    request_id = str(uuid.uuid4())
    payload = authenticated_market_json('平安证券', url, {
        'X-API-Key': key if key is not None else market_key('pingan'),
        'X-Skill-ID': 'market-query', 'X-Request-ID': request_id,
        'channelId': 'c_lite_adapgaty_a_skill', 'requestId': request_id,
        'Content-Type': 'application/json', 'Accept': 'application/json',
    }, json.dumps(data, separators=(',', ':')).encode())
    if payload.get('code') not in (0, '0'):
        raise ValueError('平安证券行情接口拒绝请求，请检查密钥、权限及额度')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise ValueError('平安证券返回了无效行情数据结构')
    return data


def pingan_test_quote(key):
    data = pingan_json(PINGAN_QUOTE, {'codes': ['SH600519']}, key)
    items = data.get('items') if isinstance(data, dict) else None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get('code') == 'SH600519':
            price = number(item.get('price'))
            if price is not None and price > 0:
                return {'price': price}
    raise ValueError('平安证券未返回可核验的样例报价')


def load_pingan_quotes(symbols, failures):
    quotes = {}
    for start in range(0, len(symbols), 5):
        batch = symbols[start:start+5]
        try:
            data = pingan_json(PINGAN_QUOTE, {'codes': [s.upper() for s in batch]})
        except QuoteUnavailable:
            raise
        except (OSError, ValueError) as exc:
            failures.append(str(exc)[:160])
            continue
        for item in data.get('items') or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get('code', '')).lower()
            if symbol not in batch:
                continue
            # The skill's documented price schema has no guaranteed trading time.
            # A request time is never substituted for a missing quotation timestamp.
            stamp = next((item.get(k) for k in ('timestamp', 'datetime', 'trade_time', 'update_time', 'time')
                          if item.get(k) is not None), None)
            stamp = stamp if stamp is not None else data.get('timestamp') or data.get('update_time')
            if isinstance(stamp, str) and re.fullmatch(r'\d{2}:\d{2}:\d{2}', stamp):
                stamp = str(item.get('date') or data.get('date') or '') + 'T' + stamp
            try:
                if isinstance(stamp, (int, float)):
                    instant = dt.datetime.fromtimestamp(stamp / 1000 if stamp >= 10**12 else stamp, TZ)
                elif isinstance(stamp, str) and len(stamp) >= 16:
                    instant = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                    instant = instant.astimezone(TZ) if instant.tzinfo else instant.replace(tzinfo=TZ)
                else:
                    raise ValueError()
            except (ValueError, TypeError, OSError, OverflowError):
                failures.append(f'{symbol} 报价缺少可核验的行情时间')
                continue
            price = number(item.get('price'))
            if price is None or price <= 0:
                continue
            quotes[symbol] = {'code': symbol[2:], 'name': item.get('name'), 'price': price,
                              'date': instant.date().isoformat(), 'timestamp': instant.isoformat(),
                              'open': number(item.get('open')), 'high': number(item.get('high')),
                              'low': number(item.get('low')), 'volume': number(item.get('volume')),
                              'amount': number(item.get('amount')), 'previous_close': number(item.get('prev_close')),
                              'turnover': number(item.get('turnover_pct')), 'source': '平安证券行情',
                              'volume_basis': '股/份', 'qualified_code': symbol.upper()}
    return quotes


def pingan_daily(symbol):
    data = pingan_json(PINGAN_KLINE, {'code': symbol.upper(), 'last_n': 640})
    item = next((item for item in data.get('items', []) if isinstance(item, dict) and item.get('code') == symbol.upper()
                 and item.get('period') == 'day'), None)
    if not item:
        raise ValueError('平安证券未返回同一标的的日线')
    adjustment = {'qfq': 'qfq', '前复权': 'qfq', 'none': 'none', '不复权': 'none', '未复权': 'none',
                  'hfq': 'hfq', '后复权': 'hfq'}.get(item.get('adjust'))
    if adjustment not in ('qfq', 'none'):
        raise ValueError('平安证券日线缺少可核验的复权口径或返回后复权数据')
    rows = [{'date': row['date'], **{k: number(row.get(k)) for k in ('open', 'high', 'low', 'close')},
             'vol': number(row.get('volume')), 'netFlow': None} for row in item.get('bars') or []]
    return rows, PINGAN_KLINE, adjustment, '股/份'


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
    has_new_key = 'token' in payload or (provider == 'futu' and 'mode' in payload)
    settings = None
    if provider == 'futu':
        if has_new_key and 'mode' in payload:
            settings = validate_futu_credentials({**payload, 'api_key': payload.get('token')})
            key = settings
        elif has_new_key:
            token = validate_futu_credentials({'mode': 'oauth', 'api_key': payload.get('token')})['api_key']
            key = futu_credentials(token)
            settings = {'api_key': token}
        else:
            key = futu_credentials()
    else:
        key = validate_market_key(provider, payload['token']) if has_new_key else market_key(provider)
        settings = {'api_key': key}
    lock = MARKET_KEY_LOCKS[provider]
    if not lock.acquire(blocking=False):
        raise RuntimeError(f'{MARKET_KEY_SETTINGS[provider][0]} 正在测试，请稍后重试')
    try:
        if provider == 'futu':
            probes = [(label, ticker, lambda ticker=ticker: futu_test_quote(ticker, key))
                      for label, ticker in (('美股', 'US.AAPL'), ('港股', 'HK.00700'))]
        elif provider == 'massive':
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
                save_private_settings(market_key_path(provider), settings)
                if provider in ('futu', 'massive'):
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


def ifind_tool(service, tool, arguments):
    global IFIND_NEXT_CALL
    token = ifind_token()
    result, session = ifind_post(service, token, 'initialize', 1, {
        'protocolVersion': '2025-03-26', 'capabilities': {},
        'clientInfo': {'name': 'LanCloudRisk', 'version': '0.9.0'}})
    version = result.get('protocolVersion') or '2025-03-26'
    ifind_post(service, token, 'notifications/initialized', session=session, version=version)
    with IFIND_LOCK:
        time.sleep(max(0, IFIND_NEXT_CALL - time.monotonic()))
        IFIND_NEXT_CALL = time.monotonic() + 0.5
        result, _ = ifind_post(service, token, 'tools/call', 2, {'name': tool, 'arguments': arguments},
                               session=session, version=version)
    if result.get('isError'):
        raise ValueError('同花顺工具返回错误，请检查工具权限或查询范围')
    return result


def ifind_rows(result):
    """Only consume structured JSON, including MCP JSON text and tabular columns."""
    output = []
    def visit(node, inherited=None):
        inherited = inherited or {}
        if isinstance(node, list):
            if (node and isinstance(node[0], list) and all(isinstance(k, str) for k in node[0])
                    and len(set(node[0])) == len(node[0])):
                for row in node[1:]:
                    if isinstance(row, list) and len(row) == len(node[0]):
                        visit(dict(zip(node[0], row)), inherited)
            else:
                for row in node:
                    visit(row, inherited)
        elif isinstance(node, dict):
            if node.get('type') == 'text':
                try:
                    raw = node.get('text', '').strip()
                    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', raw, re.S | re.I)
                    visit(json.loads(fenced[1] if fenced else raw), inherited)
                except (ValueError, RecursionError):
                    pass
                return
            fields = {**inherited, **{k: v for k, v in node.items() if not isinstance(v, (dict, list))}}
            arrays = {k: v for k, v in node.items() if isinstance(v, list) and v
                      and all(not isinstance(x, (dict, list)) for x in v)}
            if arrays and len({len(v) for v in arrays.values()}) == 1:
                for i in range(len(next(iter(arrays.values())))):
                    output.append({**fields, **{k: v[i] for k, v in arrays.items()}})
            else:
                output.append(fields)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value, fields)
    visit(result)
    return output


def field_value(row, *names):
    return next((row[name] for name in names if row.get(name) is not None), None)


def ifind_matches(row, symbol, market):
    code = str(field_value(row, 'thscode', 'thsCode', 'code', 'symbol', '证券代码', '同花顺代码') or '').upper()
    if market == 'CN':
        return code in (symbol[2:], symbol[2:] + '.' + symbol[:2].upper())
    if market == 'HK':
        return bool(re.fullmatch(r'\d{1,5}\.HK', code) and code.split('.')[0].zfill(5) == symbol.zfill(5))
    expected = symbol.replace('-', '.').upper()
    # A qualified US ticker is required to avoid matching a Chinese/HK namesake.
    candidates = (expected + '.US', expected + '.O', expected + '.OQ', expected + '.N',
                  expected + '.A', expected + '.P', 'US.' + expected)
    return code in candidates or (code == expected and row.get('market') == 'US')


def ifind_daily(symbol, market='CN'):
    fund = market == 'CN' and is_cn_etf(symbol)
    service = ('fund' if fund else 'stock') if market == 'CN' else 'global_stock'
    tool = ('get_fund_market_performance' if fund else 'get_stock_performance') if market == 'CN' else 'global_stock_quotes'
    subject = symbol[2:] + '.' + symbol[:2].upper() if market == 'CN' else ('港股 ' if market=='HK' else '美股 ') + symbol
    query = f'{subject} 最近640个交易日的每日开盘价、最高价、最低价、收盘价、成交量（股/份），不复权；返回证券代码、交易日期、成交量单位和复权方式的结构化表格，不要净值或累计净值。'
    result = ifind_tool(service, tool, {'query': query})
    bars, adjustments = {}, set()
    today = quote_day(dt.datetime.now(dt.timezone.utc), 'Asia/Shanghai' if market in ('CN','HK') else 'America/New_York')
    for row in ifind_rows(result):
        if not ifind_matches(row, symbol, market):
            continue
        day = str(field_value(row, 'date', 'time', '交易日期', '日期', '时间') or '')[:10]
        try:
            date = dt.date.fromisoformat(day)
        except ValueError:
            continue
        if date > today:
            continue
        adjustment = {'none': 'none', '不复权': 'none', '未复权': 'none', 'qfq': 'qfq', '前复权': 'qfq'}.get(
            field_value(row, 'adjust', 'adjustment', '复权方式'))
        # Do not silently relabel unspecified prices, NAVs, or lot volumes as raw OHLCV.
        if not adjustment:
            continue
        prices = {k: number(field_value(row, k, label)) for k, label in
                  [('open', '开盘价'), ('high', '最高价'), ('low', '最低价'), ('close', '收盘价')]}
        volume = number(field_value(row, '成交量(股)', '成交量（股）', '成交量(份)', '成交量（份）'))
        if volume is None and field_value(row, 'volume_unit', '成交量单位') in ('股', '份', '股/份', 'shares'):
            volume = number(field_value(row, 'volume', '成交量'))
        if any(value is None for value in prices.values()) or volume is None:
            continue
        bars[day] = {'date': day, **prices, 'vol': volume, 'netFlow': None, 'completed': date < today}
        adjustments.add(adjustment)
    if not bars or len(adjustments) != 1:
        raise ValueError('同花顺未返回同一标的、同一复权口径和明确股/份单位的 OHLCV 日线')
    return [bars[day] for day in sorted(bars)], IFIND_ENDPOINT.format(service), adjustments.pop(), '股/份'


def eastmoney_search_rows(query):
    url = EASTMONEY_SEARCH + '?' + urllib.parse.urlencode({'input': query, 'type': 14, 'count': 20})
    table = (json.loads(fetch(url)).get('QuotationCodeTable') or {})
    rows = table.get('Data')
    if table.get('Status') not in (None, 0) or not isinstance(rows, list):
        raise ValueError('东方财富证券搜索未返回有效列表')
    return rows


def eastmoney_security(row):
    symbol, name = row.get('Code'), row.get('Name')
    secid = str(row.get('QuoteID') or '')
    if not isinstance(symbol, str) or not isinstance(name, str) or not name.strip() or re.search(r'[<>\x00-\x1f]', name):
        return None
    if not re.fullmatch(r'\d{1,3}\.[A-Za-z0-9.-]{1,20}', secid) or secid.split('.', 1)[1].upper() != symbol.upper():
        return None
    category, kind = row.get('Classify'), str(row.get('SecurityType'))
    if category in ('AStock', 'Fund') and re.fullmatch(r'\d{6}', symbol) and secid.split('.')[0] in ('0', '1'):
        provider_symbol = cn_provider_symbol(symbol)
        if secid.split('.')[0] != ('1' if provider_symbol.startswith('sh') else '0'):
            return None
        return {'code': symbol, 'symbol': symbol, 'name': name[:100], 'market': 'CN', 'currency': 'CNY',
                'type': 'etf' if category == 'Fund' else 'stock', 'provider_symbol': provider_symbol,
                'eastmoney_secid': secid, 'search_source': '东方财富', 'watch': False, 'benchmark': False}
    if category == 'HK' and secid.startswith('116.') and kind in ('19', '6'):
        market, security_type = 'HK', 'etf' if kind == '6' else 'stock'
    elif category == 'UsStock' and secid.split('.')[0] in ('105', '106', '107') and kind in ('7', '20'):
        security_type = 'etf' if 'ETF' in name.upper() else 'stock'
        if security_type == 'stock' and re.search(r'\b(notes?|bonds?|warrants?|rights?)\b|债券|认股权', name, re.I):
            return None
        market = 'US'
    else:
        return None
    meta = foreign_security(market, symbol, name, security_type, '东方财富')
    return {**meta, 'eastmoney_secid': secid} if meta else None


def search_eastmoney_securities(query, market):
    return [meta for row in eastmoney_search_rows(query) if isinstance(row, dict)
            and (meta := eastmoney_security(row)) and (meta['market']==market or market=='ALL' and meta['market']!='CN')]


def resolve_eastmoney_security(market, symbol, code):
    meta = next((row for row in search_eastmoney_securities(symbol, market) if row['code'] == code), None)
    if not meta:
        raise ValueError('东方财富未核验同一市场的股票或 ETF')
    return meta


def eastmoney_secid(meta):
    if meta['market'] == 'CN':
        symbol = meta['provider_symbol']
        if not re.fullmatch(r'(?:sh|sz|bj)\d{6}', symbol):
            raise ValueError('东方财富 A股证券标识无效')
        return ('1.' if symbol.startswith('sh') else '0.') + symbol[2:]
    secid = meta.get('eastmoney_secid')
    if secid:
        expected = meta['symbol'].replace('-', '.').upper()
        prefix, _, code = str(secid).partition('.')
        if prefix not in (('116',) if meta['market']=='HK' else ('105', '106', '107')) or code.upper() != expected:
            raise ValueError('东方财富证券标识与市场或代码不匹配')
        return secid
    return resolve_eastmoney_security(meta['market'], meta['symbol'], meta['code'])['eastmoney_secid']


def eastmoney_quote(symbol):
    secid = eastmoney_secid({'market': 'CN', 'provider_symbol': symbol})
    url = EASTMONEY_QUOTE + '?' + urllib.parse.urlencode({'secid': secid, 'fltt': 2, 'invt': 2,
        'fields': 'f13,f43,f44,f45,f46,f47,f48,f57,f58,f59,f60,f86,f168'})
    payload = json.loads(fetch(url))
    row = payload.get('data')
    if payload.get('rc') != 0 or not isinstance(row, dict) or row.get('f57') != symbol[2:]:
        raise ValueError('东方财富未返回同一 A股证券的报价')
    if row.get('f13') is not None and str(row['f13']) != secid.split('.')[0]:
        raise ValueError('东方财富报价市场身份不匹配')
    instant = public_time(row.get('f86'))
    price = number(row.get('f43'))
    if instant is None or price is None or price <= 0:
        raise ValueError('东方财富报价缺少有效价格或行情时间')
    volume = number(row.get('f47'))
    # fltt=2 already returns decimal prices. CN volume is reported in lots of 100 shares/units.
    return {'code': symbol[2:], 'name': row.get('f58'), 'price': price, 'open': number(row.get('f46')),
            'high': number(row.get('f44')), 'low': number(row.get('f45')), 'volume': volume*100 if volume is not None else None,
            'amount': number(row.get('f48')), 'previous_close': number(row.get('f60')), 'turnover': number(row.get('f168')),
            'timestamp': instant.isoformat(), 'date': instant.date().isoformat(), 'source': '东方财富行情',
            'volume_basis': '手×100股/份', 'qualified_code': symbol.upper()}


def load_eastmoney_quotes(symbols, failures):
    quotes = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(eastmoney_quote, symbol): symbol for symbol in symbols}
        for future in concurrent.futures.as_completed(jobs):
            symbol = jobs[future]
            try:
                quotes[symbol] = future.result()
            except QUOTE_READ_ERRORS as exc:
                failures.append(symbol + '：' + str(exc)[:130])
    return quotes


def eastmoney_daily(secid, timezone, *, cn=False, close_only=False):
    url = EASTMONEY_KLINE + '?' + urllib.parse.urlencode({'secid': secid, 'klt': 101, 'fqt': 0,
        'lmt': 640, 'end': dt.datetime.now(TZ).strftime('%Y%m%d'), 'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'})
    payload = json.loads(fetch(url))
    data = payload.get('data')
    prefix, _, code = secid.partition('.')
    if (payload.get('rc') != 0 or not isinstance(data, dict) or str(data.get('code', '')).upper() != code.upper()
            or str(data.get('market')) != prefix or not isinstance(data.get('klines'), list)):
        raise ValueError('东方财富未返回同一市场标的的日线')
    today, rows = quote_day(dt.datetime.now(dt.timezone.utc), timezone), {}
    for line in data['klines']:
        if not isinstance(line, str):
            continue
        cells = line.split(',')
        try:
            day = dt.date.fromisoformat(cells[0])
            o, c, h, l, v = map(number, cells[1:6])
        except (ValueError, IndexError, TypeError):
            continue
        if None in (o, c, h, l) or (v is None and not close_only) or day>today or min(o,c)<=0 or not 0<l<=min(o,c) or h<max(o,c) or (v is not None and v<0):
            continue
        rows[day] = {'date': day.isoformat(), 'open': o, 'high': h, 'low': l, 'close': c,
                     'vol': v*100 if cn and v is not None else v, 'netFlow': None, 'completed': day<today}
    if not rows:
        raise ValueError('东方财富未返回经过校验的 OHLCV 日线')
    return [rows[day] for day in sorted(rows)], url


def eastmoney_cn_daily(symbol):
    rows, url = eastmoney_daily(eastmoney_secid({'market': 'CN', 'provider_symbol': symbol}), 'Asia/Shanghai', cn=True)
    return rows, url, 'none', '股/份'


def load_eastmoney_stock(meta):
    timezone = 'Asia/Hong_Kong' if meta['market']=='HK' else 'America/New_York'
    rows, url = eastmoney_daily(eastmoney_secid(meta), timezone)
    record = daily_stock_record(meta, rows, url, timezone, '东方财富', '东方财富日线（未复权）', 'none',
                                '东方财富交易所当地日频价格；当日日线未确认，成交量保留供应商原始单位。')
    record['volume_unit'] = '供应商原始单位（仅计算量比）'
    return record


def load_eastmoney_global(asset):
    symbol, name, ticker, _, kind, _ = asset
    if symbol.startswith('us') and kind == 'equity':
        meta = {'market': 'US', 'symbol': ticker, 'code': 'US.'+ticker}
        secid, timezone = eastmoney_secid(meta), 'America/New_York'
    else:
        aliases = {
            'A50': ('富时中国A50', '富时中国A50指数'), 'CNH': ('美元兑离岸人民币', '美元/离岸人民币'),
            'DXY': ('美元指数',), 'DJI': ('道琼斯工业指数', '道琼斯工业平均指数'),
            'IXIC': ('纳斯达克综合指数', '纳斯达克综指'), 'SOX': ('费城半导体指数',),
            'N225': ('日经225', '日经225指数'), 'KOSPI': ('韩国综合指数',),
            'hkHSTECH': ('恒生科技', '恒生科技指数'), 'usVIX': ('VIX波动率指数', '恐慌指数'),
        }
        if symbol not in aliases:
            return unsupported_quote('东方财富暂无该品种已核验的同一价格口径映射')
        candidates = []
        for row in eastmoney_search_rows('USDCNH' if symbol=='CNH' else aliases[symbol][0]):
            if not isinstance(row, dict) or row.get('Name') not in aliases[symbol]:
                continue
            if symbol=='CNH':
                valid = row.get('Classify')=='FOREX' and row.get('Code')=='USDCNH'
            else:
                valid = row.get('SecurityTypeName')=='指数' and row.get('Classify') in ('UniversalIndex', 'Index')
            secid = str(row.get('QuoteID') or '')
            if valid and re.fullmatch(r'\d{1,3}\.[A-Za-z0-9.-]{1,20}', secid):
                candidates.append(secid)
        if len(set(candidates)) != 1:
            return unsupported_quote('东方财富未唯一核验同一标的，拒绝使用 ETF、期货、其他指数或反向汇率替代')
        secid = candidates[0]
        timezone = {'CNH':'Asia/Shanghai','A50':'Asia/Shanghai','N225':'Asia/Tokyo',
                    'KOSPI':'Asia/Seoul','hkHSTECH':'Asia/Hong_Kong'}.get(symbol,'America/New_York')
    daily, url = eastmoney_daily(secid, timezone, close_only=True)
    return {'history':[{'date':row['date'],'close':row['close']} for row in daily], 'timestamp':daily[-1]['date'],
            'timezone':timezone, 'provisional':not daily[-1]['completed']}, url


def load_futu_cn_quotes(symbols, failures):
    quotes = {}
    for start in range(0, len(symbols), 20):
        batch = symbols[start:start+20]
        try:
            data = futu_json(FUTU_API+'/snapshot', data={'code_list': [s[:2].upper()+'.'+s[2:] for s in batch]}).get('data') or {}
        except QuoteUnavailable:
            raise
        except QUOTE_READ_ERRORS as exc:
            failures.append(str(exc)[:140])
            continue
        for row in data.get('snapshot_list') or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get('code', ''))
            symbol = ticker.replace('.', '').lower()
            if symbol not in batch:
                continue
            stamp = row.get('update_time') or row.get('update_timestamp')
            if isinstance(stamp,str) and len(stamp)<16 and not re.fullmatch(r'\d{10,13}',stamp):
                continue
            instant = public_time(stamp)
            price = number(row.get('last_price'))
            if instant is None or price is None or price<=0:
                continue
            volume = number(row.get('volume'))
            precision = row.get('volume_precision', data.get('volume_precision', 0))
            volume = volume/10**precision if volume is not None and type(precision) is int and 0<=precision<=12 else None
            quotes[symbol] = {'code': symbol[2:], 'name': row.get('sc_name') or row.get('name'), 'price': price,
                              'open': number(row.get('open_price')), 'high': number(row.get('high_price')), 'low': number(row.get('low_price')),
                              'volume': volume, 'amount': number(row.get('turnover')), 'previous_close': number(row.get('prev_close_price')),
                              'turnover': number(row.get('turnover_rate')), 'timestamp': instant.isoformat(), 'date': instant.date().isoformat(),
                              'source': '富途行情', 'volume_basis': '股/份', 'qualified_code': ticker}
    return quotes


def futu_cn_daily(symbol):
    rows, url = futu_daily(symbol[:2].upper()+'.'+symbol[2:], 'Asia/Shanghai', adjusted=False)
    return rows, url, 'none', '股/份'


def is_cn_etf(symbol, meta=None):
    # Include on-exchange fund families so new ETF codes inherit the same policy.
    return (meta or {}).get('type') == 'etf' or symbol.startswith(('sh5', 'sz15', 'sz16'))


def load_cn_quotes(symbols, *, issues=None, minimum=None, require_bar=False):
    sources = quote_routes('CN', {
        '腾讯': lambda pending, _: parse_quotes(fetch(QUOTE + ','.join(pending))),
        '东方财富': load_eastmoney_quotes, '平安证券': load_pingan_quotes, '同花顺 iFinD': load_ifind_quotes,
        '富途': load_futu_cn_quotes,
        '新浪': lambda pending, _: parse_sina_quotes(fetch(SINA_QUOTE + ','.join(pending))),
    })
    return load_cn_quote_group(symbols, [(quote_source_label(p, '报价'), load) for p, load in sources],
                               issues=issues, minimum=minimum, require_bar=require_bar)


def load_cn_quote_group(symbols, sources, *, issues=None, minimum=None, require_bar=False):
    remaining = dict.fromkeys(symbols)
    minimum = minimum or {}
    quotes, errors, price_only = {}, {}, {}
    for name, load in sources:
        if not remaining:
            break
        started = time.monotonic()
        requested = len(remaining)
        failures = []
        unavailable = False
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
        except QUOTE_READ_ERRORS as exc:
            failures.append(str(exc)[:165])
            unavailable = isinstance(exc, QuoteUnavailable)
        accepted = requested - len(remaining)
        status = 'skipped' if unavailable else 'partial' if accepted and failures else 'ok' if accepted else 'failed'
        trace_quote(name, status, f'有效标的 {accepted}/{requested}' + ('；' + '；'.join(failures) if failures else ''),
                    elapsed=round((time.monotonic()-started)*1000))
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
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
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
    def public_daily(provider):
        url = (SINA + '?' + urllib.parse.urlencode({'symbol': symbol, 'scale': 240, 'ma': 'no', 'datalen': 640})
               if provider == '新浪日线' else TENCENT + '?' + urllib.parse.urlencode({'param': symbol + ',day,,,640,'}))
        raw = fetch(url)
        rows = parse_sina(raw) if provider == '新浪日线' else parse_tencent(raw, symbol)
        return rows, url, 'none', '股/份' if provider == '新浪日线' else '供应商原始单位（仅计算量比）'
    sources = [(quote_source_label(p, '日线'), load) for p, load in quote_routes('CN', {
        '腾讯': lambda: public_daily('腾讯日线'), '东方财富': lambda: eastmoney_cn_daily(symbol),
        '平安证券': lambda: pingan_daily(symbol), '同花顺 iFinD': lambda: ifind_daily(symbol),
        '富途': lambda: futu_cn_daily(symbol), '新浪': lambda: public_daily('新浪日线'),
    })]
    for provider, load in sources:
        started = time.monotonic()
        try:
            rows, url, adjustment, unit = load()
            bars, skipped = clean_bars(rows, listed, quote)
            if (previous or {}).get('as_of', '') > bars[-1]['date']:
                raise ValueError('新日线早于缓存日期')
            required = min(640, len((previous or {}).get('bars', [])))
            if len(bars) < required:
                raise ValueError(f'仅返回 {len(bars)} 根日线，少于已有研究样本所需 {required} 根，继续下一来源')
            # Tencent native K-line volume has board-specific units. Keep them explicit;
            # only ratios use this field. No absolute share-volume calculations are exposed.
            quote_source = quote.get('source', '腾讯公开行情')
            basis = '前复权' if adjustment == 'qfq' else '未复权'
            trace_quote(provider, 'ok', f'{len(bars)} 根有效日线，最新 {bars[-1]["date"]}',
                        elapsed=round((time.monotonic()-started)*1000))
            return code, {'source': f'{provider}（{basis}）+ {quote_source}名称核验', 'as_of': bars[-1]['date'],
                          'adjustment': adjustment, 'completed': bars[-1]['completed'], 'bars': bars,
                          'fundamentals': None, 'fetched_at': now(), 'latest_quote': quote,
                          'volume_unit': unit, 'provider_url': url, 'skipped_rows': skipped,
                          'history_checked_at': now(), 'history_provider': provider.removesuffix('日线').strip(),
                          'fallback_errors': errors}
        except Exception as exc:
            errors.append(provider + ': ' + str(exc)[:160])
            trace_quote(provider, 'skipped' if isinstance(exc, QuoteUnavailable) else 'failed', str(exc),
                        elapsed=round((time.monotonic()-started)*1000))
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
    if previous and any(previous.get(k) != record.get(k) for k in ('history_provider', 'adjustment', 'volume_unit')):
        previous = None
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


# Explicit provider symbols preserve the underlying instrument and quotation basis.
FUTU_EXTERNAL = {
    'hkHSTECH': ('HK.800700', 'Asia/Hong_Kong'), 'N225': ('JP.N225', 'Asia/Tokyo'),
    'BTC': ('CC.BTCUSD', 'UTC'), 'usVIX': ('US.VIX', 'America/New_York'),
    'DJI': ('US..DJI', 'America/New_York'), 'IXIC': ('US..IXIC', 'America/New_York'),
    'SOX': ('US..SOX', 'America/New_York'),
}
MASSIVE_EXTERNAL = {
    'SOX': 'I:SOX', 'BTC': 'X:BTCUSD', 'CNH': 'C:USDCNH',
    'DJI': 'I:DJI', 'IXIC': 'I:COMP', 'usVIX': 'I:VIX',
}
YAHOO_CHART_HOSTS = ('query1.finance.yahoo.com', 'query2.finance.yahoo.com')


def quote_day(instant, timezone):
    try:
        from zoneinfo import ZoneInfo
        return instant.astimezone(ZoneInfo(timezone)).date()
    except (ImportError, KeyError):
        fixed_hours = {'UTC': 0, 'Asia/Hong_Kong': 8, 'Asia/Shanghai': 8, 'Asia/Tokyo': 9, 'Asia/Seoul': 9}
        if timezone in fixed_hours:
            return instant.astimezone(dt.timezone(dt.timedelta(hours=fixed_hours[timezone]))).date()
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
    today = quote_day(current, timezone)
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
            day = quote_day(dt.datetime.fromtimestamp(item['t'] / 1000, dt.timezone.utc), timezone)
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


def futu_daily(ticker, timezone, *, key=None, days=720, adjusted=True, current=None):
    current = current or dt.datetime.now(dt.timezone.utc)
    today = quote_day(current, timezone)
    start = today - dt.timedelta(days=days)
    endpoint = f'{FUTU_API}/{urllib.parse.quote(ticker, safe="")}/history-kline'
    query = {'start': start, 'end': today, 'ktype': 2, 'autype': int(adjusted), 'num': 370, 'extended_time': 0}
    cursor, rows = {}, {}
    for _ in range(2):
        response = futu_json(endpoint + '?' + urllib.parse.urlencode({**query, **cursor}), key)
        data = response.get('data') or {}
        items = data.get('kline_list')
        precision = data.get('volume_precision', 0)
        scale = 10**precision if type(precision) is int and 0 <= precision <= 12 else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get('code', ticker) != ticker:
                continue
            try:
                date = str(item.get('date', ''))
                if re.fullmatch(r'\d{8}', date):
                    day = dt.date(int(date[:4]), int(date[4:6]), int(date[6:]))
                else:
                    instant = dt.datetime.fromtimestamp(int(item['time_key']) / 1000, dt.timezone.utc)
                    day = quote_day(instant, timezone)
            except (TypeError, ValueError, OverflowError, OSError, KeyError):
                continue
            prices = {field: number(item.get(field)) for field in ('open', 'high', 'low', 'close')}
            if start <= day <= today and prices['close'] is not None and prices['close'] > 0:
                volume = number(item.get('volume'))
                rows[day] = {'date': day.isoformat(), **prices,
                             'vol': volume / scale if scale and volume is not None else None,
                             'completed': day < today}
        pagination = response.get('pagination') or {}
        if not pagination.get('has_more') or days <= 10:
            break
        page_token = pagination.get('next_page_token')
        next_time = data.get('next_time')
        if page_token:
            query.pop('end', None)
        cursor = {'page_token': page_token} if page_token else {'next_time': next_time}
        if not page_token and not next_time:
            break
    if not rows:
        raise ValueError('富途未返回可核验的日线')
    return [rows[day] for day in sorted(rows)], endpoint


def futu_test_quote(ticker, key):
    timezone = 'Asia/Hong_Kong' if ticker.startswith('HK.') else 'America/New_York'
    rows, _ = futu_daily(ticker, timezone, key=key, days=10)
    return {'price': rows[-1]['close'], 'timestamp': rows[-1]['date'], 'detail': '日线数据'}


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
    failures = []
    for host in YAHOO_CHART_HOSTS:
        url = f'https://{host}/v8/finance/chart/{urllib.parse.quote(ticker, safe="")}?{query}'
        try:
            result = json.loads(fetch(url))['chart']['result'][0]
            if result['meta']['symbol'].upper() != ticker.upper():
                raise ValueError('标的身份不匹配')
            parsed = parse(result)
            errors.extend(failures)
            return parsed, url
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
            failures.append(f'{host.split(".")[0]}：{str(exc)[:130]}')
    raise ValueError('；'.join(failures))


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


def load_futu_global(symbol, ticker, stock):
    mapping = (futu_exact_external(symbol) if symbol in ('A50', 'CNH') else
               ('US.' + ticker, 'America/New_York') if stock else FUTU_EXTERNAL.get(symbol))
    if mapping is None:
        return unsupported_quote('富途暂无可核验的同一标的代码')
    qualified, timezone = mapping
    rows, url = futu_daily(qualified, timezone, adjusted=stock)
    last = rows[-1]
    return {'history': [{'date': b['date'], 'close': b['close']} for b in rows],
            'timestamp': last['date'], 'timezone': timezone, 'provisional': not last['completed']}, url


def futu_exact_external(symbol):
    keyword = '富时中国A50指数' if symbol == 'A50' else 'USDCNH'
    response = futu_json(FUTU_API + '/search-quote?' + urllib.parse.urlencode({'keyword': keyword, 'page_size': 30}))
    matches = set()
    for row in (response.get('data') or {}).get('items') or []:
        code = row.get('code', '')
        name = re.sub(r'\s+', '', str(row.get('sc_name') or row.get('name') or '')).upper()
        kind = str(row.get('stock_type') or row.get('sec_type') or '').upper()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}', code):
            continue
        if symbol == 'A50':
            valid = name in ('富时中国A50指数'.upper(), '富時中國A50指數'.upper(), 'FTSECHINAA50INDEX', 'FTSECHINAA50')
            valid = valid and kind == 'IDX'
        else:
            valid = (code.upper().split('.')[-1] == 'USDCNH'
                     and name in ('美元/离岸人民币', '美元/離岸人民幣', 'USD/CNH', 'USDCNH')
                     and kind == 'FOREX')
        if valid:
            matches.add(code)
    if len(matches) != 1:
        raise QuoteUnavailable(f'富途搜索未唯一核验 {keyword} 的同一标的代码，不采用期货、ETF或其他汇率替代')
    return matches.pop(), 'Asia/Shanghai'


def load_global_asset(asset, not_before=None):
    symbol, name, ticker, unit, kind, weight = asset
    yahoo_link = 'https://finance.yahoo.com/quote/' + urllib.parse.quote(ticker, safe='') + '/'
    stock = symbol.startswith('us') and kind == 'equity'
    row = {'symbol': symbol, 'name': name, 'ticker': ticker, 'unit': unit, 'kind': kind, 'weight': RISK_WEIGHTS.get(symbol, weight),
           'url': yahoo_link, 'fetched_at': now()}
    errors = []
    routes = quote_routes('FOREIGN', {
        '富途': lambda: load_futu_global(symbol, ticker, stock),
        'Yahoo Finance': lambda: read_yahoo_chart(ticker, 'range=2y&interval=1d', lambda data: parse_yahoo_global(data, kind), errors),
        '腾讯': lambda: load_tencent_global(symbol, ticker, stock),
        '东方财富': lambda: load_eastmoney_global(asset),
        '同花顺 iFinD': lambda: load_ifind_global(symbol, ticker, stock),
        '新浪': lambda: load_sina_global(symbol, ticker, stock),
    })
    try:
        provider, (record, url) = first_quote_source(
            routes, errors, date_of=lambda result: result[0]['history'][-1]['date'], not_before=not_before)
    except ValueError as exc:
        return {**row, 'error': str(exc), 'price': None, 'history': [], 'returns': external_changes([], kind), 'stale': True}
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


def load_external(previous=()):
    scope = QUOTE_TRACE.get()
    dates = {row['symbol']: row.get('history_as_of') for row in previous}

    def load(asset):
        if scope is None:
            return load_global_asset(asset, dates.get(asset[0]))
        record, stage, _ = scope
        return traced_quote_call(record, stage, asset[0], load_global_asset, asset, dates.get(asset[0]))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(load, GLOBAL_ASSETS))


QUOTE_PROVIDER_NAMES = {'腾讯公开行情': '腾讯', '新浪公开行情': '新浪', '同花顺 iFinD Level 1': '同花顺 iFinD',
                        '平安证券行情': '平安证券', '东方财富行情': '东方财富', '富途行情': '富途'}


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


def load_gpr_history():
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


def clean_public_text(value, limit=1200):
    text = str(value or '')
    text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), text)
    for _ in range(2):
        text = html.unescape(text)
    text = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', text, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', text)).strip()[:limit]


def public_source_url(value):
    value = str(value or '').replace('\\u0026', '&')
    try:
        parts = urllib.parse.urlsplit(value)
        if parts.scheme in ('http', 'https') and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def public_time(value):
    try:
        if isinstance(value, (int, float)) or isinstance(value, str) and re.fullmatch(r'\d{10,13}', value):
            stamp = float(value)
            instant = dt.datetime.fromtimestamp(stamp / 1000 if stamp >= 10**12 else stamp, TZ)
        else:
            instant = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            instant = instant.astimezone(TZ) if instant.tzinfo else instant.replace(tzinfo=TZ)
        return instant if instant <= dt.datetime.now(TZ) else None
    except (ValueError, TypeError, OSError, OverflowError):
        return None


BRIEFING_TOPICS = {
    'politics': r'出口|关税|制裁|监管|禁令|政策|许可|贸易|export|tariff|sanction|ban|regulat',
    'war': r'战争|冲突|空袭|导弹|停火|红海|航运|封锁|袭击|战事|war\b|conflict|strike|ceasefire|red sea|blockade',
}
BRIEFING_WEIGHTS = {'news': 40, 'community': 25, 'gap': 35}


def futu_public_items(endpoint, keyword, size, max_age):
    params = {'keyword': keyword, 'size': size}
    news = endpoint == 'news_search'
    if news:
        params.update(news_type=1, lang='zh-CN', sort_type=2)
    url = 'https://ai-news-search.futunn.com/' + endpoint + '?' + urllib.parse.urlencode(params)
    payload = json.loads(fetch(url, user_agent=('futunn-news-search/0.0.2 (Skill)' if news else
                                               'futu-comment-sentiment/0.0.2 (Skill)')))
    if not isinstance(payload, dict) or payload.get('code') not in (0, '0'):
        raise ValueError('富途公开资讯服务返回错误，未取得可用样本')
    rows = payload.get('data')
    if not isinstance(rows, list):
        raise ValueError('富途公开资讯返回格式不匹配')
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        instant = public_time(row.get('publish_time'))
        if instant is None or (dt.datetime.now(TZ) - instant).total_seconds() > max_age * 86400:
            continue
        title, body = clean_public_text(row.get('title'), 250), clean_public_text(row.get('desc'))
        link = public_source_url(row.get('url'))
        if not title and not body or news and not link:
            continue
        output.append({'title': title, 'text': body, 'url': link, 'published_at': instant.isoformat(),
                       'symbol': keyword, 'source': '富途新闻' if news else '富途社区'})
    return sorted(output, key=lambda row: row['published_at'], reverse=True)


def news_cue(item, kind):
    text = item['title'] + ' ' + item['text']
    if not re.search(BRIEFING_TOPICS[kind], text, re.I):
        return None
    if re.search(r'辟谣|假消息|未经证实|否认.*(?:袭击|制裁|封锁)|未发生|并无', text):
        return None
    easing = (r'解除制裁|取消关税|放宽出口限制|出口许可获批' if kind == 'politics' else
              r'达成停火|停火协议生效|宣布停火|恢复通航|解除封锁')
    escalation = (r'新增制裁|扩大制裁|实施.*禁令|禁止.*出口|上调.*关税|吊销.*许可' if kind == 'politics' else
                  r'发动.*袭击|遭到.*袭击|导弹袭击|空袭|实施封锁|关闭.*海峡|停火.*破裂')
    if re.search(easing, text, re.I) and not re.search(escalation, text, re.I):
        return 25, '缓和线索'
    for match in re.finditer(escalation, text, re.I):
        if not re.search(r'未|否认|不会|没有|避免|取消|解除', text[max(0, match.start()-8):match.start()]):
            return 80, '升级措辞线索'
    return 45, '政策议题线索' if kind == 'politics' else '冲突议题线索'


def briefing_news(kind):
    queries = ['NVDA 出口管制', '半导体 关税 制裁'] if kind == 'politics' else ['原油 航运 冲突', 'ZIM 红海']
    rows, errors, seen = [], [], set()
    for query in queries:
        try:
            result = futu_public_items('news_search', query, 10, 7)
        except QUOTE_READ_ERRORS as exc:
            errors.append(str(exc)[:150])
            continue
        for row in result:
            cue = news_cue(row, kind)
            key = re.sub(r'\W', '', row['title']).casefold()
            if cue is not None and key and key not in seen:
                seen.add(key)
                rows.append({**row, 'cue_score': cue[0], 'cue': cue[1]})
    rows.sort(key=lambda row: row['published_at'], reverse=True)
    rows = rows[:10]
    # Headline clues have at most half coverage. Counts affect sample adequacy, not event severity.
    coverage = min(1, len(rows)/3) * .5
    return {'items': rows, 'score': sum(r['cue_score'] for r in rows)/len(rows) if rows else None,
            'coverage': coverage, 'as_of': min((row['published_at'][:10] for row in rows), default=None),
            'reason': '仅按文本显式措辞标记线索：缓和25、议题45、升级80；新闻标题或摘要未独立核验，最多计半权重。非事件真伪或严重程度鉴定。',
            'errors': errors}


def comment_label(text):
    not_bull = re.search(r'不看好|不看多|不会上涨|not bullish', text, re.I)
    not_bear = re.search(r'不看空|不会下跌|没有风险|not bearish', text, re.I)
    text = re.sub(r'不看好|不看多|不会上涨|not bullish|不看空|不会下跌|没有风险|not bearish', '', text, flags=re.I)
    positive = re.search(r'看好|看多|上涨|反弹|突破|增长|订单改善|利好|bullish|rebound|upside', text, re.I)
    negative = re.search(r'看空|下跌|暴跌|恶化|风险|利空|恐慌|需求下滑|bearish|downside|weak demand', text, re.I)
    positive, negative = bool(positive or not_bear), bool(negative or not_bull)
    return 'bullish' if positive and not negative else 'bearish' if negative and not positive else 'neutral'


def briefing_community(kind):
    symbols = ['NVDA', 'SOXX'] if kind == 'politics' else ['XLE', 'ZIM']
    output, all_posts, seen, errors = [], [], set(), []
    windows = []
    for symbol in symbols:
        try:
            raw = futu_public_items('stock_feed', symbol, 30, 3)
        except QUOTE_READ_ERRORS as exc:
            errors.append(symbol + '：' + str(exc)[:140])
            output.append({'symbol': symbol, 'status': 'error', 'post_count': 0})
            continue
        windows.extend(row['published_at'] for row in raw)
        posts = []
        for row in raw:
            text = (row['title'] + ' ' + row['text']).strip()
            text = row['title'] if row['title'] == row['text'] else text
            key = re.sub(r'\W', '', text).casefold()
            if (len(key) < 16 or key in seen or re.search(r'加群|邀请码|返佣|私信.*领取|免费荐股', text)
                    or not re.search(BRIEFING_TOPICS[kind], text, re.I)):
                continue
            seen.add(key)
            posts.append({**row, 'opinion': text[:180], 'label': comment_label(text)})
        counts = {label: sum(r['label'] == label for r in posts) for label in ('bullish', 'bearish', 'neutral')}
        output.append({'symbol': symbol, 'status': 'ok' if posts else 'empty', 'evidence_limited': len(posts)<5, 'post_count': len(posts),
                       **{label + '_pct': count/len(posts)*100 if posts else None for label, count in counts.items()}})
        all_posts.extend(posts)
    counts = {label: sum(r['label'] == label for r in all_posts) for label in ('bullish', 'bearish', 'neutral')}
    directional = counts['bullish'] + counts['bearish']
    enough = len(all_posts) >= 5 and directional >= 3
    as_of = min((r['published_at'][:10] for r in all_posts), default=None)
    pct = {key: counts[key]/len(all_posts)*100 if all_posts else None for key in counts}
    label = ('mixed' if all_posts and min(pct['bullish'],pct['bearish'])>=25 and abs(pct['bullish']-pct['bearish'])<15 else
             'bullish' if counts['bullish']>max(counts['bearish'],counts['neutral']) else
             'bearish' if counts['bearish']>max(counts['bullish'],counts['neutral']) else 'neutral')
    group = {'label': label, 'post_count': len(all_posts), **{key+'_pct': f'{v:.1f}%' if v is not None else None for key,v in pct.items()},
             'summary': '有效主题样本不足，不能推断整体共识' if not enough else
                        '有效主题样本中，看多、看空与中性分别为 '+ ' / '.join(f'{pct[k]:.1f}%' for k in ('bullish','bearish','neutral'))}
    return {'request': {'symbol_list': symbols, 'lang': 'zh-CN', 'size_per_symbol': 30}, 'generated_at': now(), 'mode': 'multi',
            'group': group, 'symbols': output, 'post_count': len(all_posts),
            'top_opinions': sorted(all_posts, key=lambda r:r['published_at'], reverse=True)[:3],
            'counts': counts, 'time_range': {'earliest': min(windows), 'latest': max(windows)} if windows else None,
            'score': counts['bearish']/len(all_posts)*100 if enough else None,
            'coverage': min(1, len(all_posts)/10) if enough else 0, 'as_of': as_of,
            'reason': '主题相关讨论去重、去广告后，按公开词规则分类；至少5条有效样本且3条有方向。看空占全部有效样本的比例是情绪压力，不是冲突概率。',
            'errors': errors}


def briefing_gap(instruments, catalog):
    today = dt.datetime.now(TZ).date()
    def closed(record):
        return sorted((row for row in record.get('bars', []) if row.get('completed') is True
                       and row.get('date', '') <= today.isoformat() and (number(row.get('close')) or 0) > 0), key=lambda row: row['date'])
    benchmark = closed(instruments.get('510300', {}))
    rows, eligible = [], 0
    scope = [m for m in catalog if m.get('market') == 'CN' and m.get('type') == 'stock' and m.get('watch')]
    for meta in scope:
        record = instruments.get(meta['code'], {})
        bars = closed(record)
        if len(bars) < 121 or (today - dt.date.fromisoformat(bars[-1]['date'])).days > 7:
            continue
        eligible += 1
        ret120 = (bars[-1]['close']/bars[-121]['close']-1)*100
        ret20 = (bars[-1]['close']/bars[-21]['close']-1)*100
        relative = (ret20 - (benchmark[-1]['close']/benchmark[-21]['close']-1)*100
                    if len(benchmark)>=21 and benchmark[-1]['date']==bars[-1]['date'] else None)
        pressure = ret120 <= -20 or relative is not None and relative <= -20
        f = record.get('fundamentals') or {}
        published = public_time(str(f.get('notice_date') or f.get('period') or ''))
        fresh = published is not None and (dt.datetime.now(TZ)-published).days<=220
        fields = ('profit', 'deductProfit', 'operatingCash', 'revenueGrowth')
        complete = fresh and all(number(f.get(k)) is not None for k in fields)
        health = complete and all(f[k]>0 for k in fields)
        label = ('价格与经营分化，问题性质待核实' if pressure and health else
                 '价格与经营同时承压，不能认定错杀' if pressure and complete else
                 '价格承压，经营证据不足' if pressure else '未触发价格压力筛查')
        rows.append({'code': meta['code'], 'name': meta['name'], 'ret120': ret120, 'relative20': relative,
                     'pressure': pressure, 'financial_health': health if complete else None, 'classification': label,
                     'date': bars[-1]['date'], 'financial_date': f.get('notice_date') or f.get('period')})
    return {'items': rows, 'sample_count': eligible, 'scope_count': len(scope),
            'score': sum(r['pressure'] for r in rows)/eligible*100 if eligible>=3 else None,
            'coverage': eligible/len(scope) if eligible>=3 and scope else 0, 'as_of': min((r['date'] for r in rows), default=None),
            'reason': '120日跌幅≥20%或20日落后沪深300 ETF≥20个百分点，压力样本占比映射关注分；财务仅作反证，缺一致预期、审计及竞争力证据，不输出错杀概率或完整偏差总分。'}


def load_geopolitics(instruments=None, catalog=None):
    gap = briefing_gap(instruments or {}, catalog or [])
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(load, kind): (kind, source) for kind in ('politics', 'war')
                for source, load in [('news', briefing_news), ('community', briefing_community)]}
        for future in concurrent.futures.as_completed(jobs):
            kind, source = jobs[future]
            try:
                results[kind, source] = future.result()
            except QUOTE_READ_ERRORS as exc:
                results[kind, source] = {'score': None, 'coverage': 0, 'as_of': None, 'reason': str(exc)[:160], 'errors': [type(exc).__name__]}
    output = {'schema_version': 2, 'method': 'public-evidence-v2', 'as_of': dt.datetime.now(TZ).date().isoformat(),
              'fetched_at': now(), 'source': '富途新闻 + 富途社区情绪 + 市场情绪偏离筛查', 'items': []}
    labels = {'news': '富途新闻', 'community': '富途社区情绪', 'gap': '市场情绪偏离筛查'}
    for kind, name in [('politics', '政治 / 地缘风险'), ('war', '战争 / 冲突风险')]:
        sources = {'news': results[kind, 'news'], 'community': results[kind, 'community'], 'gap': gap}
        parts = [{'id': key, 'name': labels[key], 'weight': weight, 'score': sources[key]['score'],
                  'coverage': sources[key]['coverage'], 'as_of': sources[key]['as_of'],
                  'max_age_days': 3 if key=='community' else 7, 'reason': sources[key].get('reason', ''),
                  'errors': sources[key].get('errors', [])} for key, weight in BRIEFING_WEIGHTS.items()]
        known = sum(p['weight']*p['coverage'] for p in parts if p['score'] is not None)
        weighted = sum(p['weight']*p['coverage']*p['score'] for p in parts if p['score'] is not None)
        points = []
        news, community = sources['news'], sources['community']
        headlines = news.get('items', [])
        points.append(f'本轮近7日取得 {len(headlines)} 条相关新闻线索；标题措辞用于关注筛查，政策适用范围或战况仍需核对原文。')
        if headlines:
            latest = headlines[0]
            points.append(f'最新新闻线索：{latest["title"]}（{latest["published_at"][:10]}，{latest["cue"]}）；该条报道不等于事件已获独立确认。')
        points.append(f'近3日主题相关社区有效样本 {community.get("post_count",0)} 条；看多、看空与中性分布反映讨论样本，不代表全市场。')
        if community.get('post_count'):
            counts = community['counts']
            points.append(f'有效讨论中看多 {counts["bullish"]} 条、看空 {counts["bearish"]} 条、中性或分歧 {counts["neutral"]} 条；观点须结合具体理由核实。')
        points.append(f'价格与基本面对照覆盖 {gap["sample_count"]}/{gap["scope_count"]} 只A股股票，{sum(r["pressure"] for r in gap["items"])} 只触发压力筛查；不把下跌归因于单一政策或冲突。')
        divergent = [r['name'] for r in gap['items'] if r['pressure'] and r['financial_health'] is True]
        if divergent:
            points.append('价格与经营分化样本：' + '、'.join(divergent[:5]) + '；仍需排除结构性问题并等待催化剂验证。')
        score = weighted/known if known else None
        conclusion = ('证据覆盖不足，暂不输出点分；先核对新闻与真实经营影响' if known<60 else
                      '多项文本与市场线索提示高关注，优先核对升级范围与传导证据' if score>=60 else
                      '当前有效样本的压力相对有限，仍需持续验证政策或冲突变化')
        output['items'].append({'kind': kind, 'name': name, 'weight': 10, 'score': score, 'coverage': known/100,
                                'low': weighted/100, 'high': weighted/100+100-known, 'as_of': output['as_of'],
                                'conclusion': conclusion, 'core_points': points, 'components': parts,
                                'news': headlines, 'community': community, 'gap': gap,
                                'transmission': ('出口许可 → 海外收入 / 订单 → 盈利与估值；逐项核对实际适用范围' if kind=='politics' else
                                                 '能源 / 航运 / 保险 → 成本与交付 → 现金流；需核对运价、路线与经营披露'),
                                'counterpoints': ['新闻与社区文本不是官方确认，关键词分类可能误读否定、讽刺和语境。',
                                                  '缓和线索、经营现金流改善与实际订单兑现可构成反证；证据不足时问题性质保持不确定。',
                                                  '一致预期、审计与竞争力证据未齐全，不能把财务筛查通过等同于错杀或必然修复。']})
    return output


# Market-qualified IDs prevent collisions such as HK.00700 versus US.00700.
BENCHMARK_CODES = {'510300', '159338', '510500', '512100', '159915', '588080'}
OPTION_UNDERLYING_CODES = ('510050', '510300', '159919', '510500', '588000', '588080', '159901', '159915', '159922')
BASE_WATCH_CODES = {'002281', '601869', '688825', '688981', '300033', '300390', '515980'} | BENCHMARK_CODES | set(OPTION_UNDERLYING_CODES)
WATCHLIST_DEFAULTS_VERSION = 2
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
                     'search_source': '腾讯证券搜索', 'watch': False, 'benchmark': False,
                     **({'tencent_symbol': 'us' + raw_symbol.upper()} if m == 'US' else {})})
    return rows


def search_tencent_securities(query, market):
    if market == 'US' and re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,14}', query):
        query = query.replace('-', '.')
    url = 'https://smartbox.gtimg.cn/s3/?' + urllib.parse.urlencode({'q': query, 't': 'all'})
    return [row for row in parse_security_search(fetch(url), market) if market!='ALL' or row['market']!='CN']


def ifind_security(row):
    code = str(field_value(row, 'thscode', 'thsCode', 'code', 'symbol', '证券代码', '同花顺代码') or '').upper()
    if re.fullmatch(r'\d{1,5}\.HK', code):
        symbol, market = code.split('.')[0].zfill(5), 'HK'
    elif code.startswith('US.'):
        symbol = code[3:]
        market = 'US'
    elif re.search(r'\.(?:US|OQ|O|N|A|P)$', code):
        symbol = code.rsplit('.', 1)[0]
        market = 'US'
    elif row.get('market') == 'US':
        symbol = code
        market = 'US'
    else:
        return None
    kind = str(field_value(row, 'type', 'security_type', '证券类型') or '').upper()
    kind = {'STOCK': 'stock', 'EQUITY': 'stock', '股票': 'stock', 'ETF': 'etf'}.get(kind)
    currency = field_value(row, 'currency', '币种', '交易币种')
    if currency not in (('HKD','港元','港币') if market=='HK' else ('USD', '美元')):
        return None
    return foreign_security(market, symbol, field_value(row, 'name', '证券简称', '证券名称'), kind, '同花顺 iFinD')


def search_ifind_securities(query, market='US'):
    if market=='CN':
        if not re.fullmatch(r'\d{6}', query):
            return unsupported_quote('同花顺 A股证券核验当前支持精确六位代码')
        symbol = cn_provider_symbol(query)
        failures = []
        quote = load_ifind_quotes([symbol], failures).get(symbol)
        if not quote or not quote.get('name'):
            raise ValueError('同花顺未返回同一 A股证券身份；'+'；'.join(failures))
        return [cn_security_meta(symbol, quote['name'], '同花顺 iFinD')]
    result = ifind_tool('global_stock', 'global_stock_profile', {
        'query': f'{"港股" if market=="HK" else "港美股" if market=="ALL" else "美股"} {query} 的证券代码、证券简称、证券类型（股票或ETF）和交易币种，返回结构化表格。'})
    return [meta for row in ifind_rows(result) if (meta := ifind_security(row)) and market in ('ALL',meta['market'])]


def cn_provider_symbol(code):
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('需要六位 A股证券代码')
    return ('bj' if code.startswith(('4','8','92')) else 'sh' if code.startswith(('5','6')) else 'sz')+code


def cn_security_meta(symbol, name, provider):
    return {'code':symbol[2:], 'symbol':symbol[2:], 'name':name, 'market':'CN', 'currency':'CNY',
            'type':'etf' if is_cn_etf(symbol) else 'stock', 'provider_symbol':symbol,
            'search_source':provider, 'watch':False, 'benchmark':False}


def search_pingan_securities(query):
    if not re.fullmatch(r'\d{6}', query):
        return unsupported_quote('平安证券行情接口仅支持精确证券代码核验')
    symbol = cn_provider_symbol(query)
    data = pingan_json(PINGAN_QUOTE, {'codes':[symbol.upper()]})
    for row in data['items']:
        if isinstance(row,dict) and row.get('code')==symbol.upper() and row.get('name') and (number(row.get('price')) or 0)>0:
            return [cn_security_meta(symbol,row['name'],'平安证券')]
    raise ValueError('平安证券未返回同一股票或 ETF 的有效身份')


def resolve_tencent_security(market, symbol, code):
    rows = search_tencent_securities(symbol, market)
    meta = next((row for row in rows if row['code'] == code), None)
    if meta is None:
        raise ValueError('腾讯未核验该股票或 ETF')
    return meta


def resolve_ifind_security(symbol, code, market='US'):
    meta = next((row for row in search_ifind_securities(symbol, market) if row['code'] == code), None)
    if meta is None:
        raise ValueError('同花顺未核验该美股或 ETF')
    return meta


def foreign_security(market, symbol, name, kind, provider):
    if market not in ('US', 'HK') or kind not in ('stock', 'etf') or not isinstance(symbol, str):
        return None
    if not isinstance(name, str) or not name.strip() or re.search(r'[<>\x00-\x1f]', name):
        return None
    try:
        code = asset_id(market, symbol.replace('.', '-') if market == 'US' else symbol)
    except ValueError:
        return None
    return {'code': code, 'symbol': code.split('.', 1)[1], 'name': name.strip()[:100],
            'market': market, 'type': kind, 'currency': 'HKD' if market == 'HK' else 'USD',
            'timezone': 'Asia/Hong_Kong' if market == 'HK' else 'America/New_York',
            'provider_symbol': symbol, 'search_source': provider, 'watch': False, 'benchmark': False}


def futu_security(row):
    ticker = row.get('code')
    if not isinstance(ticker, str) or '.' not in ticker or row.get('state') in ('DELISTED', 'EXPIRED'):
        return None
    market, symbol = ticker.split('.', 1)
    kind = {'STOCK': 'stock', 'ETF': 'etf'}.get(str(row.get('stock_type', row.get('sec_type'))).upper())
    if market in ('SH','SZ','BJ') and re.fullmatch(r'\d{6}',symbol) and kind:
        name = row.get('sc_name') or row.get('name')
        if not isinstance(name,str) or not name.strip() or re.search(r'[<>\x00-\x1f]',name):
            return None
        return {**cn_security_meta(market.lower()+symbol,name.strip(),'富途'),'type':kind}
    return foreign_security(market, symbol, row.get('sc_name') or row.get('name'), kind, '富途')


def futu_basic_info(codes):
    result = futu_json(FUTU_API + '/stock-basicinfo', data={'code_list': codes})
    return (result.get('data') or {}).get('basic_list') or []


def search_futu_securities(query, market):
    query = urllib.parse.urlencode({'keyword': query, 'page_size': 30})
    result = futu_json(FUTU_API + '/search-quote?' + query)
    candidates = (result.get('data') or {}).get('items') or []
    parsed, unknown = {}, []
    for row in candidates:
        code = row.get('code')
        if not isinstance(code, str) or code.split('.', 1)[0] not in ('US', 'HK', 'SH', 'SZ', 'BJ'):
            continue
        row_market = 'CN' if code.split('.',1)[0] in ('SH','SZ','BJ') else code.split('.',1)[0]
        if market == 'ALL' and row_market == 'CN':
            continue
        if market != 'ALL' and market != row_market:
            continue
        meta = futu_security(row)
        if meta:
            parsed[meta['code']] = meta
        elif type(row.get('sec_type')) is int:
            unknown.append(code)
    if unknown:
        for row in futu_basic_info(unknown):
            meta = futu_security(row)
            if meta and row.get('code') in unknown:
                parsed[meta['code']] = meta
    return list(parsed.values())


YAHOO_US_EXCHANGES = {'NMS', 'NGM', 'NCM', 'NAS', 'NYQ', 'ASE', 'PCX', 'BTS', 'BATS', 'PNK', 'OQB', 'OQX'}


def search_yahoo_securities(query, market, errors):
    params = urllib.parse.urlencode({'q': query, 'quotesCount': 25, 'newsCount': 0})
    failures = []
    for host in YAHOO_CHART_HOSTS:
        try:
            response = json_get(f'https://{host}/v1/finance/search?{params}')
            results = {}
            for row in response.get('quotes', []):
                ticker, exchange = row.get('symbol', ''), row.get('exchange')
                row_market = 'HK' if exchange == 'HKG' and re.fullmatch(r'\d{1,5}\.HK', ticker) else None
                if exchange in YAHOO_US_EXCHANGES and re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', ticker):
                    row_market = 'US'
                if row_market is None or market not in ('ALL', row_market):
                    continue
                kind = {'EQUITY': 'stock', 'ETF': 'etf'}.get(row.get('quoteType'))
                symbol = ticker.split('.')[0] if row_market == 'HK' else ticker
                meta = foreign_security(row_market, symbol, row.get('longname') or row.get('shortname'), kind, 'Yahoo Finance')
                if meta:
                    meta['provider_symbol'] = ticker
                    results[meta['code']] = meta
            if results:
                errors.extend(failures)
                return list(results.values())
            failures.append(f'{host.split(".")[0]} 未找到匹配的股票或 ETF')
        except QUOTE_READ_ERRORS as exc:
            failures.append(f'{host.split(".")[0]}：{str(exc)[:130]}')
    raise ValueError('；'.join(failures))


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
    rows, errors = [], []
    if market in ('ALL', 'CN'):
        try:
            _, rows = first_quote_source(quote_routes('CN', {
                '腾讯': lambda: search_tencent_securities(query, 'CN'),
                '东方财富': lambda: search_eastmoney_securities(query, 'CN'),
                '平安证券': lambda: search_pingan_securities(query),
                '同花顺 iFinD': lambda: search_ifind_securities(query, 'CN'),
                '富途': lambda: search_futu_securities(query, 'CN'),
                '新浪': lambda: search_sina_securities(query, 'CN'),
            }), errors)
        except ValueError:
            if market == 'CN':
                raise
    if market != 'CN':
        overseas_market = 'HK' if market == 'ALL' and re.fullmatch(r'\d{1,5}(?:\.HK)?', query, re.I) else market
        if not (market == 'ALL' and re.fullmatch(r'\d{6}', query)):
            try:
                _, overseas = first_quote_source(quote_routes('FOREIGN', {
                    '富途': lambda: search_futu_securities(query, overseas_market),
                    'Yahoo Finance': lambda: search_yahoo_securities(query, overseas_market, errors),
                    '腾讯': lambda: search_tencent_securities(query, overseas_market),
                    '东方财富': lambda: search_eastmoney_securities(query, overseas_market),
                    '同花顺 iFinD': lambda: search_ifind_securities(query, overseas_market),
                    '新浪': lambda: search_sina_securities(query, overseas_market),
                }), errors)
                rows.extend(overseas)
            except ValueError:
                if not rows:
                    raise
    if not rows and errors:
        raise ValueError('；'.join(errors))
    if rows:
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


def resolve_futu_security(market, symbol, code):
    ticker = f'{market}.{symbol}'
    codes = [ticker]
    if market == 'US' and '-' in symbol:
        codes.append(ticker.replace('-', '.'))
    for row in futu_basic_info(codes):
        meta = futu_security(row)
        if meta and meta['code'] == code and row.get('code') in codes:
            return meta
    raise ValueError('富途未核验该股票或 ETF')


def resolve_yahoo_security(market, symbol, errors):
    ticker = yahoo_symbol({'market': market, 'symbol': symbol})

    def verify(row):
        meta = row['meta']
        currency = 'HKD' if market == 'HK' else 'USD'
        kind = {'EQUITY': 'stock', 'ETF': 'etf'}.get(meta.get('instrumentType'))
        exchanges = {'HKG'} if market == 'HK' else YAHOO_US_EXCHANGES
        if meta.get('currency') != currency or meta.get('exchangeName') not in exchanges:
            raise ValueError('标的市场或币种不匹配')
        name = meta.get('longName') or meta.get('shortName')
        result = foreign_security(market, symbol, name, kind, 'Yahoo Finance')
        if not result:
            raise ValueError('Yahoo 未核验该股票或 ETF')
        result['provider_symbol'] = ticker
        return result

    result, _ = read_yahoo_chart(ticker, 'range=5d&interval=1d', verify, errors)
    return result


def resolve_massive_security(market, symbol, code):
    if market != 'US':
        return unsupported_quote('Massive 股票接口不覆盖港股')
    ticker = symbol.replace('-', '.')
    url = f'{MASSIVE_API}/v3/reference/tickers/{urllib.parse.quote(ticker, safe="")}'
    meta = massive_security(massive_json(url).get('results'))
    if not meta or meta['code'] != code or meta['provider_symbol'] != ticker:
        raise ValueError('Massive 未核验该股票或 ETF')
    return meta


def resolve_security(market, symbol):
    market = str(market).upper()
    if market == 'US':
        symbol = str(symbol).replace('.', '-')
    code = asset_id(market, symbol)
    if market == 'CN':
        candidates = search_securities(code, market)
        meta = next((dict(row) for row in candidates if row['code'] == code), None)
        if not meta:
            raise ValueError('证券搜索源未确认该标的；请从搜索结果添加')
        quote = load_cn_quotes([meta['provider_symbol']]).get(meta['provider_symbol'])
        if not quote or quote['code'] != code or quote['name'] != meta['name']:
            raise ValueError('证券名称与报价源核验不一致')
    else:
        symbol = code.split('.', 1)[1]
        errors = []
        routes = quote_routes('FOREIGN', {
            '富途': lambda: resolve_futu_security(market, symbol, code),
            'Yahoo Finance': lambda: resolve_yahoo_security(market, symbol, errors),
            '腾讯': lambda: resolve_tencent_security(market, symbol, code),
            '东方财富': lambda: resolve_eastmoney_security(market, symbol, code),
            '同花顺 iFinD': lambda: resolve_ifind_security(symbol, code, market),
            '新浪': lambda: resolve_sina_security(market, symbol, code),
        })
        _, meta = first_quote_source(routes, errors)
        meta['verified_name'] = meta['name']
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
    errors = []

    def yahoo():
        record, url = read_yahoo_chart(yahoo_symbol(meta), 'range=5y&interval=1d&includePrePost=false',
                                       lambda result: parse_international_chart(result, meta), errors)
        return {**record, 'provider_url': url, 'history_provider': 'Yahoo Finance'}

    routes = quote_routes('FOREIGN', {
        '富途': lambda: load_futu_stock(meta), 'Yahoo Finance': yahoo,
        '腾讯': lambda: load_tencent_stock(meta), '东方财富': lambda: load_eastmoney_stock(meta),
        '同花顺 iFinD': lambda: load_ifind_stock(meta), '新浪': lambda: load_sina_stock(meta),
    })
    _, record = first_quote_source(routes, errors, date_of=lambda result: result['as_of'],
                                   not_before=(previous or {}).get('as_of'))
    record['fallback_errors'] = errors
    prior_dates = [b['date'] for b in record['bars'] if b['completed']]
    comparable = previous if (previous or {}).get('history_provider') == record['history_provider'] else None
    # Re-fetching all daily bars repairs the last previously-provisional trading session.
    target = max((b['date'] for b in (comparable or {}).get('bars', []) if b.get('completed') is False and any(x['date'] == b['date'] and x['completed'] for x in record['bars'])), default=None)
    if not target:
        target = prior_dates[-2] if record['completed'] and len(prior_dates)>1 else prior_dates[-1] if prior_dates else None
    if target:
        verify_previous(record, comparable, target)
    record['research_errors'] = {'flows': '该海外数据源未提供与A股可比的历史大单净额',
                                 'announcements': '海外公告原文索引尚未接入，不把缺失视为无风险'}
    for key in ('fundamentals', 'financial_history'):
        if (previous or {}).get(key):
            record[key] = previous[key]
    record['research_fetched_at'] = now()
    return meta['code'], record


def daily_stock_record(meta, daily, url, timezone, provider, source, adjustment, method):
    checked_at = now()
    bars = []
    for row in daily:
        o, h, l, c, v = (row[key] for key in ('open', 'high', 'low', 'close', 'vol'))
        if None in (o, h, l, v) or min(o, c) <= 0 or not 0 < l <= min(o, c) or h < max(o, c) or v < 0:
            continue
        bars.append({**row, 'observed_at': checked_at, 'bar_source': source, 'netFlow': None})
    if not bars:
        raise ValueError(f'{provider} 未返回可核验的 OHLCV 日线')
    last = bars[-1]
    return {'source': source, 'provider_url': url, 'history_provider': provider, 'as_of': last['date'],
            'closed_as_of': max((b['date'] for b in bars if b['completed']), default=None),
            'adjustment': adjustment, 'completed': last['completed'], 'bars': bars, 'volume_unit': '股/份',
            'market': meta['market'], 'currency': meta['currency'], 'timezone': timezone, 'fetched_at': checked_at,
            'history_checked_at': checked_at, 'fundamentals': None,
            'latest_quote': {'price': last['close'], 'code': meta['code'], 'name': meta['name'],
                             'timestamp': last['date'], 'date': last['date'], 'source': source},
            'international_method': method}


def tencent_us_daily(meta=None, index=None):
    if index:
        symbol = {'DJI': 'us.DJI', 'IXIC': 'us.IXIC', 'SOX': 'us.SOX', 'usVIX': 'us.VIX'}[index]
    else:
        symbol = meta.get('tencent_symbol')
        if not symbol:
            symbol = resolve_tencent_security('US', meta['symbol'], meta['code'])['tencent_symbol']
        symbol = 'us' + symbol[2:].upper()
        if not re.fullmatch(r'us[A-Za-z0-9.-]{1,30}', symbol):
            raise ValueError('腾讯美股证券代码无效')
    url = TENCENT_US_KLINE + '?' + urllib.parse.urlencode({'param': symbol + ',day,,,640,qfq'})
    payload = json.loads(fetch(url))
    record = (payload.get('data') or {}).get(symbol) or {}
    quote = (record.get('qt') or {}).get(symbol) or []
    if len(quote) < 36 or quote[35] != 'USD':
        raise ValueError('腾讯缺少可核验的美元证券报价身份')
    normalize = lambda value: re.sub(r'\.(?:oq|n|a|ps|o|p)$', '', value, flags=re.I).replace('.', '-').upper()
    if index:
        expected = symbol[2:]
        matched = str(quote[2]).upper() == expected.upper()
    else:
        matched = normalize(str(quote[2])) == meta['symbol'].upper()
    if not matched:
        raise ValueError('腾讯日线标的身份不匹配')
    adjusted = bool(record.get('qfqday'))
    today = quote_day(dt.datetime.now(dt.timezone.utc), 'America/New_York')
    rows = {}
    for row in record.get('qfqday') or record.get('day') or []:
        try:
            date = dt.date.fromisoformat(row[0])
            o, c, h, l, v = map(number, row[1:6])
        except (ValueError, TypeError, IndexError):
            continue
        if date > today or None in (o, c, h, l, v):
            continue
        if min(o, c) <= 0 or not 0 < l <= min(o, c) or h < max(o, c) or v < 0:
            continue
        rows[date] = {'date': date.isoformat(), 'open': o, 'high': h, 'low': l, 'close': c,
                      'vol': v, 'completed': date < today}
    if not rows:
        raise ValueError('腾讯未返回同一美股标的的有效日线')
    if (today - max(rows)).days > 7:
        raise ValueError('腾讯最近日线距今超过7天，继续尝试下一来源')
    return [rows[d] for d in sorted(rows)], url, 'qfq' if adjusted else 'none', quote[0] == 'delay'


def load_tencent_stock(meta):
    hk = meta['market']=='HK'
    daily, url, adjustment, delayed = tencent_hk_daily(meta) if hk else tencent_us_daily(meta)
    source = ('腾讯港股日线（' if hk else '腾讯美股日线（') + ('前复权' if adjustment == 'qfq' else '未复权') + '）' + (' · 延迟' if delayed else '')
    record = daily_stock_record(meta, daily, url, 'Asia/Hong_Kong' if hk else 'America/New_York', '腾讯', source, adjustment,
                                '腾讯本币常规时段日线，不含盘前盘后；交易所当地当日日线未确认。')
    if hk:
        record['volume_unit'] = '供应商原始单位（仅计算量比）'
    return record


def tencent_hk_daily(meta):
    if not re.fullmatch(r'\d{5}',meta['symbol']):
        raise ValueError('腾讯港股需要五位股票或 ETF 代码')
    symbol = 'hk'+meta['symbol']
    url = TENCENT+'?'+urllib.parse.urlencode({'param':symbol+',day,,,640,qfq'})
    record = (json.loads(fetch(url)).get('data') or {}).get(symbol) or {}
    quote = (record.get('qt') or {}).get(symbol) or []
    if len(quote)<3 or quote[2]!=meta['symbol']:
        raise ValueError('腾讯港股日线缺少同一标的报价身份')
    today = quote_day(dt.datetime.now(dt.timezone.utc),'Asia/Hong_Kong')
    rows = {}
    for row in record.get('qfqday') or record.get('day') or []:
        try:
            date = dt.date.fromisoformat(row[0])
            o,c,h,l,v = map(number,row[1:6])
        except (ValueError,IndexError,TypeError):
            continue
        if date<=today and None not in (o,c,h,l,v) and 0<l<=min(o,c)<=max(o,c)<=h and v>=0:
            rows[date] = {'date':date.isoformat(),'open':o,'close':c,'high':h,'low':l,'vol':v,'completed':date<today}
    if not rows:
        raise ValueError('腾讯未返回有效的港股 OHLCV 日线')
    return [rows[date] for date in sorted(rows)],url,'qfq' if record.get('qfqday') else 'none',quote[0]=='delay'


def load_tencent_global(symbol, ticker, stock):
    if not stock and symbol not in ('DJI','IXIC','SOX','usVIX'):
        return unsupported_quote('腾讯暂无该非股票品种已核验的同一标的日线适配')
    meta = {'code': 'US.' + ticker, 'symbol': ticker} if stock else None
    daily, url, _, delayed = tencent_us_daily(meta, None if stock else symbol)
    return {'history': [{'date': row['date'], 'close': row['close']} for row in daily],
            'timestamp': daily[-1]['date'], 'timezone': 'America/New_York',
            'provisional': not daily[-1]['completed'], 'delayed': delayed}, url


def load_ifind_stock(meta):
    daily, url, adjustment, _ = ifind_daily(meta['symbol'], meta['market'])
    hk = meta['market']=='HK'
    source = ('同花顺 iFinD 港股日线（' if hk else '同花顺 iFinD 美股日线（') + ('前复权' if adjustment == 'qfq' else '未复权') + '）'
    return daily_stock_record(meta, daily, url, 'Asia/Hong_Kong' if hk else 'America/New_York', '同花顺 iFinD', source, adjustment,
                              '同花顺本币日频行情；交易所当地当日数据未确认，不视为实时Level 1。')


def load_ifind_global(symbol, ticker, stock):
    if not stock and symbol not in ('DJI','IXIC','SOX','usVIX'):
        return unsupported_quote('同花顺暂无该非股票品种已核验的同一标的日线适配')
    if not stock:
        names = {
            'DJI': ('道琼斯工业指数', '道琼斯工业平均指数', 'DOWJONESINDUSTRIALAVERAGE'),
            'IXIC': ('纳斯达克综合指数', 'NASDAQCOMPOSITE'),
            'SOX': ('费城半导体指数', 'PHLXSEMICONDUCTORSECTORINDEX'),
            'usVIX': ('VIX波动率指数', 'CBOEVOLATILITYINDEX'),
        }[symbol]
        result = ifind_tool('index', 'index_data', {'query':
            f'美国 {names[0]}（{ticker}）最近640个交易日的每日收盘点数，返回指数名称、指数代码和交易日期的结构化表格；不要ETF或期货代理。'})
        today = quote_day(dt.datetime.now(dt.timezone.utc), 'America/New_York')
        history = {}
        for row in ifind_rows(result):
            name = re.sub(r'\s+', '', str(field_value(row, 'name', '指数名称', '证券简称') or '')).upper()
            if name not in names or str(field_value(row, 'type', '证券类型') or '').upper() in ('ETF', 'FUTURE'):
                continue
            day = str(field_value(row, 'date', 'time', '交易日期', '日期', '时间') or '')[:10]
            try:
                date = dt.date.fromisoformat(day)
            except ValueError:
                continue
            close = number(field_value(row, 'close', '收盘价', '收盘点数'))
            if date <= today and close is not None and close > 0:
                history[day] = {'date': day, 'close': close}
        if not history:
            raise ValueError('同花顺未返回可核验的同一美国指数日线')
        last = max(history)
        return {'history': [history[d] for d in sorted(history)], 'timestamp': last,
                'timezone': 'America/New_York', 'provisional': last == today.isoformat()}, IFIND_ENDPOINT.format('index')
    record = load_ifind_stock({'code': 'US.' + ticker, 'symbol': ticker, 'name': ticker,
                               'market': 'US', 'currency': 'USD'})
    return {'history': [{'date': row['date'], 'close': row['close']} for row in record['bars']],
            'timestamp': record['as_of'], 'timezone': 'America/New_York', 'provisional': not record['completed']}, record['provider_url']


def sina_jsonp(raw):
    text = raw.decode('utf-8')
    match = re.search(r'\bvar\s*=?\s*\((.*)\)\s*;?\s*$', text, re.S)
    if not match:
        raise ValueError('新浪未返回可解析的行情 JSONP')
    result = json.loads(match[1])
    if isinstance(result, dict) and result.get('__ERROR'):
        raise ValueError('新浪该行情接口不支持请求的品种或服务')
    return result


def sina_us_daily(ticker):
    if not re.fullmatch(r'[A-Za-z.][A-Za-z0-9.-]{0,20}', ticker):
        raise ValueError('新浪美股代码无效')
    ticker = ticker.lower().replace('-', '.')
    identity = fetch(SINA_QUOTE + 'gb_' + ticker).decode('gb18030')
    matched = re.search(r'\bhq_str_gb_' + re.escape(ticker) + r'="([^"]*)"', identity)
    cells = matched[1].split(',') if matched else []
    if len(cells) < 26 or not cells[0] or (number(cells[1]) or 0) <= 0:
        raise ValueError('新浪未返回同一美元证券的有效报价身份')
    url = 'https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var/US_MinKService.getDailyK?' + urllib.parse.urlencode({'symbol': ticker})
    items = sina_jsonp(fetch(url))
    if not isinstance(items, list):
        raise ValueError('新浪美股日线为空或格式错误')
    today = quote_day(dt.datetime.now(dt.timezone.utc), 'America/New_York')
    bars = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        try:
            date = dt.date.fromisoformat(str(row.get('d', '')))
        except ValueError:
            continue
        values = {k: number(row.get(key)) for k, key in [('open', 'o'), ('high', 'h'), ('low', 'l'), ('close', 'c'), ('vol', 'v')]}
        if date <= today and all(v is not None for v in values.values()):
            bars[date] = {'date': date.isoformat(), **values, 'completed': date < today}
    if not bars:
        raise ValueError('新浪未返回有效的美股日线')
    return [bars[d] for d in sorted(bars)][-1000:], url


def load_sina_stock(meta):
    if meta['market'] != 'US':
        return unsupported_quote('新浪港股暂无已核验的历史日线适配器，保留已有快照')
    daily, url = sina_us_daily(meta['symbol'])
    return daily_stock_record(meta, daily, url, 'America/New_York', '新浪', '新浪美股日线（供应商价格口径）', 'provider',
                              '新浪美元日频行情；未确认分红与拆股调整口径，不与其他来源拼接。纽约当地当日未确认。')


def load_sina_global(symbol, ticker, stock):
    if stock or symbol in ('DJI', 'IXIC', 'SOX', 'usVIX'):
        daily, url = sina_us_daily(ticker if stock else {'DJI': 'dji', 'IXIC': 'ixic', 'SOX': 'sox', 'usVIX': 'vix'}[symbol])
        # Global close-only cards still require a valid OHLC envelope from Sina.
        valid = [row for row in daily if min(row['open'], row['close']) > 0
                 and 0 < row['low'] <= min(row['open'], row['close'])
                 and row['high'] >= max(row['open'], row['close']) and row['vol'] >= 0]
        timezone = 'America/New_York'
    elif symbol == 'CNH':
        url = 'https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var/NewForexService.getDayKLine?symbol=fx_susdcnh'
        payload = sina_jsonp(fetch(url))
        if not isinstance(payload, str):
            raise ValueError('新浪 USDCNH 日线格式不匹配')
        valid = []
        today = dt.datetime.now(TZ).date()
        for line in payload.split('|'):
            parts = line.split(',')
            try:
                date = dt.date.fromisoformat(parts[0])
                o, l, h, c = map(float, parts[1:5])
            except (ValueError, IndexError):
                continue
            if date <= today and all(math.isfinite(v) for v in (o, l, h, c)) and 0 < l <= min(o, c) <= max(o, c) <= h:
                valid.append({'date': date.isoformat(), 'close': c, 'completed': date < today})
        timezone = 'Asia/Shanghai'
    elif symbol in ('GOLD', 'SILVER'):
        code = 'GC' if symbol == 'GOLD' else 'SI'
        url = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var=/GlobalFuturesService.getGlobalFuturesDailyKLine?' + urllib.parse.urlencode({'symbol': code})
        payload = sina_jsonp(fetch(url))
        if not isinstance(payload, list):
            raise ValueError('新浪贵金属连续期货日线格式不匹配')
        today = quote_day(dt.datetime.now(dt.timezone.utc), 'America/New_York')
        valid = []
        for row in payload:
            try:
                date = dt.date.fromisoformat(row['date'])
            except (ValueError, TypeError, KeyError):
                continue
            close = number(row.get('close'))
            if date <= today and close is not None and close > 0:
                valid.append({'date': date.isoformat(), 'close': close, 'completed': date < today})
        timezone = 'America/New_York'
    else:
        return unsupported_quote('新浪暂无已核验的同一标的日线映射；不以其他指数、ETF、期货或汇率替代')
    if not valid:
        raise ValueError('新浪未返回经过校验的同一标的日线')
    if len({row['date'] for row in valid}) != len(valid):
        raise ValueError('新浪日线日期重复，保留已有快照')
    valid.sort(key=lambda row: row['date'])
    return {'history': [{'date': row['date'], 'close': row['close']} for row in valid][-1000:],
            'timestamp': valid[-1]['date'], 'timezone': timezone, 'provisional': not valid[-1]['completed']}, url


def search_sina_securities(query, market):
    url = 'https://suggest3.sinajs.cn/suggest/key=' + urllib.parse.quote(query, safe='') + '&name=var'
    raw = fetch(url).decode('gb18030')
    match = re.search(r'\bvar\s+var\s*=\s*("(?:\\.|[^"\\])*")', raw)
    if not match:
        raise ValueError('新浪证券搜索格式不匹配')
    output = []
    for entry in json.loads(match[1]).split(';'):
        fields = entry.split(',')
        if len(fields) < 5:
            continue
        category, symbol, name = fields[1:3] + [fields[4]]
        m = {'41': 'US', '31': 'HK', '11': 'CN', '203': 'CN'}.get(category)
        if not m or market not in ('ALL', m) or market=='ALL' and m=='CN':
            continue
        if m == 'CN':
            if not re.fullmatch(r'(?:sh|sz|bj)\d{6}', fields[3]):
                continue
            output.append({'code': symbol, 'symbol': symbol, 'name': name[:100], 'market': 'CN', 'currency': 'CNY',
                           'type': 'etf' if is_cn_etf(fields[3]) else 'stock', 'provider_symbol': fields[3],
                           'search_source': '新浪', 'watch': False, 'benchmark': False})
        else:
            # Categories 31/41 exclude warrant/OTC categories; reject foreign exchange suffixes.
            if m == 'US' and '.' in symbol and not re.fullmatch(r'[A-Za-z]+\.[ABab]', symbol):
                continue
            kind = 'etf' if 'ETF' in name.upper() else 'stock'
            meta = foreign_security(m, symbol, name, kind, '新浪')
            if meta:
                output.append(meta)
    return output


def resolve_sina_security(market, symbol, code):
    meta = next((r for r in search_sina_securities(symbol, market) if r['code'] == code), None)
    if not meta:
        raise ValueError('新浪未核验同一股票或 ETF')
    return meta


def load_futu_stock(meta):
    market, symbol = meta['market'], meta['symbol']
    timezone = 'Asia/Hong_Kong' if market == 'HK' else 'America/New_York'
    ticker = meta['provider_symbol'] if meta.get('search_source') == '富途' else symbol.replace('-', '.')
    daily, url = futu_daily(f'{market}.{ticker}', timezone)
    return daily_stock_record(meta, daily, url, timezone, '富途', '富途日线（前复权）', 'qfq',
                              '富途本币前复权日线，交易所当地当日的日线暂不进入确认回测。')


def load_massive_stock(meta):
    ticker = meta['symbol'].replace('-', '.')
    daily, url, timezone, delayed = massive_daily(ticker)
    source = 'Massive 日线（拆股调整）' + (' · 延迟' if delayed else '')
    return daily_stock_record(meta, daily, url, timezone, 'Massive', source, 'provider',
                              'Massive 美元日线按拆股调整，非总回报。纽约当地当日的日线暂不进入确认回测。')


def load_watch_record(meta, previous=None, quotes=None):
    if meta.get('market', 'CN') != 'CN':
        return load_international_asset(meta, previous)
    symbol = meta['provider_symbol']
    SYMBOLS[meta['code']] = symbol
    quotes = quotes if quotes is not None else load_cn_quotes([symbol], minimum={symbol: meta})
    code, record = load_asset((meta['code'], meta['name'], meta.get('listed')), quotes, previous)
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


# Independent research evidence: never changes prices, historical factors or order signals.
STOCK_RESEARCH_SOURCES = {'pingan': '平安证券资金查询', 'futu_capital': '富途资金异动',
                          'ifind': '同花顺经营与披露', 'futu_news': '富途新闻'}
STOCK_RESEARCH_FIELDS = {
    '行业': ('industry', 'text'), '主营业务': ('business', 'text'), '主要产品': ('products', 'text'),
    '主营构成': ('segments', 'text'), '主营业务收入占比': ('business_share', 'percent'),
    '营收同比': ('revenue_growth', 'percent'), '归母净利同比': ('profit_growth', 'percent'),
    '毛利率': ('gross_margin', 'percent'), '经营活动现金流净额': ('operating_cash', 'amount'),
    '资本开支': ('capex', 'amount'), 'ROIC': ('roic', 'percent'),
    '研发资本化比例': ('rd_capitalization', 'percent'), '股份支付费用': ('sbc', 'amount'),
}


def research_symbol(meta):
    if meta['market'] == 'CN':
        symbol = meta.get('provider_symbol') or cn_provider_symbol(meta['code'])
        if not re.fullmatch(r'(?:sh|sz|bj)\d{6}', symbol):
            raise ValueError('研究证券代码无效')
        return symbol[:2].upper()+'.'+symbol[2:]
    return asset_id(meta['market'], meta['symbol'])


def research_error(exc):
    # Errors must not leak a CLI credential, a response body or a local private-key path.
    if isinstance(exc, QuoteUnavailable):
        return str(exc)[:220]
    if isinstance(exc, urllib.error.HTTPError):
        return 'HTTP '+str(exc.code)+'：行情服务请求失败'
    if isinstance(exc, (OSError, subprocess.TimeoutExpired)):
        return '网络或本机调用失败：'+type(exc).__name__
    code = re.search(r'HTTP\s+\d{3}|错误码\s+-?\d+', str(exc))
    return ('服务调用失败（'+code[0]+'）') if code else '数据校验或服务调用失败：'+type(exc).__name__


def stock_pingan_evidence(meta):
    if meta['market'] != 'CN':
        raise QuoteUnavailable('平安证券资金接口仅用于 A 股股票')
    symbol = research_symbol(meta).replace('.', '')
    url = PINGAN_QUOTE.rsplit('/', 1)[0]+'/capitalFlow'
    data = pingan_json(url, {'codes': [symbol]})
    row = next((r for r in data['items'] if isinstance(r, dict) and r.get('code') == symbol), None)
    if row is None or number(row.get('main_net_inflow')) is None:
        raise ValueError('未返回同一证券的有效资金统计')
    instant = public_time(field_value(row, 'timestamp', 'quote_time', 'time', 'update_time'))
    # This endpoint may omit observation time. Keep that omission, never stamp it as today's flow.
    return {'source': '平安证券', 'url': url, 'as_of': instant.isoformat() if instant else None,
            'currency': 'CNY', 'time_missing': instant is None,
            'metrics': {key: number(row.get(key)) for key in ('main_net_inflow', 'super_large_net_inflow',
                'large_net_inflow', 'main_net_inflow_pct', 'amount')},
            'basis': '供应商超大单+大单分类统计，不代表机构身份；金额单位元。'}


def futu_cli_path():
    executable = shutil.which('futu')
    candidates = [Path.home()/'.futu/bin/futu', Path(os.getenv('LOCALAPPDATA') or str(Path.home()))/'FutuCLI/bin/futu.exe']
    return executable or next((str(p) for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)


def futu_cli_auth_ready(executable):
    try:
        result = subprocess.run([executable,'auth','status','--format','json'],capture_output=True,
                                text=True,encoding='utf-8',timeout=10)
        payload = json.loads(result.stdout) if result.returncode==0 else {}
    except (ValueError,subprocess.TimeoutExpired):
        raise QuoteUnavailable('无法核验富途 CLI 授权状态；请在服务电脑运行 futu auth status --format json') from None
    if not isinstance(payload,dict) or (payload.get('token') or {}).get('status') not in ('logged in','expired'):
        raise QuoteUnavailable('富途 CLI 尚未完成 OAuth 授权；请在服务电脑运行 futu auth login')
    # Discard the status payload. It can contain account metadata and is never cached or returned.


def stock_futu_capital(meta):
    executable = futu_cli_path()
    if not executable:
        raise QuoteUnavailable('未安装富途 CLI；资金异动需新版 futu CLI 及独立 OAuth 登录，网页 AppKey 不替代 CLI 授权')
    futu_cli_auth_ready(executable)
    symbol = research_symbol(meta)
    command = [executable, 'unusual', 'capital-flows', symbol, '--time-range', '7', '--language-id', '0', '--format', 'json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=40)
    except subprocess.TimeoutExpired:
        raise QuoteUnavailable('富途资金异动请求超时（40 秒）') from None
    if result.returncode:
        output = (result.stderr or '').lower()
        if any(cue in output for cue in ('unknown command', 'unknown flag')):
            raise QuoteUnavailable('富途 CLI 版本不支持资金异动，请升级 CLI')
        if any(cue in output for cue in ('auth', 'login', '401', '403', 'permission')):
            raise QuoteUnavailable('富途 CLI 未登录或资金异动权限不足；请在服务电脑运行 futu auth login')
        raise ValueError('富途 CLI 执行失败')
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get('retType') != 0 or payload.get('errCode') != 0 or payload.get('stock_symbol') != symbol:
        raise ValueError('富途资金异动返回错误或证券不匹配')
    raw_content = payload.get('content')
    content = '\n'.join(clean_public_text(line, 2000) for line in raw_content.splitlines())[:12000] if isinstance(raw_content,str) else ''
    if not content:
        raise ValueError('富途资金异动未返回内容；不能视为没有异动')
    today = dt.datetime.now(TZ).date()
    return {'source':'富途资金异动', 'symbol':symbol, 'content':content,
            'time_range':clean_public_text(payload.get('time_range'), 100),
            'requested_range': {'start':(today-dt.timedelta(days=6)).isoformat(),'end':today.isoformat()},
            'basis':'保留供应商资金分布/经纪商、资金流向与卖空原文；没有异动与未取得数据分别展示。'}


def stock_item_relevant(meta, title, text=''):
    content = title+' '+text
    name = str(meta.get('name') or '').strip()
    if len(name) >= 3 and name.casefold() in content.casefold():
        return True
    symbol = meta.get('symbol') or meta['code']
    return bool(re.search(r'(?<![A-Za-z0-9])'+re.escape(symbol)+r'(?![A-Za-z0-9])',content,re.I))


def stock_futu_news(meta):
    keyword = meta['name'] if meta['market']=='CN' else meta.get('symbol') or meta['name']
    rows = futu_public_items('news_search', keyword, 15, 30)
    seen, items = set(), []
    for row in rows:
        key = re.sub(r'\W','',row['title']).casefold()
        if key and key not in seen and stock_item_relevant(meta,row['title'],row['text']):
            seen.add(key); items.append(row)
    if not items:
        raise ValueError('未返回近30日可核验关联的公司新闻')
    return {'source':'富途新闻', 'items':items[:10], 'as_of':items[0]['published_at'],
            'basis':'标题与摘要线索，不等于公告全文；同一事件的重复标题合并，观点由页面规则归纳。'}


def stock_ifind_evidence(meta):
    cn = meta['market']=='CN'
    symbol = (meta.get('provider_symbol') or cn_provider_symbol(meta['code'])) if cn else meta['symbol']
    target = (symbol[2:]+'.'+symbol[:2].upper()) if cn else ('港股 ' if meta['market']=='HK' else '美股 ')+symbol
    subject = target+' '+meta['name']
    service = 'stock' if cn else 'global_stock'
    operations = [('业务', service, 'get_stock_info' if cn else 'global_stock_profile',
                   '行业、主营业务、主要产品、主营构成、主营业务收入占比'),
                  ('增长与现金', service, 'get_stock_financials' if cn else 'global_stock_financial',
                   '营收同比、归母净利同比、毛利率、经营活动现金流净额、资本开支'),
                  ('资本效率', service, 'get_stock_financials' if cn else 'global_stock_financial',
                   'ROIC、研发资本化比例、股份支付费用')]
    facts, notices, errors, seen = [], [], {}, set()
    for label, server_type, tool, fields in operations:
        try:
            result = ifind_tool(server_type, tool, {'query':subject+' 最新已披露报告的'+fields+
                '；仅此证券，返回证券代码、报告期、披露日期、原文链接、币种、金额单位和百分比单位的结构化表格。缺失留空，不估计。'})
            for row in ifind_rows(result):
                if not ifind_matches(row,symbol,meta['market']):
                    continue
                period = str(field_value(row,'period','report_date','报告期') or '')[:10]
                try:
                    if dt.date.fromisoformat(period)>dt.datetime.now(TZ).date():
                        continue
                except ValueError:
                    continue
                notice_date = str(field_value(row,'notice_date','披露日期') or '')[:10]
                try:
                    if dt.date.fromisoformat(notice_date)>dt.datetime.now(TZ).date():
                        continue
                except ValueError:
                    notice_date = None
                url = public_source_url(field_value(row,'url','source_url','原文链接'))
                for field,(key,kind) in STOCK_RESEARCH_FIELDS.items():
                    value = field_value(row,field,key)
                    if value is None or isinstance(value,(dict,list)) or isinstance(value,bool):
                        continue
                    value = clean_public_text(str(value),1400 if kind=='text' else 120)
                    if not value or value in ('--','—','None','null','暂无'):
                        continue
                    if kind!='text' and number(value.replace(',','').removesuffix('%')) is None:
                        continue
                    unit = '文本' if kind=='text' else str(field_value(row,field+'单位','unit','单位',
                            '金额单位' if kind=='amount' else '百分比单位') or ('%' if '%' in value else ''))
                    currency = field_value(row,'currency','币种')
                    if kind=='amount' and (not unit or currency not in ('CNY','USD','HKD','人民币','美元','港元')):
                        continue
                    if kind=='percent' and unit not in ('%','百分比','百分点'):
                        continue
                    identity = (key,period,value)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    facts.append({'key':key,'label':field,'value':value,'unit':unit,'currency':currency,
                                  'period':period,'notice_date':notice_date,'url':url,'source':'同花顺 iFinD','kind':kind})
            if not any(f['label'] in fields for f in facts):
                errors[label]='未返回代码、报告期和单位均可核验的结构化字段'
        except QUOTE_READ_ERRORS as exc:
            if isinstance(exc,QuoteUnavailable) and not facts:
                raise
            errors[label]=research_error(exc)
    try:
        today=dt.datetime.now(TZ).date()
        result=ifind_tool('news','search_notice',{'query':subject+' 产品交付、订单收入、研发资本化、股份支付及解禁减持公告',
            'time_start':(today-dt.timedelta(days=180)).isoformat(),'time_end':today.isoformat(),'size':8})
        for row in ifind_rows(result):
            title=clean_public_text(field_value(row,'title','标题','公告标题'),250)
            link=public_source_url(field_value(row,'url','原文链接','链接'))
            instant=public_time(field_value(row,'published_at','publish_time','发布时间','公告日期','日期'))
            if title and link and instant and instant.date()>=today-dt.timedelta(days=180) and stock_item_relevant(meta,title):
                notices.append({'title':title,'url':link,'published_at':instant.isoformat(),'source':'同花顺公告检索'})
    except QUOTE_READ_ERRORS as exc:
        errors['公告检索']=research_error(exc)
    if not facts and not notices:
        raise ValueError('同花顺未返回可核验的公司证据')
    return {'source':'同花顺 iFinD','facts':facts[:60],'notices':notices[:8],'errors':errors,
            'as_of':max([f['notice_date'] or f['period'] for f in facts]+[n['published_at'] for n in notices]),
            'basis':'只展示返回的结构化字段；报告期、币种与单位逐项保留，不覆盖原财务口径。'}


def enrich_stock_research(meta, record, previous=None, *, force=False):
    if meta.get('type')!='stock':
        return record
    old=(previous or record).get('research_evidence') or {}
    if old.get('symbol') != research_symbol(meta):
        old={}
    try:
        age=(dt.datetime.now(TZ)-dt.datetime.fromisoformat(old['checked_at'])).total_seconds()
    except (KeyError,ValueError,TypeError):
        age=1e9
    if not force and 0<=age<21600:
        record['research_evidence']=old
        return record
    providers={}
    operations={'pingan':stock_pingan_evidence,'futu_capital':stock_futu_capital,
                'ifind':stock_ifind_evidence,'futu_news':stock_futu_news}
    def load(entry):
        key,operation=entry
        started=time.monotonic()
        checked=now()
        try:
            payload=operation(meta)
            return key,{'status':'partial' if payload.get('errors') else 'ok','checked_at':checked,'data':payload,
                        'elapsed_ms':round((time.monotonic()-started)*1000)}
        except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,subprocess.TimeoutExpired) as exc:
            retained=(old.get('providers') or {}).get(key,{}).get('data')
            return key,{'status':'retained' if retained else 'skipped' if isinstance(exc,QuoteUnavailable) else 'failed',
                        'checked_at':checked,'data':retained,'error':research_error(exc),
                        'data_checked_at':((old.get('providers') or {}).get(key) or {}).get('data_checked_at') or
                            ((old.get('providers') or {}).get(key) or {}).get('checked_at') if retained else None,
                        'elapsed_ms':round((time.monotonic()-started)*1000)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        providers.update(pool.map(load,operations.items()))
    record['research_evidence']={'version':1,'symbol':research_symbol(meta),'checked_at':now(),'providers':providers}
    return record


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
        self.research_jobs = {}
        self.screen_jobs, self.screening_cache = {}, {}
        self.catalog = base_catalog()
        self.watchlist_revision = 0
        self.registry_activated = False
        self.data_revision = 0
        self.state = {'busy': False, 'done': 0, 'total': len(ASSETS), 'stage': 'idle', 'errors': {}, 'last_refresh': None,
                      'price_result': None, 'quote_sources': {}, 'successful_quote_sources': [],
                      'quote_attempts': [],
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
        restored_watch_codes = set()
        if isinstance(cached_catalog, list) and len(cached_catalog) <= 200:
            for item in cached_catalog:
                try:
                    market = item['market']
                    if asset_id(market, item['symbol']) != item['code'] or item.get('type') not in ('stock', 'etf'):
                        continue
                    safe = {k: item[k] for k in ['code', 'symbol', 'market', 'type', 'name', 'currency', 'provider_symbol', 'listed', 'timezone', 'verified_name', 'verified_at', 'tencent_symbol', 'eastmoney_secid', 'search_source'] if k in item}
                    if not isinstance(safe.get('name'), str) or len(safe['name']) > 100:
                        continue
                    if market == 'CN' and not re.fullmatch(r'(?:sh|sz|bj)\d{6}', safe.get('provider_symbol', '')):
                        continue
                    safe.update(watch=item.get('watch') is True, benchmark=item['code'] in BENCHMARK_CODES if market == 'CN' else False)
                    self.catalog[item['code']] = safe
                    if safe['watch']:
                        restored_watch_codes.add(item['code'])
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
        version = self.snapshot.get('watchlist_defaults_version')
        version = version if type(version) is int and version >= 0 else 0
        if self.registry_activated and version < WATCHLIST_DEFAULTS_VERSION:
            if version < 1:
                for code in BENCHMARK_CODES:
                    self.catalog[code]['watch'] = True
            if version < 2:
                for code in OPTION_UNDERLYING_CODES:
                    self.catalog[code]['watch'] = True
            active = {code for code, meta in self.catalog.items() if meta.get('watch')}
            if active != restored_watch_codes:
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
                self.research_jobs.pop(code, None)
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
            record = enrich_stock_research(meta, record, previous)
            with self.lock:
                if self.job_versions.get(code) != version or not self.catalog[code].get('watch') and not self.catalog[code].get('benchmark'):
                    return
                snapshot = {**self.snapshot, 'instruments': {**self.snapshot.get('instruments', {}), code: record}, 'fetched_at': now(), 'catalog': self.catalog_view()}
            self.commit_snapshot(snapshot)
            with self.lock:
                if self.job_versions.get(code) == version:
                    self.asset_jobs[code] = {'status': 'ready', 'stage': '完成', 'finished_at': now(),
                                             'research_gaps': record.get('research_errors', {})}
        except Exception as exc:
            with self.lock:
                if self.job_versions.get(code) == version:
                    self.asset_jobs[code] = {'status': 'error', 'error': str(exc)[:300], 'finished_at': now()}

    def start_research(self, code):
        with self.lock:
            meta = self.catalog.get(code)
            if not meta or meta.get('type') != 'stock' or not (meta.get('watch') or meta.get('benchmark')):
                raise ValueError('请选择当前自选中的股票；ETF 请使用成分穿透研究')
            if code not in self.snapshot.get('instruments', {}):
                raise ValueError('请先取得该股票的一份有效行情快照，再补充独立研究')
            if self.research_jobs.get(code, {}).get('status') == 'loading':
                return False
            job_id = secrets.token_hex(8)
            self.research_jobs[code] = {'status':'loading','id':job_id,'started_at':now(),'stage':'读取公司研究证据'}
        threading.Thread(target=self.refresh_research, args=(code,job_id), daemon=True).start()
        return True

    def refresh_research(self, code, job_id):
        try:
            with self.lock:
                meta = dict(self.catalog[code])
                previous = json.loads(json.dumps(self.snapshot['instruments'][code]))
            result = enrich_stock_research(meta, {}, previous, force=True)['research_evidence']
            # Merge research into the latest price record while holding the same commit lock.
            with self.commit_lock:
                with self.lock:
                    current = self.catalog.get(code, {})
                    if (self.research_jobs.get(code, {}).get('id') != job_id or not current.get('watch')):
                        return
                    records = dict(self.snapshot.get('instruments', {}))
                    if code not in records:
                        return
                    records[code] = {**records[code], 'research_evidence':result}
                    snapshot = {**self.snapshot,'instruments':records}
                self.commit_snapshot(snapshot)
            with self.lock:
                states=[p['status'] for p in result['providers'].values()]
                self.research_jobs[code] = {'status':'ready','finished_at':now(),
                    'result':'ok' if all(x=='ok' for x in states) else 'partial' if any(x in ('ok','partial','retained') for x in states) else 'failed'}
        except Exception as exc:
            with self.lock:
                self.research_jobs[code] = {'status':'error','finished_at':now(),'error':research_error(exc)}

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
                              quote_attempts=[], stage='准备刷新', started_at=now())
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
        attempts = []
        prices_published = False

        def record_attempt(entry):
            with self.lock:
                attempts.append(entry)

        def read_quotes(stage, target, load, *args, **kwargs):
            return traced_quote_call(record_attempt, stage, target, load, *args, **kwargs)

        try:
            with self.lock:
                self.state['quote_attempts'] = attempts
                previous = json.loads(json.dumps(self.snapshot))
                self.state['stage'] = '复核前一交易日收盘K线'
            active = self.active_metadata()
            cn_active = [m for m in active if m['market'] == 'CN']
            cn_assets = [(m['code'], m['name'], m.get('listed')) for m in cn_active]
            quote_symbols = [m['provider_symbol'] for m in cn_active] + [SYMBOLS[a[0]] for a in SECTOR_ASSETS]
            quote_minimum = {m['provider_symbol']: {
                'name': m['name'], 'type': m['type'],
                'timestamp': previous.get('instruments', {}).get(m['code'], {}).get('latest_quote', {}).get('timestamp'),
            } for m in cn_active}
            quote_sources['初始报价'] = {}
            try:
                quotes = read_quotes('初始报价', 'A 股', load_cn_quotes, quote_symbols,
                                     issues=quote_sources['初始报价'], minimum=quote_minimum)
            except OSError as exc:
                errors['A股初始报价'] = str(exc)
                quotes = {}
            with self.lock:
                self.state.update(errors=dict(errors), quote_sources={'初始报价': dict(quote_sources['初始报价'])})
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(read_quotes, 'A 股日线', a[0], load_asset, a, quotes,
                                       previous.get('instruments', {}).get(a[0])): a[0]
                           for a in cn_assets} if quotes else {}
                futures.update({pool.submit(read_quotes, '自选日线', m['code'], load_international_asset, m,
                                            previous.get('instruments', {}).get(m['code'])): m['code']
                                for m in active if m['market'] != 'CN'})
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
                    fresh_quotes = read_quotes('二次报价', 'A 股', load_cn_quotes, quote_symbols,
                                               issues=quote_sources['二次报价'],
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
                for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'etf_flow', 'research_errors', 'research_fetched_at', 'research_evidence']:
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
                    for key in ['fundamentals', 'financial_history', 'announcements', 'etf', 'nav', 'flows', 'etf_flow', 'research_errors', 'research_fetched_at', 'research_evidence']:
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
            stock_meta = [m for m in active if m['type']=='stock' and m['code'] in instruments]
            def supplement(meta):
                code=meta['code']
                enrich_stock_research(meta,instruments[code],previous.get('instruments',{}).get(code))
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(supplement,stock_meta))
            has_external_update = False
            try:
                prior_external = previous.get('external', [])
                external = merge_external(prior_external, read_quotes('外围日线', '', load_external, prior_external))
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
                geopolitics = load_geopolitics({**previous.get('instruments', {}), **price_records}, active)
            except Exception as exc:
                geopolitics = {**previous.get('geopolitics', {}), 'error': str(exc)[:180]}
                errors['geopolitics'] = str(exc)[:180]
            sectors = {s['code']: s for s in previous.get('sectors', [])}
            for a in SECTOR_ASSETS if fresh_quotes else []:
                try:
                    _, sector = read_quotes('板块日线', a[0], load_asset, a, fresh_quotes)
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
            elif geopolitics.get('method') == 'public-evidence-v2' and any(
                    row.get('news') or row.get('community', {}).get('post_count') or row.get('gap', {}).get('items')
                    for row in geopolitics.get('items', [])):
                # News research can refresh independently. Preserve the price snapshot's original time.
                self.commit_snapshot({**previous, 'geopolitics': geopolitics, 'catalog': self.catalog_view()})
        except Exception as exc:
            errors['刷新任务'] = f'{type(exc).__name__}：{str(exc)[:380]}'
        finally:
            with self.lock:
                if not prices_published and not errors:
                    errors['刷新任务'] = '各行情源未返回可发布的价格'
                price_errors = any(key != 'geopolitics' for key in errors)
                result = ('partial' if price_errors else 'ok') if prices_published else 'failed'
                finished_at = now()
                self.state.update(busy=False, stage='完成', errors=errors, quote_sources=quote_sources,
                                  successful_quote_sources=sorted(successful_sources) if prices_published else [],
                                  price_result=result, last_refresh=finished_at)

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
                        old_research=old.get('research_evidence')
                        if old_research and str(old_research.get('checked_at') or '')>str((record.get('research_evidence') or {}).get('checked_at') or ''):
                            record={**record,'research_evidence':old_research}
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


# Private, on-demand account reads. Never merge this response into DataStore or disk caches.
ACCOUNT_READ_LIMIT = threading.BoundedSemaphore(2)
ACCOUNT_MARKETS = {1:('HK','港股','Asia/Hong_Kong'),2:('US','美股','America/New_York'),
                   4:('HKCC','沪深股通','Asia/Shanghai'),6:('SG','新加坡','Asia/Singapore'),
                   8:('AU','澳大利亚','Australia/Sydney'),11:('MY','马来西亚','Asia/Kuala_Lumpur'),
                   12:('CA','加拿大','America/Toronto'),15:('JP','日本','Asia/Tokyo'),
                   18:('KR','韩国','Asia/Seoul')}
PENDING_ACCOUNT_ORDER_STATUSES = frozenset(('WAITING_SUBMIT','SUBMITTING','SUBMITTED','FILLED_PART',
                                           'CANCELLING_PART','CANCELLING_ALL'))
ACCOUNT_CURRENCIES = frozenset(('HKD','USD','CNY','SGD','JPY','AUD','CAD','MYR','KRW'))


class AccountReadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def account_read_error(output):
    text = str(output).upper()
    if 'PERMISSION_DENIED' in text or '403' in text:
        return AccountReadError('permission_denied','富途拒绝账户读取权限（PERMISSION_DENIED / 403）；请核对授权账户及读取权限，行情 AppKey 通过测试不代表账户有权限')
    if any(key in text for key in ('UNAUTHORIZED','NOT LOGGED IN','401','TOKEN EXPIRED','LOGIN REQUIRED')):
        return AccountReadError('login_required','富途 CLI 账户会话不可用，请在运行服务的电脑执行 futu auth login')
    if 'UNKNOWN COMMAND' in text or 'UNKNOWN FLAG' in text:
        return AccountReadError('cli_outdated','当前富途 CLI 不支持账户读取命令，请更新官方 CLI')
    return AccountReadError('provider_failed','富途账户读取失败，请检查 CLI 连接与账户状态后重试；原始命令输出不回传页面')


def futu_account_command(executable, action, *, account=None, currency=None, market=None):
    # Fixed read-only commands. No user-supplied command, flag or executable is accepted.
    routes={'version':['version'],'auth':['auth','status'],'list':['account','list'],
            'funds':['account','funds'],'positions':['position'],'orders':['order','list']}
    if action not in routes:
        raise AccountReadError('invalid_action','不支持的账户读取操作')
    args=[executable,*routes[action]]
    if action in ('funds','positions','orders'):
        if not isinstance(account,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',account):
            raise AccountReadError('invalid_account','账户标识无效')
        args+=['--account',account]
    if action=='funds':
        if currency not in ACCOUNT_CURRENCIES:
            raise AccountReadError('invalid_currency','不支持的资金展示币种')
        args+=['--currency',currency]
    if action=='orders':
        if market not in {entry[0] for entry in ACCOUNT_MARKETS.values()}:
            raise AccountReadError('invalid_market','不支持的订单市场')
        args+=['--market',market]
    args+=['--format','json','--source','skill']
    try:
        result=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',timeout=35)
    except subprocess.TimeoutExpired:
        raise AccountReadError('timeout','富途账户读取超时（35 秒），请稍后重试') from None
    except OSError:
        raise AccountReadError('cli_unavailable','无法运行富途 CLI，请检查本机安装') from None
    if result.returncode:
        raise account_read_error(result.stderr or result.stdout)
    if action=='version':
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError,TypeError):
        raise AccountReadError('invalid_response','富途账户接口未返回有效 JSON') from None


def futu_account_connection():
    executable=futu_cli_path()
    if not executable:
        raise AccountReadError('cli_missing','此电脑尚未安装富途 CLI。请按账户页指引安装，并运行 futu auth login 完成账户授权')
    futu_account_command(executable,'version')
    auth=futu_account_command(executable,'auth')
    if not isinstance(auth,dict) or not isinstance(auth.get('token'),dict) or auth['token'].get('status')!='logged in':
        raise AccountReadError('login_required','富途 CLI 尚未登录或会话已过期，请在运行服务的电脑执行 futu auth login')
    # Discard auth/account metadata; only business reads below are returned.
    return executable


def account_decimal(value):
    from decimal import Decimal, InvalidOperation
    if isinstance(value,bool) or value is None:
        return None
    try:
        number=Decimal(str(value))
        return format(number,'f') if number.is_finite() else None
    except (InvalidOperation,ValueError):
        return None


def normalize_futu_accounts(raw):
    if raw is None:
        raw=[]  # Official CLI prints a nil result slice as JSON null on a successful read.
    if not isinstance(raw,list) or any(not isinstance(r,dict) for r in raw):
        raise AccountReadError('invalid_response','富途账户清单格式无效')
    output,seen=[],set()
    for row in raw:
        account=str(row.get('account_id') or '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',account) or account in seen:
            continue
        seen.add(account)
        markets=row.get('enable_market') or []
        markets=[v for v in markets if type(v) is int] if isinstance(markets,list) else []
        output.append({'account_id':account,'display_id':'••••'+account[-4:],
            'type':clean_public_text(row.get('acc_type'),60),'name':clean_public_text(row.get('name'),100),
            'security_firm':clean_public_text(row.get('security_firm'),80),'markets':markets,
            'market_names':[ACCOUNT_MARKETS[v][1] if v in ACCOUNT_MARKETS else '未接入市场 '+str(v) for v in markets]})
    if raw and not output:
        raise AccountReadError('invalid_response','富途未返回可核验的账户标识')
    return output


def normalize_account_funds(raw,currency):
    if not isinstance(raw,dict) or raw.get('currency')!=currency:
        raise AccountReadError('invalid_response','资金响应缺少同一展示币种，未采用该数据')
    fields=('available_funds','cash','frozen_cash','power','max_withdrawal','total_assets','market_val')
    metrics={key:account_decimal(raw.get(key)) for key in fields}
    if not any(value is not None for value in metrics.values()):
        raise AccountReadError('invalid_response','富途未返回可用资金字段')
    return {'currency':currency,**metrics}


def normalize_account_positions(raw):
    if raw is None:
        raw=[]
    if not isinstance(raw,list) or any(not isinstance(r,dict) for r in raw):
        raise AccountReadError('invalid_response','富途持仓响应格式无效')
    output,skipped=[],0
    for row in raw:
        code=row.get('code')
        qty=account_decimal(row.get('qty'))
        if not isinstance(code,str) or not re.fullmatch(r'[A-Z]{2,8}\.[A-Za-z0-9_.-]{1,80}',code) or qty is None:
            skipped+=1;continue
        from decimal import Decimal
        if Decimal(qty)==0:
            continue
        item={'code':code,'name':clean_public_text(row.get('stock_name'),150),
              'currency':str(row.get('currency') or '未返回')[:10],
              'side':clean_public_text(row.get('position_side'),30),'qty':qty}
        for key in ('can_sell_qty','nominal_price','market_val','today_pl_val'):
            item[key]=account_decimal(row.get(key))
        for key in ('cost_price','pl_val','pl_ratio'):
            item[key]=account_decimal(row.get(key)) if row.get(key+'_valid') is True else None
        output.append(item)
    return {'items':output,'skipped':skipped}


def account_order_time(value):
    # Official create_time can use seconds, milliseconds, microseconds or nanoseconds.
    try:
        if isinstance(value,bool) or value is None:
            return None
        if isinstance(value,(int,float)) or isinstance(value,str) and re.fullmatch(r'\d{10,19}',value):
            from decimal import Decimal
            stamp=Decimal(str(value))
            if not stamp.is_finite() or stamp<=0:
                return None
            while stamp>=10**11:
                stamp/=1000
            whole=int(stamp)
            return dt.datetime.fromtimestamp(whole,dt.timezone.utc)+dt.timedelta(microseconds=int((stamp-whole)*1000000))
        instant=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return instant if instant.tzinfo else None  # Never guess a timezone for a naive timestamp.
    except (ValueError,TypeError,OSError,OverflowError):
        return None


def account_market_day(instant,market):
    zone=next(entry[2] for entry in ACCOUNT_MARKETS.values() if entry[0]==market)
    if market in ('HK','US','HKCC','CA'):
        return quote_day(instant,'America/New_York' if market=='CA' else zone).isoformat()
    if market in ('SG','MY'):
        return instant.astimezone(TZ).date().isoformat()
    if market in ('JP','KR'):
        return instant.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    try:
        from zoneinfo import ZoneInfo
        return instant.astimezone(ZoneInfo(zone)).date().isoformat()
    except (ImportError,KeyError):
        raise AccountReadError('timezone_unavailable','此电脑缺少澳大利亚时区信息，无法判断当日订单；可安装 Python tzdata') from None


def normalize_pending_orders(raw,market,*,current=None):
    current=current or dt.datetime.now(dt.timezone.utc)
    day=account_market_day(current,market)
    if raw is None:
        raw=[]
    if not isinstance(raw,list) or any(not isinstance(r,dict) for r in raw):
        raise AccountReadError('invalid_response','富途当前订单响应格式无效')
    items,unclassified,seen=[],0,set()
    for row in raw:
        status=row.get('status') or row.get('order_status')
        if status not in PENDING_ACCOUNT_ORDER_STATUSES:
            if status not in ('FILLED_ALL','CANCELLED_PART','CANCELLED_ALL','FAILED','DISABLED','DELETED'):
                unclassified+=1
            continue
        instant=account_order_time(row.get('create_time'))
        if instant is None or instant>current:
            unclassified+=1;continue
        if account_market_day(instant,market)!=day:
            continue
        symbol=row.get('symbol') or row.get('code')
        order=str(row.get('order_id') or '')
        qty=account_decimal(row.get('qty'))
        filled=account_decimal(row.get('filled_qty',row.get('dealt_qty')))
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',order) or not isinstance(symbol,str)
                or not re.fullmatch(r'[A-Z]{2,8}\.[A-Za-z0-9_.-]{1,80}',symbol) or qty is None):
            unclassified+=1;continue
        from decimal import Decimal
        if Decimal(qty)<=0 or filled is not None and (Decimal(filled)<0 or Decimal(filled)>=Decimal(qty)):
            continue
        prefix=symbol.split('.')[0]
        if prefix not in ({'SH','SZ','BJ','HKCC'} if market=='HKCC' else {market}):
            unclassified+=1;continue
        identity=(market,order)
        if identity in seen:
            continue
        seen.add(identity)
        items.append({'order_id':order,'symbol':symbol,'name':clean_public_text(row.get('stock_name'),150),
            'market':market,'side':clean_public_text(row.get('side') or row.get('trd_side'),30),
            'type':clean_public_text(row.get('type') or row.get('order_type'),40),'status':status,
            'price':account_decimal(row.get('price')),'qty':qty,'filled_qty':filled,
            'remaining_qty':format(Decimal(qty)-Decimal(filled),'f') if filled is not None else None,
            'created_at':instant.isoformat(),'local_date':day})
    return {'items':sorted(items,key=lambda r:r['created_at'],reverse=True),'unclassified':unclassified,
            'market':market,'local_date':day,'timezone':next(e[2] for e in ACCOUNT_MARKETS.values() if e[0]==market)}


def read_futu_account(payload):
    action=payload.get('action','list')
    if action not in ('list','snapshot'):
        raise AccountReadError('invalid_action','仅支持账户清单与账户概览读取')
    if not ACCOUNT_READ_LIMIT.acquire(blocking=False):
        raise AccountReadError('busy','账户读取正在进行，请稍后重试')
    try:
        executable=futu_account_connection()
        accounts=normalize_futu_accounts(futu_account_command(executable,'list'))
        if action=='list':
            return {'accounts':accounts,'checked_at':now(),'source':'富途官方 CLI / OAuth'}
        account=payload.get('account_id')
        selected=next((a for a in accounts if a['account_id']==account),None)
        if selected is None:
            raise AccountReadError('invalid_account','该账户不在当前富途授权清单中，请重新读取账户')
        currency=payload.get('currency','HKD')
        if currency not in ACCOUNT_CURRENCIES:
            raise AccountReadError('invalid_currency','不支持的资金展示币种')
        sections={}
        def load(key,action,**kwargs):
            started=now()
            try:
                raw=futu_account_command(executable,action,account=account,**kwargs)
                value=normalize_account_funds(raw,currency) if action=='funds' else normalize_account_positions(raw) if action=='positions' else normalize_pending_orders(raw,kwargs['market'])
                return key,{'ok':True,'data':value,'checked_at':now(),'requested_at':started}
            except AccountReadError as exc:
                return key,{'ok':False,'error':str(exc),'code':exc.code,'checked_at':now()}
        operations=[('funds','funds',{'currency':currency}),('positions','positions',{})]
        operations += [('orders:'+ACCOUNT_MARKETS[v][0],'orders',{'market':ACCOUNT_MARKETS[v][0]}) for v in dict.fromkeys(selected['markets']) if v in ACCOUNT_MARKETS]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            tasks=[pool.submit(load,key,operation,**kwargs) for key,operation,kwargs in operations]
            sections.update(task.result() for task in tasks)
        unsupported=[v for v in selected['markets'] if v not in ACCOUNT_MARKETS]
        return {'account':selected,'currency':currency,'sections':sections,'checked_at':now(),
                'source':'富途官方 CLI / OAuth','order_date_basis':'按各市场交易所当地日期筛选今日新建、仍在途或部分成交的订单',
                'unsupported_order_markets':unsupported,'order_markets_missing':not bool(selected['markets'])}
    finally:
        ACCOUNT_READ_LIMIT.release()


class LocalHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, handler):
        host, _ = address
        if ':' in host:
            self.address_family = socket.AF_INET6
        self.listen_host = host
        super().__init__(address, handler)

    def local_url(self):
        host = {'': '127.0.0.1', '0.0.0.0': '127.0.0.1', '::': '::1'}.get(self.listen_host, self.listen_host)
        if ':' in host:
            host = f'[{host}]'
        return f'http://{host}:{self.server_port}/'


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
        # Reverse proxies can forward the public host when they rewrite Host.
        return site == 'same-origin' or url.netloc.lower() in (
            self.headers.get('Host', '').lower(), self.headers.get('X-Forwarded-Host', '').strip().lower())

    def market_key_statuses(self):
        statuses = {'ifind': {'available': True, **ifind_connection_status()}}
        statuses.update({provider: {'available': True, **market_key_status(provider)}
                         for provider in MARKET_KEY_SETTINGS})
        return statuses

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
                          'fetched_at': self.server.store.snapshot.get('fetched_at'), 'environment': 'local-server', 'data_revision': self.server.store.data_revision, 'watchlist_revision': self.server.store.watchlist_revision, 'asset_jobs': dict(self.server.store.asset_jobs), 'research_jobs': dict(self.server.store.research_jobs)}
                result['quote_attempts'] = list(result.get('quote_attempts', []))
            result['market_keys'] = self.market_key_statuses()
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
                        'user-state.js', 'record-ui.js', 'themes.css', 'watchlist-manager.js', 'watchlist-manager.css', 'factor-screening.js', 'factor-screening.css', 'risk-engine.js', 'risk-views.js', 'risk.css', 'market-cards.js', 'market-cards.css', 'wyckoff-engine.js', 'wyckoff-views.js', 'wyckoff.css', 'stock-research.js', 'stock-research.css', 'futu-account.js', 'futu-account.css', 'install_futu_cli.py']:
            return self.send({'error': 'Not found'}, status=404)
        embedded = globals().get('EMBEDDED_ASSETS', {})
        if name in embedded:
            text = embedded[name]
        else:
            text = (ROOT / ('tools' if name == 'install_futu_cli.py' else 'dist') / name).read_text(encoding='utf-8')
        if name == 'index.html':
            text = text.replace('</head>', '<script>window.RISKLENS_LOCAL=true;window.RISKLENS_TOKEN=' + json.dumps(TOKEN) + ';</script></head>')
        if name == 'install_futu_cli.py':
            return self.send(text.encode('utf-8'), 'application/octet-stream')
        kind = 'text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/plain' if name.endswith('.txt') else 'application/javascript'
        self.send(text.encode(), kind + '; charset=utf-8')

    def do_POST(self):
        key_provider = MARKET_KEY_TEST_PATHS.get(self.path)
        if not key_provider and self.path not in ('/api/refresh', '/api/watchlist/add', '/api/watchlist/remove', '/api/watchlist/refresh', '/api/screening/start', '/api/research/refresh', '/api/account/read'):
            return self.send({'error': 'Not found'}, status=404)
        if not self.is_page_request():
            return self.send({'error': '仅允许当前页面发起操作'}, status=403)
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
            if not 0 < length <= (16384 if key_provider == 'futu' else 4096):
                raise ValueError('请求体长度无效')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('需要JSON对象')
            if key_provider:
                result = test_ifind_connection(payload) if key_provider == 'ifind' else test_market_key(key_provider, payload)
                result['market_keys'] = self.market_key_statuses()
                return self.send(result)
            store = self.server.store
            if self.path == '/api/account/read':
                try:
                    return self.send(read_futu_account(payload))
                except AccountReadError as exc:
                    return self.send({'error':str(exc),'code':exc.code},status=409 if exc.code=='busy' else 400 if exc.code.startswith('invalid_') else 502)
            if self.path == '/api/research/refresh':
                started = store.start_research(payload.get('code'))
                return self.send({'started':started},status=202 if started else 200)
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
    parser = argparse.ArgumentParser(description='观澜本地研究环境（行情、公司研究与富途账户查询）')
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
    server = LocalHTTPServer((args.host, args.port), Handler)
    server.store = store
    if not args.no_refresh:
        store.start()
    url = server.local_url()
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
