import re,sys,statistics as st
rx=re.compile(r'(\S+) .*prompt throughput: ([\d.]+) tokens/s, Avg generation throughput: ([\d.]+) tokens/s, Running: (\d+) reqs, Waiting: (\d+) reqs(?:, Deferred: (\d+) reqs)?, GPU KV cache usage: ([\d.]+)%')
rows=[]
for l in open(sys.argv[1]):
    m=rx.search(l)
    if m: rows.append((m.group(1),float(m.group(2)),float(m.group(3)),int(m.group(4)),int(m.group(5)),int(m.group(6) or 0),float(m.group(7))))
n=len(rows); print("windows(10s):",n, "span min:", n/6)
busy=[r for r in rows if r[3]>=1]
if not busy: sys.exit("no windows with running>=1 in this log")
stall=[r for r in busy if r[2]<5 and r[1]<50]
print(f"windows with running>=1: {len(busy)}  of which gen<5 tok/s AND prompt<50 tok/s (stalled): {len(stall)} = {100*len(stall)/len(busy):.0f}%")
print(f"stalled windows with deferred>0: {sum(1 for r in stall if r[5]>0)}   waiting>0: {sum(1 for r in stall if r[4]>0)}")
# stall run lengths
runs=[];cur=0
for r in rows:
    s=r[3]>=1 and r[2]<5 and r[1]<50
    if s: cur+=1
    elif cur: runs.append(cur); cur=0
if cur: runs.append(cur)
print("stall runs (x10s):",sorted(runs,reverse=True)[:15], " total runs",len(runs), " median",st.median(runs) if runs else 0)
gen=[r[2] for r in busy if r[2]>=5]
print(f"gen tok/s in non-stalled busy windows: mean {st.mean(gen):.1f} median {st.median(gen):.1f} max {max(gen):.1f}")
pt=[r[1] for r in rows if r[1]>0]
print(f"prompt tok/s in windows with prefill: mean {st.mean(pt):.0f} median {st.median(pt):.0f} max {max(pt):.0f} n={len(pt)}")
print(f"running mean {st.mean(r[3] for r in rows):.2f}  waiting mean {st.mean(r[4] for r in rows):.2f}  deferred mean {st.mean(r[5] for r in rows):.2f}  max waiting {max(r[4] for r in rows)}")
print(f"kv usage mean {st.mean(r[6] for r in rows):.1f} max {max(r[6] for r in rows):.1f}")
print(f"total gen tokens (sum gen*10s) {sum(r[2]*10 for r in rows):.0f}; if stalls removed at median busy rate: stalled seconds {len(stall)*10}")
