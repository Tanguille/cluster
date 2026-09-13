import json,sys,urllib.request,urllib.parse
W=sys.argv[1] if len(sys.argv)>1 else "1h"
def q(expr):
    u="http://127.0.0.1:18428/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    r=json.load(urllib.request.urlopen(u))["data"]["result"]
    return float(r[0]["value"][1]) if r else float("nan")
def inc(m): return q(f'sum(increase({m}[{W}]))')
def mean(m): return inc(m+"_sum")/inc(m+"_count")
n=inc("vllm:request_success_total")
print(f"window {W}  requests {n:.0f}")
print(f"prompt tokens/req      {inc('vllm:prompt_tokens_total')/n:9.0f}")
print(f"cached prompt tok/req  {inc('vllm:prompt_tokens_cached_total')/n:9.0f}")
print(f"gen tokens/req         {inc('vllm:generation_tokens_total')/n:9.0f}")
print(f"prefix hit (gpu)       {inc('vllm:prefix_cache_hits_total')/inc('vllm:prefix_cache_queries_total')*100:8.1f}%")
print(f"external prefix hit    {inc('vllm:external_prefix_cache_hits_total')/inc('vllm:external_prefix_cache_queries_total')*100:8.1f}%  (hits/req {inc('vllm:external_prefix_cache_hits_total')/n:.0f} tok)")
print(f"tiering chunk hit      {inc('vllm:kv_offload_tiering_chunk_hits_total')/inc('vllm:kv_offload_tiering_chunk_queries_total')*100:8.1f}%")
print("-- seconds per request (mean) --")
for lbl,m in [("queue","vllm:request_queue_time_seconds"),("prefill","vllm:request_prefill_time_seconds"),("ttft","vllm:time_to_first_token_seconds"),("decode","vllm:request_decode_time_seconds"),("inference","vllm:request_inference_time_seconds"),("e2e","vllm:e2e_request_latency_seconds"),("tpot","vllm:request_time_per_output_token_seconds")]:
    try: print(f"{lbl:12s}{mean(m):9.2f}")
    except Exception as e: print(lbl,"n/a")
print(f"{'kv computed tok/req':22s}{mean('vllm:request_prefill_kv_computed_tokens'):9.0f}")
print("-- kv offload --")
lt=inc("vllm:kv_offload_load_time_total"); lc=inc("vllm:kv_offload_load_size_count"); lb=inc("vllm:kv_offload_load_bytes_total")
print(f"loads {lc:.0f}  load time total {lt:.1f}s  per req {lt/n:.2f}s  bytes/req {lb/n/2**30:.2f} GiB  GiB/s {lb/2**30/lt if lt else 0:.2f}")
tr=inc("vllm:kv_offload_tiering_read_time_total"); tb=inc("vllm:kv_offload_tiering_read_bytes_total")
print(f"tier read time total {tr:.1f}s per req {tr/n:.2f}s bytes/req {tb/n/2**30:.2f} GiB")
for m in ["vllm:kv_offload_lookup_sync_delay_seconds","vllm:kv_offload_lookup_async_delay_seconds","vllm:kv_offload_tiering_lookup_sync_delay_seconds","vllm:kv_offload_tiering_lookup_async_delay_seconds"]:
    try: print(f"{m.split(':')[1]:45s} n={inc(m+'_count'):.0f} mean {mean(m):.3f}s total {inc(m+'_sum'):.1f}s")
    except: pass
st=inc("vllm:kv_offload_store_time_total"); print(f"store time total {st:.1f}s per req {st/n:.2f}s")
