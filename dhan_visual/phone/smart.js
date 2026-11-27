/* Pure ranking, shared by Home and Screener. Never starts market requests. */
(function(root){
  "use strict";
  const criteria=["ema200","ema50","ema20","monthly_r1"];
  function rank(rows,selected=criteria){
    const active=criteria.filter(k=>selected.includes(k));
    if(!active.length)return [];
    return rows.flatMap(row=>{
      const premium=row["5%+ OTM CE (%)"];
      if(typeof premium!=="number"||!Number.isFinite(premium)||premium<=0)return [];
      const checks=row.bullish_checks||{};
      const score=active.filter(k=>checks[k]===true).length;
      const unknown=active.filter(k=>checks[k]!==true&&checks[k]!==false).length;
      return [{row,score,unknown,premium,total:active.length}];
    }).sort((a,b)=>b.score-a.score||b.premium-a.premium||a.unknown-b.unknown||String(a.row.Stock).localeCompare(String(b.row.Stock)));
  }
  const api={criteria,rank};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  else root.CCSSmart=api;
})(typeof globalThis!=="undefined"?globalThis:this);
