"""Download one link (track, album, playlist or artist) and exit.

    pip install mornye
    export MORNYE_REGISTRIES="https://example.com/your-registry.json"
    python download.py "https://open.spotify.com/playlist/..." ~/Music

Mornye ships with no provider: the registry above is the extension source
*you* choose (see docs/guide/extensions.md). Its extensions are installed on
the first run, and the services below must match their ids.
"""

from __future__ import annotations

import sys

from mornye import Mornye

# The extensions to try, in order. Change these to the ids your registry has.
SERVICES = ["ext:tidal-web", "ext:qobuz-web"]


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python download.py URL [OUTPUT_DIR]")
    url = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./downloads"

    Mornye(
        url=url,
        output_dir=output_dir,
        services=SERVICES,
        quality="LOSSLESS",  # or HI_RES_LOSSLESS
        create_playlist_subfolders=True,  # one folder (and M3U) per playlist
        use_artist_subfolders=True,
        use_album_subfolders=True,
    )


if __name__ == "__main__":
    main()
