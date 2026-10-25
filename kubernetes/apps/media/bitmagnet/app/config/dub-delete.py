"""Delete the selected hashes through bitmagnet's GraphQL API, which also blocklists them against re-crawling."""
import json
import os
import urllib.request

URL = os.environ.get("BITMAGNET_GRAPHQL_URL", "http://bitmagnet:3333/graphql")
MUTATION = "mutation($h: [Hash20!]!) { torrent { delete(infoHashes: $h) } }"

hashes = open(os.environ.get("DUB_HASHES", "/tmp/dubs.txt")).read().split()
for i in range(0, len(hashes), 100):
    body = json.dumps({"query": MUTATION, "variables": {"h": hashes[i:i + 100]}}).encode()
    request = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError(result["errors"])
print(f"deleted {len(hashes)} dubs")
