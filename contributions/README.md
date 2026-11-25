# Private contribution tools

This folder is **admin-only**. Do not expose it as HTTP endpoints, share its
configurations, or place its staging folder beneath the website's file root.

`auto_contribute.py` monitors a private directory of *authorized* local APK
files. It discovers package/version metadata from each APK using Android's
`aapt2 dump badging`, compares versions to the cached catalog, and identifies
newer or unlisted packages without a headset, ADB, or a hand-written manifest.

## Safe dry run (no credentials or network)

```bash
python3 auto_contribute.py \
  --catalog /private/catalog.json \
  --incoming /private/authorized-apks \
  --state /private/upload-state.json
```

The folder should be owned by the maintainer and mode `0700`. The default
command **does not upload** and records no successful uploads. Use `--loop`
with `--interval 3600` for automatic periodic discovery without uploads.

## Real provider contributions (disabled pending VRSRC instructions)

`provider_upload.py` is deliberately not a generic send-anywhere CLI. It
requires a provider-issued, private `upload.config` (mode 0600) and a separate
private path-layout file that identifies the approved remote section and the
provider's *actual* naming rules. The layout schema is:

```json
{"remote":"<provider-upload-config-section>",
 "relativePathTemplate":"<provider-specified-directory>/{package}/{versionCode}/{filename}"}
```

**This schema is a local adapter's mockable interface, not a VRSRC-provided
schema.** Do not invent a production destination from this example. The
provider's official remote, layout, and permission must be obtained first.

Actual uploads also require `VRSRC_ACCESS_APPROVED=1`,
`VRSRC_UPLOAD_AUTHORIZED=1`, and the CLI `--send` flag. The scanner uses
`rclone copyto` for one file at a time, with low request pacing and immutable
uploads; it records a successful transfer to prevent duplicates. This only
means `rclone` succeeded, not that the provider accepted the contribution.

The previous CLI option accepting arbitrary `--destination` is removed.
The rest of V5's manual queue/preflight tools remain available offline.

Only contribute content you own or have permission to redistribute. Never use
unlicensed downloaded files to feed the scanner. No automatic discovery of
new releases is possible if the private staging folder receives no new files;
this is not a remote app-release tracker.
