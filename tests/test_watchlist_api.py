import importlib.util,json,threading,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
spec=importlib.util.spec_from_file_location('api_server',Path(__file__).resolve().parents[1]/'local/server.py');app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
class QuietHandler(app.Handler):
    def log_message(self,*args):pass
class APITests(unittest.TestCase):
    def test_ifind_settings_test_accepts_loopback_only_and_preserves_existing_key(self):
        with tempfile.TemporaryDirectory() as folder:
            key_path=Path(folder)/'private'/'ifind.json'
            old_key='fixture-saved-key'
            rejected_key='fixture-rejected-key'
            cache=Path(folder)/'cache.json'
            with patch.object(app,'ifind_settings_path',return_value=key_path), \
                 patch.object(app,'cache_locations',return_value=[cache]):
                store=app.DataStore(cache)
                with ThreadingHTTPServer(('127.0.0.1',0),QuietHandler) as server:
                    server.store=store
                    thread=threading.Thread(target=server.serve_forever,daemon=True)
                    thread.start()
                    base='http://127.0.0.1:'+str(server.server_port)
                    def request(path,payload=None,**extra_headers):
                        headers={'X-RiskLens-Token':app.TOKEN,'Content-Type':'application/json',**extra_headers}
                        data=None if payload is None else json.dumps(payload).encode()
                        req=urllib.request.Request(base+path,data=data,headers=headers)
                        try:
                            with urllib.request.urlopen(req) as response:return response.status,json.load(response)
                        except urllib.error.HTTPError as response:return response.code,json.load(response)
                    def provider(symbols,failures,token):
                        if token==rejected_key or symbols==['sh510300']:
                            failures.append('HTTP 403（访问被拒绝）')
                            return {}
                        return {symbols[0]:{'timestamp':'2026-11-10T15:00:00+08:00','price':100}}
                    try:
                        with patch.object(app,'load_ifind_quotes',side_effect=provider) as quotes:
                            self.assertEqual(request('/api/ifind/test',{'token':old_key},Host='proxy.example')[0],403)
                            self.assertEqual(request('/api/ifind/test',{'token':old_key},Origin='http://localhost:'+str(server.server_port))[0],403)
                            quotes.assert_not_called()
                            status,result=request('/api/ifind/test',{'token':old_key},Origin=base)
                            self.assertEqual((status,result['result'],result['saved']),(200,'partial',True))
                            self.assertNotIn(old_key,json.dumps(result))
                            self.assertEqual(app.ifind_token(),old_key)
                            self.assertEqual(key_path.stat().st_mode & 0o777,0o600)
                            status,result=request('/api/ifind/test',{'token':rejected_key})
                            self.assertEqual((status,result['result'],result['saved']),(200,'failed',False))
                            self.assertNotIn(rejected_key,json.dumps(result))
                            self.assertEqual(app.ifind_token(),old_key)
                            self.assertEqual(request('/api/ifind/test',{})[1]['result'],'partial')
                            self.assertEqual(request('/api/status')[1]['ifind'],{'available':True,'configured':True})
                            self.assertNotIn(old_key,json.dumps(request('/api/snapshot')[1]))
                            self.assertEqual(request('/ifind.json')[0],404)
                    finally:
                        server.shutdown()
                        thread.join()

    def test_crud_contract_auth_origin_and_revision(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'cache_locations',return_value=[]):
            store=app.DataStore(Path(folder)/'cache.json');server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler);server.store=store
            threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
            def request(path,value=None,token=True,origin=None,fetch_site=None,method=None):
                headers={'Content-Type':'application/json'}
                if token:headers['X-RiskLens-Token']=app.TOKEN
                if origin:headers['Origin']=origin
                if fetch_site:headers['Sec-Fetch-Site']=fetch_site
                req=urllib.request.Request(base+path,data=json.dumps(value).encode() if value is not None else None,headers=headers,method=method)
                try:
                    with urllib.request.urlopen(req) as response:return response.status,json.load(response)
                except urllib.error.HTTPError as response:return response.code,json.load(response)
            meta={'code':'US.NVDA','symbol':'NVDA','market':'US','name':'英伟达','currency':'USD','type':'stock','provider_symbol':'NVDA','watch':True}
            try:
                payload={'market':'US','symbol':'NVDA','expected_revision':0}
                self.assertEqual(request('/api/watchlist/add',payload,token=False)[0],403)
                self.assertEqual(request('/api/screening/start',payload,token=False)[0],403)
                with patch.object(store,'start_screening',return_value='test-screen'):
                    self.assertEqual(request('/api/screening/start',{'source':'watch','codes':['002281']})[0],202)
                store.screen_jobs['test-screen']={'status':'ready','result':{'catalog':[],'instruments':{}}}
                self.assertEqual(request('/api/screening?id=test-screen')[1]['status'],'ready')
                self.assertEqual(request('/api/screening?id=expired')[0],404)
                self.assertEqual(request('/api/watchlist/add',payload,origin='https://evil.example')[0],403)
                with patch.object(app,'resolve_security',return_value=meta),patch.object(store,'start_asset'):
                    self.assertEqual(request('/api/watchlist/add',payload)[0],202)
                    self.assertEqual(request('/api/watchlist/add',payload)[0],409)
                snapshot=request('/api/snapshot')[1];self.assertTrue(next(m for m in snapshot['catalog'] if m['code']=='US.NVDA')['watch'])
                self.assertEqual(request('/api/watchlist/remove',{'code':'US.NVDA','expected_revision':1})[0],200)
                self.assertEqual(request('/api/watchlist/remove',[])[0],400)
                self.assertEqual(request('/local/server.py')[0],404)
                self.assertEqual(request('/.git/config')[0],404)
                self.assertEqual(request('/api/status')[1]['total'],13)
                self.assertEqual(request('/api/refresh',token=False,method='POST')[1]['code'],'stale_session')
                self.assertEqual(request('/api/session',origin='https://preview.example',fetch_site='same-origin'),(200,{'token':app.TOKEN}))
                self.assertEqual(request('/api/session',origin='https://evil.example',fetch_site='cross-site')[0],403)
                with patch.object(store,'start',side_effect=[True,False]) as start:
                    self.assertEqual(request('/api/refresh',origin='https://preview.example',fetch_site='same-origin',method='POST'),(202,{'started':True}))
                    self.assertEqual(request('/api/refresh',origin='https://evil.example',fetch_site='cross-site',method='POST')[0],403)
                    self.assertEqual(request('/api/refresh',method='POST'),(200,{'started':False}))
                    self.assertEqual(start.call_count,2)
            finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
