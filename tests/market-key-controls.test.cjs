const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('API key is the only input and retest runs directly even if the snapshot is unavailable', async () => {
 const requests = [];
 const context = vm.createContext({
  window: {}, esc: String, panel: (title, body) => title + body,
  modal(title, body) { context.dialog = {title, body}; },
  render() {},
  async fetch(url, options) {
   requests.push({url, options});
   return {ok: true, json: async () => ({result: 'ok', checks: [], configured: true})};
  }
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/local-connector.js'), 'utf8'), context);
 vm.runInContext("localStatus={errors:{connection:'HTTP 503'}}", context);
 const cards = vm.runInContext('marketKeyPanel()', context);
 assert.equal((cards.match(/输入或更换密钥/g) || []).length, 4);
 assert.equal((cards.match(/测试已配置密钥/g) || []).length, 4);
 assert.doesNotMatch(cards, /<button[^>]*\bdisabled\b/);
 vm.runInContext("openMarketKey('massive')", context);
 assert.match(context.dialog.body, /name="provider-key" type="password"/);
 assert.equal((context.dialog.body.match(/<input\b/g) || []).length, 1);
 assert.match(cards, /onclick="runMarketKeyTest\('massive'\)"/);
 assert.equal(await vm.runInContext("runMarketKeyTest('massive')", context), true);
 assert.equal(requests[0].url, '/api/market-keys/massive/test');
 assert.equal(requests[0].options.body, '{}');
});

test('refresh details open in a dialog and display only the current run', () => {
 const dialog = {dataset: {}};
 const context = vm.createContext({window: {}, esc: String,
  document: {getElementById: () => dialog},
  modal(title, body) { dialog.title = title; dialog.body = body; }
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/local-connector.js'), 'utf8'), context);
 const yahoo = {provider: 'Yahoo Finance', status: 'failed', stage: '外围日线', target: 'SOX', detail: 'HTTP 403', elapsed_ms: 25};
 const futu = {...yahoo, provider: '富途', status: 'ok', detail: ''};
 context.status = {quote_attempts: [futu], quote_attempt_history: [{attempts: [yahoo]}]};
 vm.runInContext('openLocalRefreshDetails(status)', context);
 assert.equal(dialog.title, '行情刷新详情');
 assert.equal(dialog.dataset.kind, 'quote-refresh');
 assert.match(dialog.body, /富途<\/td>\s*<td>成功<\/td>/);
 assert.doesNotMatch(dialog.body, /Yahoo Finance|HTTP 403|<details>/);
});

test('Futu key setup offers signed AppKey and OAuth modes', () => {
 const dialog = {querySelector: () => ({}), addEventListener() {}};
 const context = vm.createContext({window: {}, esc: String,
  document: {getElementById: () => dialog},
  modal(title, body) { dialog.body = body; }
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/local-connector.js'), 'utf8'), context);
 vm.runInContext("openMarketKey('futu')", context);
 assert.match(dialog.body, /value="appkey" selected/);
 assert.match(dialog.body, /AppKey ID|PEM 私钥/);
 assert.match(dialog.body, /name="private-key" required/);
 assert.match(dialog.body, /value="Ed25519"/);
 vm.runInContext("localStatus={market_keys:{futu:{auth_mode:'oauth'}}};openMarketKey('futu')", context);
 assert.match(dialog.body, /value="oauth" selected/);
 assert.match(dialog.body, /fieldset class="market-key-signing" hidden disabled/);
});

test('refresh opens the dialog for a new run and replaces its contents with the current status', async () => {
 const requests=[];
 const content={querySelector:()=>null};
 const dialog={dataset:{},querySelector:()=>content};
 const context=vm.createContext({window:{RISKLENS_LOCAL:true},esc:String,toast(){},render(){},
  document:{getElementById:()=>dialog},
  modal(title,body){dialog.open=true;content.innerHTML=body;},
  async fetch(path){requests.push(path);return {ok:true,json:async()=>({started:true})};}
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/local-connector.js'), 'utf8'), context);
 vm.runInContext(`localStatus={quote_attempts:[{provider:'上一轮来源'}]};
  connectLocal=async()=>{
   if(!localRefreshPending)throw Error('missing pending status');
   localStatus={busy:true,quote_attempts:[{provider:'富途',status:'ok',stage:'外围日线',target:'SOX'}]};
   updateLocalRefreshDetails();
  };`,context);
 await vm.runInContext('refreshLocalData()',context);
 assert.deepEqual(requests,['/api/refresh']);
 assert.equal(dialog.open,true);
 assert.match(content.innerHTML,/富途.*成功/s);
 assert.doesNotMatch(content.innerHTML,/上一轮来源/);
});

test('Futu AppKey form submits the signing pair and clears it after saving', async () => {
 const requests=[];
 const fields={'provider-key':{value:'test-app-id'},'private-key':{value:'fixture pem'},
  'auth-mode':{value:'appkey'},'signing-algorithm':{value:'Ed25519'}};
 const submit={},feedback={};
 const form={isConnected:true,elements:{namedItem:name=>fields[name]},
  querySelector:selector=>selector.includes('submit')?submit:feedback};
 const dialog={open:true,close(){this.open=false;}};
 const context=vm.createContext({window:{},event:{preventDefault(){},currentTarget:form},
  document:{getElementById:()=>dialog},render(){},
  async fetch(path,options){requests.push([path,JSON.parse(options.body)]);return {ok:true,json:async()=>({saved:true,checks:[]})};}
 });
 vm.runInContext(fs.readFileSync(path.join(__dirname, '../dist/local-connector.js'), 'utf8'), context);
 await vm.runInContext("confirmMarketKey(event,'futu')",context);
 assert.deepEqual(requests,[['/api/market-keys/futu/test',{
  token:'test-app-id',mode:'appkey',algorithm:'Ed25519',private_key:'fixture pem'}]]);
 assert.equal(dialog.open,false);
 assert.equal(fields['provider-key'].value,'');
 assert.equal(fields['private-key'].value,'');
});
