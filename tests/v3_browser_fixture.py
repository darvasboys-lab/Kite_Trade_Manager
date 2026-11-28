from dhan_visual import workspace_store as store
data=store.read()
data["snapshot"]={"saved_at":"2026-11-27T15:25:00+05:30","complete":True,
 "filters":{"maximum":7,"minimum":0,"expiry":"DEC-26","r1_filter":"Include all stocks"},
 "rows":[{"Stock":"TCS","CMP (₹)":1000,"Lot size":100,"Contract size (₹)":100000,
 "CE strike":1052.5,"CE premium (₹)":30,"5%+ OTM CE (%)":3,"Straddle (%)":7.5,
 "ATM strike":1000,"ATM CE premium (₹)":40,"ATM PE premium (₹)":35},
 {"Stock":"SAIL","CMP (₹)":100,"Lot size":1000,"Contract size (₹)":100000,
 "CE strike":105,"CE premium (₹)":2,"5%+ OTM CE (%)":2,"Straddle (%)":6}]}
store.write(data)
