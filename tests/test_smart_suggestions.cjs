const assert=require("node:assert/strict");
const {rank,criteria}=require("../dhan_visual/phone/smart.js");
function row(Stock,premium,flags){
 return {Stock,"5%+ OTM CE (%)":premium,bullish_checks:Object.fromEntries(criteria.map((k,i)=>[k,flags[i]]))};
}
const rows=[row("A",3,[true,true,true,true]),row("B",7,[true,true,true,false]),row("C",5,[true,true,true,true]),row("D",8,[true,null,false,false]),row("E",99,[false,false,false,false]),row("F",null,[true,true,true,true]),row("G",NaN,[true,true,true,true])];
assert.deepEqual(rank(rows).map(x=>x.row.Stock),["C","A","B","D","E"]);
assert.equal(rank(rows)[3].unknown,1);
assert.equal(rank(rows)[3].score,1);
assert.deepEqual(rank(rows,["ema200"]).map(x=>x.row.Stock),["D","B","C","A","E"]);
assert.deepEqual(rank(rows,[]),[]);
assert.equal(rank([row("X",5,[null,null,null,null])])[0].score,0);
console.log("Smart Suggestions rank tests passed");
