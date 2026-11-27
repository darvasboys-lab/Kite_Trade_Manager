/* Pure weighted ranking. Uses cached data only; never starts market requests. */
(function(root){
  "use strict";
  const weights=Object.freeze({monthly_r1:20,ema20:15,ema50:10,ema200:5});
  const criteria=Object.keys(weights);
  const premiumWeight=50, premiumFullScoreAt=4;
  function premiumPoints(premium){
    if(typeof premium!=="number"||!Number.isFinite(premium)||premium<=0)return 0;
    return Math.round(Math.min(premium/premiumFullScoreAt,1)*premiumWeight*10)/10;
  }
  function rank(rows,selected=criteria){
    const active=criteria.filter(k=>selected.includes(k));
    if(!active.length)return [];
    const technicalMax=active.reduce((sum,k)=>sum+weights[k],0);
    const total=premiumWeight+technicalMax;
    return rows.flatMap(row=>{
      const premium=row["5%+ OTM CE (%)"];
      if(typeof premium!=="number"||!Number.isFinite(premium)||premium<=0)return [];
      const checks=row.bullish_checks||{};
      const passed=active.filter(k=>checks[k]===true).length;
      const technicalPoints=active.reduce((sum,k)=>sum+(checks[k]===true?weights[k]:0),0);
      const unknown=active.filter(k=>checks[k]!==true&&checks[k]!==false).length;
      const cePoints=premiumPoints(premium);
      const score=Math.round((technicalPoints+cePoints)*10)/10;
      return [{row,score,total,premium,cePoints,technicalPoints,technicalMax,passed,selectedCount:active.length,unknown}];
    }).sort((a,b)=>b.score-a.score||b.premium-a.premium||b.technicalPoints-a.technicalPoints||a.unknown-b.unknown||String(a.row.Stock).localeCompare(String(b.row.Stock)));
  }
  const api={criteria,weights,premiumWeight,premiumFullScoreAt,premiumPoints,rank};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  else root.CCSSmart=api;
})(typeof globalThis!=="undefined"?globalThis:this);
