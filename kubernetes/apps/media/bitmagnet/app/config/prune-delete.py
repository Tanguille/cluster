"""Delete the selected hashes through bitmagnet's GraphQL API, which also blocklists them against re-crawling."""
import json
import urllib.request

hashes = open("/tmp/prune.txt").read().split()
# Every call rewrites the whole blocklist bloom filter (a ~24 MB large object), so chunks stay large: a day's
# prune is one call, and a rule change's backlog goes in a few calls that each fit the timeout.
CHUNK = 20000
for i in range(0, len(hashes), CHUNK):
    body = json.dumps({"query": "mutation($h: [Hash20!]!) { torrent { delete(infoHashes: $h) } }",
                       "variables": {"h": hashes[i:i + CHUNK]}}).encode()
    request = urllib.request.Request("http://bitmagnet:3333/graphql", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError(result["errors"])
    print(f"deleted {min(i + CHUNK, len(hashes))}/{len(hashes)} torrents", flush=True)
