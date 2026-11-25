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

Provider access is **developer/maintainer-only**. The public HTTP server does not
serve remote mirror files or remote directory listings, even to logged-in friends.
Only separately authorized local files may be served through the local download
endpoint. Cached metadata remains readable through the private catalog browser.

The provider runtime client requests a short-lived key using a client ID and
private build secret at POST https://vrsrc.fyi/api/mirror/app-key. It caches keys
in memory, refreshes them after refreshAfter, retries once after a mirror 403,
and backs off after an app-key 503. The build secret is never committed.
The mirror is pinned to https://go.srcdl1.xyz with **no redirects**. A
persisted meta.7z plus conditional HEAD avoids repeated 150-MB downloads.

The source configuration lives in /etc/vialenne-vr/ServerInfo.json; credentials
can be supplied only after approval via /etc/vialenne-vr/runtime.env (mode 0600,
owned by root). Never check either file into Git.

The repository intentionally omits server configuration, game files,
credentials, cached catalogs and cover images.

## API

- `GET /api/status` — library and sync status
- `GET /api/games` — paginated catalog with search and sorting
- `GET /api/games/<package>` — game details and available files
- `GET /covers/<package>` — cached artwork
- `GET /api/download/<package>/<filename>` — locally hosted individual file

There is no public remote mirror endpoint. Upstream metadata synchronization
requires provider approval and developer-only runtime credentials.

## Automatic, maintainer-only contributions

The `contributions/auto_contribute.py` scanner discovers APK package IDs and
version codes using `aapt2` in a private incoming directory. No headset, ADB,
manually pasted inventory, or web upload is required. It compares discovered
versions to the cached catalog. Repeated successful uploads are tracked locally.

Its default mode is **dry-run only**. Real contributions additionally require
provider permission, a private provider-issued `upload.config`, explicit upload
path rules, and opt-in flags. The exact VRSRC upload configuration and upload
naming rules have **not** been issued. Until then, no uploads can be performed.
See `contributions/README.md` for details.

## Known limitations

- VRSRC must approve the app before issuing a client ID and build secret.
- The app does not grant friends the right to download through its developer key.
- Automatic discovery is from an admin-owned APK staging directory, not from
  unauthorized third-party download sources or a device.
- VRSRC's official upload.config and destination path format are still needed.
  A mock-tested provider uploader does not guarantee VRSRC acceptance.
- The application must still be reviewed against VRSRC's own test fixtures.

## License

MIT. See [LICENSE](LICENSE).