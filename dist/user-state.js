'use strict';
const RiskUserState=(()=>{
 const NOTES_KEY='risklens-v2-notes',THEME_KEY='risklens-theme';
 const validTheme=value=>value==='polymarket'?'polymarket':'light';
 function hash(value){let h=2166136261;for(const c of value){h^=c.charCodeAt(0);h=Math.imul(h,16777619)}return (h>>>0).toString(36)}
 function normalize(notes){const seen=new Set();return notes.map((note,index)=>{if(!note||typeof note!=='object'||Array.isArray(note))throw Error('研究记录格式异常，现有数据未覆盖');let id=typeof note.id==='string'&&/^[a-zA-Z0-9_-]{1,120}$/.test(note.id)?note.id:'legacy-'+hash(JSON.stringify(note))+'-'+index;while(seen.has(id))id+='-duplicate';seen.add(id);return {...note,id}})}
 function entries(notes){return notes.map((note,index)=>({note,index,beforeId:notes[index-1]?.id||null,afterId:notes[index+1]?.id||null}))}
 function createStore(storage){
  function list(){if(!storage)throw Error('当前浏览器无法读取本地存储');const raw=storage.getItem(NOTES_KEY);if(raw===null)return [];let notes;try{notes=JSON.parse(raw)}catch{throw Error('研究记录格式异常，现有数据未覆盖')}if(!Array.isArray(notes))throw Error('研究记录格式异常，现有数据未覆盖');return normalize(notes)}
  function save(notes){if(!storage)throw Error('当前浏览器无法保存研究记录');storage.setItem(NOTES_KEY,JSON.stringify(normalize(notes)))}
  return {list,
   add(note){const notes=list(),id=typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'note-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);const added={...note,id};save([added,...notes]);return added},
   remove(id){const notes=list(),removed=entries(notes).filter(e=>e.note.id===id);if(removed.length)save(notes.filter(n=>n.id!==id));return removed},
   clear(expectedIds){const notes=list();if(expectedIds&&(notes.length!==expectedIds.length||notes.some(n=>!expectedIds.includes(n.id))))throw Error('记录已发生变化，请重新确认清空');if(notes.length)save([]);return entries(notes)},
   restore(deleted){const notes=list(),ids=new Set(notes.map(n=>n.id));let restored=0;for(const entry of deleted){if(ids.has(entry.note.id))continue;const next=notes.findIndex(n=>n.id===entry.afterId),previous=notes.findIndex(n=>n.id===entry.beforeId),index=next>=0?next:previous>=0?previous+1:notes.length;notes.splice(index,0,entry.note);ids.add(entry.note.id);restored++}if(restored)save(notes);return restored},
   getTheme(){try{return validTheme(storage?.getItem(THEME_KEY))}catch{return 'light'}},
   saveTheme(theme){if(!['light','polymarket'].includes(theme))throw Error('未知网站风格');if(!storage)throw Error('当前浏览器无法保存风格偏好');storage.setItem(THEME_KEY,theme)}
  }
 }
 let storage=null;try{if(typeof localStorage!=='undefined')storage=localStorage}catch{}
 const store=createStore(storage);
 if(typeof document!=='undefined')document.documentElement.dataset.theme=store.getTheme();
 return {...store,createStore,NOTES_KEY,THEME_KEY,validTheme};
})();
if(typeof module!=='undefined')module.exports=RiskUserState;
