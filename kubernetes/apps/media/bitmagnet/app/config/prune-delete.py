"""Delete the selected hashes through bitmagnet's GraphQL API, which also blocklists them against re-crawling."""
import json
import urllib.request

hashes = open("/tmp/prune.txt").read().split()
# One call: every delete rewrites the whole blocklist bloom filter (a ~24 MB large object), so batching multiplies WAL.
body = json.dumps({"query": "mutation($h: [Hash20!]!) { torrent { delete(infoHashes: $h) } }",
                   "variables": {"h": hashes}}).encode()
request = urllib.request.Request("http://bitmagnet:3333/graphql", body, {"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=600) as response:
    result = json.load(response)
if result.get("errors"):
    raise RuntimeError(result["errors"])
print(f"deleted {len(hashes)} torrents")
