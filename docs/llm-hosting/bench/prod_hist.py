import json,sys,urllib.request,urllib.parse
W=sys.argv[1] if len(sys.argv)>1 else "75m"
def qr(expr):
    u="http://127.0.0.1:18428/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    return json.load(urllib.request.urlopen(u))["data"]["result"]
for m in ["vllm:request_prompt_tokens","vllm:request_prefill_kv_computed_tokens","vllm:request_generation_tokens","vllm:request_max_num_generation_tokens"]:
    r=qr(f"sum by (le) (increase({m}_bucket[{W}]))")
    if not r: print(m,"none"); continue
    pts=sorted(((float('inf') if x['metric']['le']=='+Inf' else float(x['metric']['le'])),float(x['value'][1])) for x in r)
    tot=pts[-1][1]; prev=0
    if not tot: print(m,"empty window"); continue
    print(f"{m}  n={tot:.0f}")
    for le,c in pts:
        if c-prev>0: print(f"   <= {le:>10.0f}: {c-prev:5.0f}  ({100*(c-prev)/tot:4.1f}%)  cum {100*c/tot:5.1f}%")
        prev=c
