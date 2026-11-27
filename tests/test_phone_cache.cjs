const assert=require("node:assert/strict");
const cache=require("../dhan_visual/phone/cache.js");
const storage={data:{},getItem(k){return this.data[k]??null},setItem(k,v){this.data[k]=v},removeItem(k){delete this.data[k]}};
function snapshot(id,stamp,overrides={}){
 return {scan_id:id,completed_at:stamp,expiry:"DEC-26",filters:{expiry:"DEC-26",maximum:7,minimum:2,r1:false},
 rows:[{Stock:"DEMO","5%+ OTM CE (%)":3.5,otm_call_detail:{strike:"105"}}],pending:false,error:"",
 progress:{complete:true,stopped:false},...overrides};
}
const old=snapshot("old","2026-11-27T09:15:00+05:30");
const fresh=snapshot("new","2026-11-27T09:25:00+05:30");
assert.equal(cache.read(storage),null);
assert.equal(cache.save(storage,old),true);
assert.equal(cache.read(storage).scan_id,"old");
assert.equal(cache.read(storage).rows[0].otm_call_detail.strike,"105");
const partial={...fresh,pending:true,progress:{complete:false}};
assert.equal(cache.save(storage,partial),false);
assert.equal(cache.decision(old,partial),"hold");
assert.equal(cache.read(storage).scan_id,"old");
const failed={...fresh,error:"Broker error",progress:{complete:false,stopped:true}};
assert.equal(cache.save(storage,failed),false);
assert.equal(cache.read(storage).scan_id,"old");
assert.equal(cache.decision(old,fresh),"offer");
assert.equal(cache.decision({},fresh),"show");
assert.equal(cache.decision(fresh,fresh),"show");
assert.equal(cache.save(storage,{...fresh,access_token:"should-not-persist",vault:"secret"}),true);
assert.equal(cache.read(storage).scan_id,"new");
assert.equal(cache.read(storage).access_token,undefined);
assert.equal(cache.save(storage,old),false);
assert.equal(cache.save(storage,{...fresh,rows:[]}),true);
assert.deepEqual(cache.read(storage).rows,[]);
assert.equal(cache.save({setItem(){throw Error("quota")},getItem(){return null}},fresh),false);
cache.forget(storage);
assert.equal(cache.read(storage),null);
console.log("Market cache tests passed");
