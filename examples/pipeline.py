"""Spotify metadata pipeline: fetch -> parse -> normalise -> store.

    python pipeline.py spotify:album:4hF2gTGuPYlykYuphDxi8J \\
        https://open.spotify.com/playlist/37i9dQZF1DX4UtSsGT1Sbe \\
        --stats --db spotify.db --csv-dir out/

Every reference costs 1 credit through the embed route. --stats adds a
rendered fetch (25 credits) for artists and playlists, which is where
monthly listeners, followers, play counts and the true playlist length
come from. Albums and tracks have nothing extra to gain from --stats.

Verified live on 2026-10-29. Set SCRAPINGBEE_API_KEY before running.
"""

import argparse
import json
import os
import sys

from fetch import SpotifyFetcher, parse_ref
from normalise import artist_stats, from_embed, playlist_stats
from parse import ParseError, embed_entity, pathfinder, pathfinder_all
from store import Store


def run_one(fetcher, ref, with_stats):
    kind, sid = parse_ref(ref)
    page = fetcher.fetch_embed(kind, sid)
    entity, tracks = from_embed(embed_entity(page.body))

    if with_stats and kind in ("artist", "playlist"):
        rendered = fetcher.fetch_rendered(kind, sid)
        if kind == "artist":
            overview = pathfinder(rendered.xhr, "queryArtistOverview")
            if overview:
                tracks = artist_stats(entity, overview)
        else:
            pages = pathfinder_all(rendered.xhr, "fetchPlaylist") + \
                pathfinder_all(rendered.xhr, "fetchPlaylistContents")
            if pages:
                richer = playlist_stats(entity, pages)
                # Keep the embed's longer list, borrow stats for rows we have.
                by_pos = {t.position: t for t in richer}
                for t in tracks:
                    extra = by_pos.get(t.position)
                    if extra and extra.track_id == t.track_id:
                        t.playcount, t.bpm, t.camelot_key = extra.playcount, extra.bpm, extra.camelot_key
        if not any("pathfinder" in rec.get("url", "") for rec in rendered.xhr):
            print(f"  warning: no pathfinder XHR captured for {kind} {sid}", file=sys.stderr)
    return entity, tracks


def main(argv=None):
    ap = argparse.ArgumentParser(description="Spotify public page metadata through ScrapingBee")
    ap.add_argument("refs", nargs="+", help="spotify: URIs or open.spotify.com URLs")
    ap.add_argument("--stats", action="store_true", help="add the 25 credit rendered fetch for artists and playlists")
    ap.add_argument("--db", default="spotify.db")
    ap.add_argument("--csv-dir", help="write entities.csv and tracks.csv here")
    ap.add_argument("--jsonl-dir", help="write entities.jsonl and tracks.jsonl here")
    args = ap.parse_args(argv)

    fetcher = SpotifyFetcher()
    store = Store(args.db)
    for ref in args.refs:
        try:
            entity, tracks = run_one(fetcher, ref, args.stats)
        except (ParseError, ValueError) as exc:
            print(f"skip {ref}: {exc}", file=sys.stderr)
            continue
        store.save(entity, tracks)
        flag = " (list may be cut short)" if entity.maybe_truncated else ""
        extra = ""
        if entity.monthly_listeners:
            extra = f", {entity.monthly_listeners:,} monthly listeners, rank {entity.world_rank}"
        elif entity.followers:
            extra = f", {entity.followers:,} saves"
        total = f"/{entity.track_count_total}" if entity.track_count_total else ""
        print(f"{entity.kind:8} {entity.name}: {entity.track_count_seen}{total} tracks{extra}{flag}")

    for fmt, folder in (("csv", args.csv_dir), ("jsonl", args.jsonl_dir)):
        if folder:
            os.makedirs(folder, exist_ok=True)
            for table in ("entities", "tracks"):
                path = os.path.join(folder, f"{table}.{fmt}")
                getattr(store, f"export_{fmt}")(table, path)
                print(f"wrote {path}")
    print(json.dumps({"credits": fetcher.credits_spent}))


if __name__ == "__main__":
    main()
