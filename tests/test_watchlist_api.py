import importlib.util,json,threading,tempfile,unittest,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
spec=importlib.util.spec_from_file_location('api_server',Path(__file__).resolve().parents[1]/'local/server.py');app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
class QuietHandler(app.Handler):
    def log_message(self,*args):pass
class APITests(unittest.TestCase):
    def test_crud_contract_auth_origin_and_revision(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'cache_locations',return_value=[]):
            store=app.DataStore(Path(folder)/'cache.json');server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler);server.store=store
            threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_port)
            def request(path,value=None,token=True,origin=None):
                headers={'Content-Type':'application/json'}
                if token:headers['X-RiskLens-Token']=app.TOKEN
                if origin:headers['Origin']=origin
                req=urllib.request.Request(base+path,data=json.dumps(value).encode() if value is not None else None,headers=headers)
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
            finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
