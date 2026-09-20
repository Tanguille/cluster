import json,sys,urllib.request,urllib.parse
W=sys.argv[1] if len(sys.argv)>1 else "80m"
def qr(expr):
    u="http://127.0.0.1:18428/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    return json.load(urllib.request.urlopen(u))["data"]["result"]
def show(title,expr,keys):
    print(f"-- {title}")
    rows=[(x['metric'],float(x['value'][1])) for x in qr(expr)]
    rows=[r for r in rows if r[1]>0]; rows.sort(key=lambda r:-r[1])
    for m,v in rows[:20]:
        print(f"{v:10.0f}  "+"  ".join(f"{k}={m.get(k,'')}" for k in keys))
show("qwen-3.8 requests by user_agent / client_ip / endpoint", f'sum by (user_agent, client_ip, endpoint, requested_model) (increase(litellm_requests_metric_total{{model="qwen-3.8"}}[{W}]))', ["user_agent","client_ip","endpoint","requested_model"])
show("input tokens by user_agent", f'sum by (user_agent) (increase(litellm_input_tokens_metric_total{{model="qwen-3.8"}}[{W}]))', ["user_agent"])
show("output tokens by user_agent", f'sum by (user_agent) (increase(litellm_output_tokens_metric_total{{model="qwen-3.8"}}[{W}]))', ["user_agent"])
show("litellm ttft mean by user_agent", f'sum by (user_agent) (increase(litellm_llm_api_time_to_first_token_metric_sum{{model="qwen-3.8"}}[{W}])) / sum by (user_agent) (increase(litellm_llm_api_time_to_first_token_metric_count{{model="qwen-3.8"}}[{W}]))', ["user_agent"])
show("litellm total latency mean by user_agent", f'sum by (user_agent) (increase(litellm_request_total_latency_metric_sum{{model="qwen-3.8"}}[{W}])) / sum by (user_agent) (increase(litellm_request_total_latency_metric_count{{model="qwen-3.8"}}[{W}]))', ["user_agent"])
show("failed requests detail", f'sum by (requested_model, user_agent, exception_status, exception_class) (increase(litellm_proxy_failed_requests_metric_total[{W}]))', ["requested_model","user_agent","exception_status","exception_class"])
