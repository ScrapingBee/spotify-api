"""Stage 2 of the pipeline: pull the JSON out of what fetch.py returned.

Embed pages carry one script tag:

    <script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{...

and the record lives at props.pageProps.state.data.entity. The same state
object also holds settings.session with an anonymous accessToken. This
module drops it on purpose: the pipeline reads what the page shows and does
not call Spotify's private endpoints with a borrowed token.

Rendered pages come back as a list of XHR records. The useful ones are POSTs
to api-partner.spotify.com/pathfinder/v2/query, told apart by the
operationName in their request body (queryArtistOverview, fetchPlaylist,
fetchPlaylistContents).
"""

import json
import re

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class ParseError(ValueError):
    pass


def next_data(html):
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ParseError("no __NEXT_DATA__ script, the page is probably the player shell")
    return json.loads(m.group(1))


def embed_entity(html):
    """Return the entity dict from an embed page, without the session block."""
    state = next_data(html)["props"]["pageProps"]["state"]
    entity = state.get("data", {}).get("entity")
    if not entity:
        raise ParseError("embed page has no data.entity")
    return entity


def pathfinder_ops(xhr):
    """Yield (operationName, variables, data) for every pathfinder XHR."""
    for rec in xhr:
        if "api-partner.spotify.com/pathfinder" not in rec.get("url", ""):
            continue
        try:
            req = json.loads(rec.get("post_data") or "{}")
            body = json.loads(rec.get("body") or "{}")
        except json.JSONDecodeError:
            continue
        if "data" in body:
            yield req.get("operationName"), req.get("variables", {}), body["data"]


def pathfinder(xhr, operation):
    """First response for one operation, or None if the player never sent it."""
    for name, _vars, data in pathfinder_ops(xhr):
        if name == operation:
            return data
    return None


def pathfinder_all(xhr, operation):
    return [(v, d) for name, v, d in pathfinder_ops(xhr) if name == operation]
