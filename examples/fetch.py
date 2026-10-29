"""Stage 1 of the pipeline: get raw Spotify pages through ScrapingBee.

Two fetchers, because open.spotify.com behaves like two different sites:

* fetch_embed() asks for /embed/<kind>/<id>. That route is a Next.js page
  that ships its data in __NEXT_DATA__, so a classic request with
  render_js=false is enough. Measured cost: 1 credit.
* fetch_rendered() asks for the normal web player page. Its server HTML is
  an empty shell (title "Spotify - Web Player", no data) on every tier we
  tried without a browser. Only premium_proxy plus render_js filled it, and
  json_response=true hands back the GraphQL XHR bodies the player made.
  Measured cost: 25 credits.

Verified live on 2026-10-29. Set SCRAPINGBEE_API_KEY before running.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field

import requests

API = "https://app.scrapingbee.com/api/v1/"
KINDS = ("artist", "album", "track", "playlist")

URI_RE = re.compile(r"spotify:(artist|album|track|playlist):([A-Za-z0-9]{22})")
URL_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:embed/)?(artist|album|track|playlist)/([A-Za-z0-9]{22})")


@dataclass
class RawPage:
    kind: str
    spotify_id: str
    route: str               # "embed" or "rendered"
    status: int
    credits: int
    body: str = ""            # HTML for embed pages
    xhr: list = field(default_factory=list)  # XHR records for rendered pages


def parse_ref(ref):
    """Accept a spotify: URI or an open.spotify.com URL, return (kind, id)."""
    m = URI_RE.search(ref) or URL_RE.search(ref)
    if not m:
        raise ValueError(f"not a Spotify artist/album/track/playlist reference: {ref}")
    return m.group(1), m.group(2)


class SpotifyFetcher:
    def __init__(self, api_key=None, retries=3, timeout=180):
        key = api_key or os.environ.get("SCRAPINGBEE_API_KEY")
        if not key:
            raise SystemExit("set SCRAPINGBEE_API_KEY")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {key}"
        self.retries = retries
        self.timeout = timeout
        self.credits_spent = 0

    def _call(self, params):
        # ScrapingBee answers 500 when the target times out or refuses.
        # Those calls are not billed, so a short backoff and retry is free.
        last = None
        for attempt in range(self.retries):
            r = self.session.get(API, params=params, timeout=self.timeout)
            if r.status_code != 500:
                break
            last = r
            time.sleep(2 * (attempt + 1))
        else:
            r = last
        cost = int(r.headers.get("spb-cost", 0) or 0)
        self.credits_spent += cost
        r.raise_for_status()
        return r, cost

    def fetch_embed(self, kind, spotify_id):
        """1 credit. Works for all four kinds."""
        r, cost = self._call({
            "url": f"https://open.spotify.com/embed/{kind}/{spotify_id}",
            "render_js": "false",
        })
        return RawPage(kind, spotify_id, "embed", r.status_code, cost, body=r.text)

    def fetch_rendered(self, kind, spotify_id, wait_ms=8000):
        """25 credits. Use it only for what the embed lacks: artist stats
        (monthly listeners, followers, world rank, top cities, play counts)
        and a playlist's real track total and save count."""
        r, cost = self._call({
            "url": f"https://open.spotify.com/{kind}/{spotify_id}",
            "premium_proxy": "true",
            "render_js": "true",
            "block_resources": "false",
            "wait": str(wait_ms),
            "json_response": "true",
        })
        data = r.json()
        return RawPage(kind, spotify_id, "rendered", r.status_code, cost,
                       body=data.get("body", ""), xhr=data.get("xhr", []))


if __name__ == "__main__":
    import sys

    kind, sid = parse_ref(sys.argv[1] if len(sys.argv) > 1 else "spotify:album:4hF2gTGuPYlykYuphDxi8J")
    page = SpotifyFetcher().fetch_embed(kind, sid)
    has_data = "__NEXT_DATA__" in page.body
    print(json.dumps({"kind": kind, "id": sid, "status": page.status, "credits": page.credits,
                      "bytes": len(page.body), "has_next_data": has_data}))
