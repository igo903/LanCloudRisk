#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install a signed official Futu CLI release using Python HTTPS (HTTP/1.1), not curl."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

RELEASE_BASE = 'https://github.com/FutunnOpen/futu-cli'
RELEASE_KEY = b'''-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEyk556CXX05/DwVDZl9lJIt74Vut5
+4Fj6BuwyUATzxXDI5O3GohEhC3X3m0ygsH/p3ClarvAUwfmRF+1DfEkWQ==
-----END PUBLIC KEY-----
'''
STAGE = '环境检查'


class InstallError(Exception):
    pass


def say(stage, message):
    global STAGE
    STAGE = stage
    print('[{}] {}'.format(stage, message), flush=True)


def asset_platform(system=None, machine=None):
    system, machine = system or platform.system(), machine or platform.machine()
    arch = {'arm64':'arm64', 'aarch64':'arm64', 'x86_64':'amd64', 'amd64':'amd64'}.get(machine.lower())
    if not arch or system not in ('Darwin', 'Linux'):
        raise InstallError('此工具支持 Mac Intel / Apple Silicon 和 Linux；Windows 请使用富途官方 PowerShell 安装器。')
    return ('darwin_' if system=='Darwin' else 'linux_musl_')+arch


def opener():
    context = ssl.create_default_context()
    if hasattr(context, 'set_alpn_protocols'):
        context.set_alpn_protocols(['http/1.1'])
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def request_url(client, url, *, output=None, limit=128*1024*1024, method='GET'):
    last = None
    for attempt in range(3):
        request = urllib.request.Request(url, method=method, headers={'User-Agent':'LanCloudRisk-FutuInstaller/1.0'})
        try:
            with client.open(request, timeout=30) as response:
                if urllib.parse.urlsplit(response.geturl()).scheme!='https':
                    raise InstallError('下载地址重定向到非 HTTPS，已停止。')
                if method=='HEAD':
                    return response.geturl()
                content, count = [], 0
                handle = open(output,'wb') if output else None
                try:
                    while True:
                        chunk = response.read(256*1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count>limit:
                            raise InstallError('官方下载内容超过允许大小，已停止。')
                        if handle:
                            handle.write(chunk)
                        else:
                            content.append(chunk)
                finally:
                    if handle:
                        handle.close()
                return output if output else b''.join(content)
        except (OSError, urllib.error.URLError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401,403,404):
                break
            if attempt<2:
                time.sleep(attempt+1)
    if isinstance(last, urllib.error.HTTPError):
        raise InstallError('官方源返回 HTTP {}（{}）；请保留本行错误。'.format(last.code,urllib.parse.urlsplit(url).hostname))
    reason = getattr(last,'reason',last)
    if isinstance(reason,ssl.SSLCertVerificationError):
        raise InstallError('Python HTTPS 证书校验失败；请修复当前 Python 的证书配置后重试，不应关闭证书校验。')
    raise InstallError('HTTPS 下载失败（{}）；请检查系统网络或代理。'.format(type(reason).__name__))


def latest_version(client):
    try:
        url=request_url(client,RELEASE_BASE+'/releases/latest',method='HEAD')
        version=urllib.parse.urlsplit(url).path.rstrip('/').split('/')[-1]
        if re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):
            return version
    except InstallError:
        pass
    raw=request_url(client,'https://api.github.com/repos/FutunnOpen/futu-cli/releases/latest',limit=1024*1024)
    version=json.loads(raw).get('tag_name')
    if not isinstance(version,str) or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):
        raise InstallError('官方版本信息无法核验。')
    return version


def verified_members(archive, expected_version):
    with tarfile.open(archive,'r:gz') as package:
        binaries=[m for m in package.getmembers() if m.name in ('futu','./futu')]
        versions=[m for m in package.getmembers() if m.name in ('VERSION','./VERSION')]
        if len(binaries)!=1 or len(versions)!=1 or not binaries[0].isfile() or not versions[0].isfile():
            raise InstallError('安装包缺少唯一的程序或版本文件；不解压目录、链接或其他文件。')
        if binaries[0].size>256*1024*1024 or versions[0].size>100:
            raise InstallError('安装包文件大小异常。')
        actual=package.extractfile(versions[0]).read().decode('ascii').strip()
        if actual!=expected_version:
            raise InstallError('安装包内版本与官方发布版本不一致。')
        return package.extractfile(binaries[0]).read()


def install(destination, version=None, *, client=None):
    target_platform=asset_platform()
    openssl=shutil.which('openssl')
    if not openssl:
        raise InstallError('缺少 openssl，无法验证富途官方发布签名；未安装程序。')
    client=client or opener()
    say('版本查询','正在通过 Python HTTPS 查询官方发行版（不调用 curl）。')
    version=version or latest_version(client)
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',version):
        raise InstallError('版本格式无效。')
    asset='futu_{}_{}.tar.gz'.format(version,target_platform)
    say('下载','{} / {}'.format(version,target_platform))
    with tempfile.TemporaryDirectory(prefix='futu-python-install-') as tmp:
        tmp=Path(tmp)
        for name,maximum in ((asset,128*1024*1024),('futu_checksums.txt',1024*1024),('futu_checksums.txt.sig',65536)):
            say('下载',name)
            request_url(client,RELEASE_BASE+'/releases/download/'+version+'/'+name,output=tmp/name,limit=maximum)
        say('校验','验证富途官方签名和安装包 SHA-256。')
        (tmp/'release.pub').write_bytes(RELEASE_KEY)
        checked=subprocess.run([openssl,'dgst','-sha256','-verify',str(tmp/'release.pub'),'-signature',
            str(tmp/'futu_checksums.txt.sig'),str(tmp/'futu_checksums.txt')],capture_output=True,timeout=20)
        if checked.returncode:
            raise InstallError('官方签名验证未通过；未安装程序。')
        checksums=[]
        for line in (tmp/'futu_checksums.txt').read_text(encoding='utf-8').splitlines():
            parts=line.split()
            if len(parts)==2 and parts[1].lstrip('*')==asset and re.fullmatch(r'[a-fA-F0-9]{64}',parts[0]):
                checksums.append(parts[0].lower())
        if len(checksums)!=1 or hashlib.sha256((tmp/asset).read_bytes()).hexdigest()!=checksums[0]:
            raise InstallError('安装包 SHA-256 未通过，未安装程序。')
        binary=verified_members(tmp/asset,version)
        say('写入',str(destination))
        destination=Path(destination).expanduser()
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        staging=None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.futu-install-',delete=False) as handle:
                staging=Path(handle.name);handle.write(binary);handle.flush();os.fsync(handle.fileno())
            staging.chmod(0o755)
            tested=subprocess.run([str(staging),'version'],capture_output=True,text=True,timeout=15)
            if tested.returncode or version not in tested.stdout:
                raise InstallError('程序无法运行或版本不匹配；原程序保持不变。')
            os.replace(staging,destination)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
    say('完成','官方 Futu CLI {} 已安装且版本验证通过。'.format(version))
    return destination


def auth_status(binary):
    say('登录检查','检查富途 CLI 登录状态；不读取账户持仓或输出令牌。')
    result=subprocess.run([str(binary),'auth','status','--format','json','--source','skill'],capture_output=True,text=True,timeout=20)
    try:
        payload=json.loads(result.stdout)
    except (ValueError,TypeError):
        raise InstallError('CLI 登录状态响应无法解析；请单独运行程序的 auth status 检查。')
    status=(payload.get('token') or {}).get('status') if isinstance(payload,dict) else None
    if status=='logged in' and result.returncode==0:
        say('登录检查','已登录。可回到 LanCloudRisk 账户页刷新账户。')
        return True
    say('需要登录','CLI 已安装；富途 OAuth 尚未完成或已过期。')
    return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path.home()/'.futu/bin')
    parser.add_argument('--version',default=None)
    parser.add_argument('--login',action='store_true',help='安装成功后打开官方 CLI 登录；由用户完成富途授权')
    parser.add_argument('--force',action='store_true',help='重新安装，即使当前已有可运行的 CLI')
    args=parser.parse_args()
    try:
        binary=args.directory.expanduser()/'futu'
        healthy=False
        if binary.is_file() and not args.force:
            try:
                result=subprocess.run([str(binary),'version'],capture_output=True,text=True,timeout=15)
                healthy=result.returncode==0 and 'futu version' in result.stdout
            except OSError:
                pass
        if healthy:
            say('环境检查','已找到可运行的 Futu CLI：'+str(binary))
        else:
            binary=install(binary,args.version)
        if auth_status(binary):
            return 0
        if args.login:
            say('授权','即将运行富途官方登录；请在弹出的浏览器中完成授权。')
            return subprocess.run([str(binary),'auth','login','--source','skill']).returncode
        print('下一步运行：\n"{}" auth login'.format(binary),flush=True)
        return 0
    except (InstallError,OSError,ValueError,subprocess.TimeoutExpired,tarfile.TarError) as exc:
        if isinstance(exc,PermissionError):
            message='目录权限不足：{}。请将此行和当前阶段反馈，不要使用 sudo 运行登录。'.format(getattr(exc,'filename','未返回'))
        elif isinstance(exc,InstallError):
            message=str(exc)
        else:
            message='{}；程序已停止，没有继续尝试登录。'.format(type(exc).__name__)
        print('[失败阶段：{}] {}'.format(STAGE,message),file=sys.stderr,flush=True)
        return 1


if __name__=='__main__':
    sys.exit(main())
