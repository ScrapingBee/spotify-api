"""Stage 3 of the pipeline: turn page JSON into typed, flat records.

Two sources feed the same record types:

* embed entities (1 credit): name, type, release date, artists, duration,
  explicit flag, and the track list
* pathfinder responses (25 credits): artist followers, monthly listeners,
  world rank, top cities, per-track play counts, playlist saves, and the
  real number of tracks in a playlist

Nothing here keeps audio. The embed's trackList carries an audioPreview
URL for every track; normalisation drops it, and the pipeline never
requests p.scdn.co.
"""

import html
from dataclasses import asdict, dataclass, field
from typing import Optional

EMBED_TRACK_CAP = 100  # the embed never listed more than 100 rows in testing


def _sid(uri):
    return uri.rsplit(":", 1)[-1] if uri else None


def _date(obj):
    if not obj:
        return None
    iso = obj.get("isoString")
    if iso:
        return iso[:10]
    if obj.get("year"):
        return f"{obj['year']:04d}-{obj.get('month') or 1:02d}-{obj.get('day') or 1:02d}"
    return None


def _largest_image(sources):
    sized = [s for s in sources or [] if s.get("url")]
    if not sized:
        return None
    return max(sized, key=lambda s: (s.get("width") or s.get("maxWidth") or 0))["url"]


@dataclass
class Track:
    track_id: str
    name: str
    artists: str
    duration_s: float
    explicit: bool
    position: int
    parent_id: str            # album, playlist or artist the row came from
    playcount: Optional[int] = None
    bpm: Optional[float] = None
    camelot_key: Optional[str] = None


@dataclass
class Entity:
    kind: str
    spotify_id: str
    name: str
    subtitle: Optional[str]
    release_date: Optional[str]
    image: Optional[str]
    track_count_seen: int
    track_count_total: Optional[int] = None
    maybe_truncated: bool = False
    followers: Optional[int] = None
    monthly_listeners: Optional[int] = None
    world_rank: Optional[int] = None
    top_cities: list = field(default_factory=list)
    description: Optional[str] = None


def from_embed(entity):
    """Embed entity -> (Entity, [Track])."""
    kind = entity["type"]
    sid = entity["id"]
    if kind == "track":
        artists = ", ".join(a["name"] for a in entity.get("artists", []))
        track = Track(sid, entity["name"], artists, round(entity["duration"] / 1000, 1),
                      bool(entity.get("isExplicit")), 1, sid)
        rows = [track]
    else:
        rows = [
            Track(_sid(t["uri"]), t["title"], t.get("subtitle", ""), round(t["duration"] / 1000, 1),
                  bool(t.get("isExplicit")), i, sid)
            for i, t in enumerate(entity.get("trackList", []), 1)
        ]
    images = (entity.get("visualIdentity") or {}).get("image") or (entity.get("coverArt") or {}).get("sources")
    ent = Entity(
        kind=kind,
        spotify_id=sid,
        name=entity["name"],
        subtitle=entity.get("subtitle"),
        release_date=_date(entity.get("releaseDate")),
        image=_largest_image(images),
        track_count_seen=len(rows),
        # An artist embed is a top-tracks list (10 rows), not a discography,
        # so only playlists and albums can be cut short.
        maybe_truncated=kind in ("playlist", "album") and len(rows) >= EMBED_TRACK_CAP,
    )
    return ent, rows


def artist_stats(ent, overview):
    """Merge queryArtistOverview into an artist Entity, return top tracks."""
    a = overview["artistUnion"]
    stats = a.get("stats") or {}
    ent.followers = stats.get("followers")
    ent.monthly_listeners = stats.get("monthlyListeners")
    ent.world_rank = stats.get("worldRank") or None
    ent.top_cities = [
        {"city": c["city"], "country": c["country"], "listeners": c["numberOfListeners"]}
        for c in (stats.get("topCities") or {}).get("items", [])
    ]
    bio = ((a.get("profile") or {}).get("biography") or {}).get("text")
    ent.description = html.unescape(bio) if bio else None
    rows = []
    for i, item in enumerate(a["discography"]["topTracks"]["items"], 1):
        t = item["track"]
        rows.append(Track(
            t["id"], t["name"], ", ".join(x["profile"]["name"] for x in t["artists"]["items"]),
            round(t["duration"]["totalMilliseconds"] / 1000, 1),
            t.get("contentRating", {}).get("label") == "EXPLICIT", i, ent.spotify_id,
            playcount=int(t["playcount"]) if t.get("playcount") else None,
        ))
    return rows


def playlist_stats(ent, pages):
    """Merge fetchPlaylist / fetchPlaylistContents pages into a playlist Entity.

    The rendered player loads 25 rows, then 50 more, and stops until you
    scroll. So these rows are the first 75 at best, but they carry play
    counts, BPM and key, which the embed does not.
    """
    rows, offset_seen = [], set()
    for variables, data in pages:
        p = data["playlistV2"]
        if p.get("followers") is not None:
            ent.followers = p["followers"]
            ent.description = p.get("description")
        content = p.get("content") or {}
        ent.track_count_total = content.get("totalCount", ent.track_count_total)
        offset = variables.get("offset", 0)
        if offset in offset_seen:
            continue
        offset_seen.add(offset)
        for i, item in enumerate(content.get("items", []), offset + 1):
            t = (item.get("itemV2") or {}).get("data") or {}
            if t.get("__typename") != "Track":
                continue
            attrs = t.get("playlistAudioAttributes") or {}
            rows.append(Track(
                _sid(t["uri"]), t["name"], ", ".join(x["profile"]["name"] for x in t["artists"]["items"]),
                round(t["trackDuration"]["totalMilliseconds"] / 1000, 1),
                t.get("contentRating", {}).get("label") == "EXPLICIT", i, ent.spotify_id,
                playcount=int(t["playcount"]) if t.get("playcount") else None,
                bpm=round(attrs["bpm"], 1) if attrs.get("bpm") else None,
                camelot_key=((attrs.get("key") or {}).get("camelotKey") or {}).get("value"),
            ))
    if ent.track_count_total is not None:
        ent.maybe_truncated = ent.track_count_seen < ent.track_count_total
    return sorted(rows, key=lambda r: r.position)


def as_dict(record):
    return asdict(record)
