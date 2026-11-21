# Personal VR Browser

A small, self-hosted VR library browser for personal use. The interface is a
plain file listing with search, sorting, cover previews and individual file
downloads. It uses Python's standard library on the server and vanilla HTML,
CSS and JavaScript in the browser.

This project is independent of VR CyberDeck. It supports a compatible metadata
format but does not include CyberDeck, its credentials, or a copy of the vrSrc
catalog. Access to third-party services and distribution of game files require
permission from the relevant provider and rights holders.

## Running locally

Requires Python 3.12 or newer. No `pip install` step is necessary.

```sh
python3 app.py serve --demo
```

Open <http://127.0.0.1:4010/>. Demo mode displays a small fictional catalog and
does not contact third-party services.

Run the tests with:

```sh
python3 -m unittest discover -s tests -v
```

## Catalog and downloads

The application reads the following files from its data directory:

```text
data/
  catalog.json
  covers/
    org.example.app.jpg
  packages/
    org.example.app/
      example.zip
```

Import a local, authorized semicolon-delimited game list:

```sh
python3 import_catalog.py /path/to/GameList.txt --data-dir ./data
python3 app.py --data-dir ./data serve
```

`import_covers.py` is an optional administrator tool for copying matching JPGs
from a previously extracted metadata bundle. Its default destination is
`/srv/storage/vr-library`, and it requires that a catalog is already present.
Do not commit metadata archives, game binaries, or user-specific data.

Files placed in `packages/<package-id>/` become individually downloadable.
Only archive and app extensions recognized by the server are listed. There is
no download-all endpoint.

## Deployment

`install-ubuntu.sh` installs a dedicated systemd service on Ubuntu, bound to
`127.0.0.1:4010`. It expects the code at `/opt/vialenne-vr` and data at
`/srv/storage/vr-library`. Configure your HTTPS reverse proxy separately and
add authentication before allowing access to private files.

The code includes an experimental adapter for a single configured metadata
source. It reads `/etc/vialenne-vr/ServerInfo.json` on the server, never from
browser input. It does **not** bundle a provider-issued API key, and it will
not bypass upstream access restrictions. Automatic upstream synchronization
will fail against services that require additional authorization. Remote
file listing and relay are disabled unless the administrator explicitly sets
`allowDownloads` in that config; enabling it does not grant permission to use
a provider's content.

The repository intentionally omits server configuration, game files,
credentials, cached catalogs and cover images.

## API

- `GET /api/status` — library and sync status
- `GET /api/games` — paginated catalog with search and sorting
- `GET /api/games/<package>` — game details and available files
- `GET /covers/<package>` — cached artwork
- `GET /api/download/<package>/<filename>` — locally hosted individual file

The optional upstream endpoints are in `app.py` and `vrsrc_source.py`, but are
not usable without appropriate provider authorization.

## Current limitations

- Upstream authentication for providers requiring developer API keys is not
  implemented yet.
- Rate limiting and the provider's required update-contribution workflow need
  to be completed before requesting production API access.
- Access controls are the responsibility of the reverse proxy; the application
  does not have user accounts.
- The current browser UI contains links to the operator's separate services;
  these must be reviewed against any provider's external-link policy before an
  application is submitted.

## License

MIT. See [LICENSE](LICENSE).
