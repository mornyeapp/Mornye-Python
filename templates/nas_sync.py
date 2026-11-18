"""Keep a list of playlists and albums downloaded, headless, forever.

Made for a NAS or a home server: it reads the links from a text file (one
per line, `#` for comments), downloads what is missing, sleeps, and starts
again. Tracks already on disk are skipped without contacting any provider,
so each pass after the first only fetches what is new.

    pip install mornye
    export MORNYE_REGISTRIES="https://example.com/your-registry.json"
    python nas_sync.py links.txt /volume1/music --every 6

Run it under systemd, Docker, tmux or `nohup`; stop it with Ctrl+C.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from mornye import AsyncMornye

# The extensions to try, in order. Change these to the ids your registry has.
SERVICES = ["ext:tidal-web", "ext:qobuz-web"]


def read_links(path: Path) -> list[str]:
    links = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            links.append(line)
    return links


async def sync_forever(links_file: Path, output_dir: str, hours: float) -> None:
    async with AsyncMornye(
        output_dir=output_dir,
        services=SERVICES,
        quality="LOSSLESS",
        create_playlist_subfolders=True,
        use_artist_subfolders=True,
        use_album_subfolders=True,
        track_max_retries=1,
        log_level=logging.INFO,
    ) as client:
        while True:
            # Read again every pass: edit the file and the next pass sees it.
            links = read_links(links_file)
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] syncing {len(links)} link(s)")
            try:
                await client.download_batch(links)
            except Exception as exc:  # one bad pass must not end the loop
                print(f"pass failed: {exc}")
            if hours <= 0:
                return
            print(f"next pass in {hours:g} hour(s)")
            await asyncio.sleep(hours * 3600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("links_file", type=Path, help="text file, one link per line")
    parser.add_argument("output_dir", help="where the music goes")
    parser.add_argument(
        "--every",
        type=float,
        default=6,
        metavar="HOURS",
        help="hours between passes (default 6; 0 = run once and exit)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(sync_forever(args.links_file, args.output_dir, args.every))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
