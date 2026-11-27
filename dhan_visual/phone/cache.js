/* Browser-local market snapshots only. No credentials, tokens or order data. */
(function(root){
"use strict";
const key="ccs-market-snapshot-v1";
function complete(s){return !!(s&&Array.isArray(s.rows)&&s.filters&&typeof s.scan_id==="string"&&s.scan_id&&typeof s.completed_at==="string"&&Number.isFinite(Date.parse(s.completed_at))&&!s.pending&&!s.error&&s.progress?.complete===true&&!s.progress.stopped);}
function record(s){
 if(!complete(s))return null;
 const {rows,expiry,scan_id,completed_at,cash_asof,scan_day,checked,total,cash,pending,error,asof,filters,progress}=s;
 return {v:1,snapshot:{rows,expiry,scan_id,completed_at,cash_asof,scan_day,checked,total,cash,pending,error,asof,filters,progress}};
}
function read(storage){try{const r=JSON.parse(storage.getItem(key)||"null");return r?.v===1&&complete(r.snapshot)?r.snapshot:null}catch(e){return null}}
function save(storage,s){try{const r=record(s);if(!r)return false;const old=read(storage);if(old&&Date.parse(old.completed_at)>Date.parse(s.completed_at))return false;storage.setItem(key,JSON.stringify(r));return true}catch(e){return false}}
function forget(storage){try{storage.removeItem(key)}catch(e){}}
function decision(display,incoming){
 if(!complete(incoming))return display?.scan_id&&display.scan_id!==incoming?.scan_id?"hold":"partial";
 return display?.scan_id&&display.scan_id!==incoming.scan_id?"offer":"show";
}
const api={complete,read,save,forget,decision};
if(typeof module!=="undefined"&&module.exports)module.exports=api;else root.CCSCache=api;
})(typeof globalThis!=="undefined"?globalThis:this);
