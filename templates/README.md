# Templates

Ready-to-edit starting points. Copy one, change the marked lines, run it.

Mornye ships with **no provider and no default extension source**: every
template below needs `MORNYE_REGISTRIES` set to a registry *you* choose, and
its `SERVICES` list changed to the ids of the extensions that registry
installs. See [Extensions](../docs/guide/extensions.md).

| File | For |
| --- | --- |
| [`download.py`](download.py) | Download one link (track, album, playlist, artist) from a script, then exit |
| [`nas_sync.py`](nas_sync.py) + [`links.txt`](links.txt) | A NAS or home server: keep a list of playlists and albums downloaded, re-checking every few hours, headless |
| [`mornye-web.service`](mornye-web.service) | The web interface as a systemd service on a headless Linux machine |
| [`../docker-compose.example.yml`](../docker-compose.example.yml) | The web interface in Docker, built from source |
| [`../docker-compose.portainer.yml`](../docker-compose.portainer.yml) | The published image with every setting wired up (Portainer, Synology, Unraid…) |
| [`../.env.example`](../.env.example) | Every environment variable the Docker setups read |

```bash
pip install mornye
export MORNYE_REGISTRIES="https://example.com/your-registry.json"

python download.py "https://open.spotify.com/album/..." ~/Music
python nas_sync.py links.txt /volume1/music --every 6
```

The full Python API is in the [API Reference](../docs/guide/api-reference.md);
the Docker setup in [Docker](../docs/guide/docker.md).
