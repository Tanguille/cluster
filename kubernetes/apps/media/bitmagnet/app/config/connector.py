"""Run the pinned upstream connector with a search prediction cache and text-only Torznab caps.

The Torznab proxy re-classifies every result on every search, and Prowlarr's RSS sync keeps
re-fetching the same newest items. The key holds every input that moves the prediction (name,
file count, seeder bucket), so a changed torrent or swarm is re-classified and new ones always are.
Only searches use the cache: the poller sees each torrent once, with real file lists that never
match a Torznab row's key.
bitmagnet answers id searches only from TMDB-matched torrents, so caps advertise title search only.
"""
from collections import OrderedDict
import importlib.util
import re
import threading
import time
import urllib.parse
import urllib.request

TTL = 24 * 3600
MAX_ENTRIES = 10_000


def _seeder_bucket(row):
    seeders = int((row.get("swarm") or {}).get("tracker_seeders") or 0)
    return next(i for i, cap in enumerate((0, 2, 9, 49, float("inf"))) if seeders <= cap)


def _key(row):
    infohash = str(row.get("infohash") or "").lower()
    if not infohash:
        return None
    return infohash, row.get("name"), len(row.get("files") or ()), _seeder_bucket(row)


def cached(run):
    """Wrap `run(ctx, rows) -> preds` with a thread-safe LRU of predictions; `ctx` passes through."""
    cache = OrderedDict()
    lock = threading.Lock()

    def wrapper(ctx, rows):
        now = time.monotonic()
        keys = [_key(row) for row in rows]
        out = [None] * len(rows)
        with lock:
            for i, key in enumerate(keys):
                hit = cache.get(key) if key else None
                if hit and now - hit[0] < TTL:
                    cache.move_to_end(key)
                    out[i] = hit[1]
        misses = [i for i, pred in enumerate(out) if pred is None]
        if misses:
            preds = run(ctx, [rows[i] for i in misses])
            assert len(preds) == len(misses), "upstream changed the batch inference contract"
            with lock:
                for i, pred in zip(misses, preds):
                    out[i] = pred
                    if keys[i]:
                        cache[keys[i]] = (now, pred)
                        cache.move_to_end(keys[i])
                while len(cache) > MAX_ENTRIES:
                    cache.popitem(last=False)
        return out

    return wrapper


ID_PARAMS = {b"imdbid", b"tmdbid", b"tvdbid"}


def strip_id_params(caps_xml):
    """Drop ID_PARAMS from every supportedParams list."""
    return re.sub(
        rb'supportedParams="([^"]*)"',
        lambda m: b'supportedParams="%s"' % b",".join(p for p in m.group(1).split(b",") if p not in ID_PARAMS),
        caps_xml,
    )


def text_only_caps(create_server):
    """Wrap `create_server(connector, ...)` so `t=caps` is answered with id params removed."""

    def wrapper(connector, *args, **kwargs):
        server = create_server(connector, *args, **kwargs)
        handler = server.RequestHandlerClass
        do_get = handler.do_GET

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if (urllib.parse.parse_qs(url.query).get("t") or [""])[0].lower() != "caps":
                return do_get(self)
            try:
                upstream = f"{connector.upstream_torznab_url}{url.path.removeprefix('/torznab')}?{url.query}"
                with urllib.request.urlopen(upstream, timeout=15) as resp:
                    body = strip_id_params(resp.read())
                    content_type = resp.headers.get("Content-Type", "application/xml")
            except OSError:
                return do_get(self)  # upstream's handler owns the 502 response
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        handler.do_GET = do_GET
        return server

    return wrapper


if __name__ == "__main__":
    spec = importlib.util.spec_from_file_location("bitmagnet_connector", "/data/armiger/runtime/bitmagnet_connector.py")
    connector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(connector)
    # filter_torznab_xml looks the feed filter up at call time; fails loudly at startup if upstream renames it.
    # span_bio (per-byte span tags, about half of each prediction) is never read on the search path.
    infer = cached(lambda classify, rows: [{**pred, "span_bio": None} for pred in classify(rows)])
    feed = connector.filter_torznab_feed
    connector.filter_torznab_feed = lambda xml, extract, classify, *rest: feed(
        xml, extract, lambda rows: infer(classify, rows), *rest)
    connector.create_server = text_only_caps(connector.create_server)
    connector.main()
