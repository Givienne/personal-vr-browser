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
will fail against services that require additional authorization. The service
will not make upstream requests until both `VRSRC_ACCESS_APPROVED=1` and a
provider-issued `VRSRC_API_KEY` are set in its private process environment.
That approval flag is a local safeguard, not a substitute for permission.
Remote file listing and relay additionally require `allowDownloads: true` in
the server-side config and permission from the provider and rights holders.
A listing is cached for five minutes; failed listings are cached for one minute.
Requests to list new directories are spaced by at least two seconds, and only
two remote transfers may run at a time. rclone also uses a one-request-per-second
TPS limit. Failed metadata syncs retry after an hour rather than continuously.
Never place environment secrets in the public repository.

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

## Checking versions for contributions

Version comparison and contribution review do not require a headset, ADB, or sideloading.
An administrator can provide a JSON manifest of package IDs and Android version codes
from an independent, authorized source. The tools in contributions/ compare this
manifest against the cached catalog, queue candidates for review, attach authorized
local files, verify hashes, and prepare an offline submission manifest.

The generic authorized_upload.py rclone adapter supports a provider-supplied
upload configuration and an explicitly authorized destination, but real upload
permission, naming requirements, and VRSRC acceptance still require provider
confirmation. It does not automatically discover releases or upload anything
from the public site. See contributions/README.md for the operator-only workflow.

The older version_audit.py utility remains an optional way to compare an
operator-provided Android package inventory; it is not required for normal use.

## Current limitations

- vrSrc must explicitly approve server-side access and clarify whether invited
  friends may use the private website. No approval has been received yet.
- Version detection, private review, and a generic gated rclone transfer adapter
  exist, but VRSRC's authorized upload protocol and acceptance remain unverified.
  Do not claim that the provider's contribution requirement is met yet.
- The upstream adapter hasn't been validated with a real approved API key.
- Access controls are the responsibility of the reverse proxy; the application
  does not have user accounts. Restrict access before serving private files.
- The browser UI contains no promotional or third-party links. External-link
  restrictions still require the provider's interpretation for deployments.

## License

MIT. See [LICENSE](LICENSE).