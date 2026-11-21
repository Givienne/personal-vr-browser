# Private Contribution Manager V3

This is a **standalone, offline, administrator-only** tool. It never contacts VRSRC, uses an upload config, or uploads files. Public VR browsing and downloading remain unchanged.

- `detect_releases.py` compares an operator-provided JSON version manifest with the local catalog (no headset/ADB required).
- `contribution_queue.py` imports candidates, attaches local owned files with SHA-256, and changes their review status.
- `prepare_submission.py` checks *approved* attachments again and produces an offline review manifest. This is **not** a VRSRC upload config, not a working provider uploader, and does **not** satisfy VRSRC's contribution requirement by itself.

## Examples

```sh
python3 -m unittest -v
python3 contribution_queue.py --queue /private/queue.json list
python3 contribution_queue.py --queue /private/queue.json attach org.example.app 123 /private/owned-release.apk
python3 contribution_queue.py --queue /private/queue.json review org.example.app 123 approved
python3 prepare_submission.py --queue /private/queue.json --output /private/review-v1.json
```

Do not attach synthetic test files as real submissions, and do not mistake catalog candidates for authorized contributions. Approval from VRSRC and a permitted upload mechanism are still required for real delivery.

Keep queue and review files in an admin-owned `0700` directory outside the publicly served site. Don't run this tool with the public website's privileges, or put real upload credentials in the repository.
