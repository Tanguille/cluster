import json,sys,urllib.request,urllib.parse
W=sys.argv[1] if len(sys.argv)>1 else "1h"
def q(expr):
    u="http://127.0.0.1:18428/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    r=json.load(urllib.request.urlopen(u))["data"]["result"]
    return float(r[0]["value"][1]) if r else float("nan")
for m in ["vllm:kv_offload_lookup_async_delay_seconds","vllm:kv_offload_tiering_lookup_async_delay_seconds","vllm:request_queue_time_seconds","vllm:request_prefill_time_seconds","vllm:time_to_first_token_seconds","vllm:request_decode_time_seconds","vllm:e2e_request_latency_seconds"]:
    print(f"{m.split(':')[1]:45s}", end="")
    for p in (0.5,0.9,0.99):
        v=q(f"histogram_quantile({p}, sum by (le) (increase({m}_bucket[{W}])))")
        print(f" p{int(p*100)}={v:8.1f}", end="")
    print()
