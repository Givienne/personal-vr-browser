# Private Contribution Manager

This directory contains an **administrator-only** version review and contribution workflow. The public Personal VR Browser remains a download-only website with no headset, ADB, sideloading, or visitor upload requirement.

Files:
- detect_releases.py: compare administrator-provided package/versionCode data to the cached catalog.
- contribution_queue.py: import candidates, attach files the administrator may contribute, review them, and calculate SHA-256.
- prepare_submission.py: verify approved attachments and generate an offline review manifest.
- authorized_upload.py: produce that offline plan, or use an explicitly authorized rclone configuration to transfer one approved file to a provider-specified destination.

All operations except the gated send command are offline. The tools do not independently discover new releases, obtain permission to redistribute packages, or know VRSRC's official upload path or acceptance protocol.

## Local-only usage

Run the tests from this directory:

    python3 -m unittest -v

Compare a provider-authorized local catalog with an operator-created JSON release manifest:

    python3 detect_releases.py --catalog /private/catalog.json --manifest /private/releases.json --output /private/candidates.json

Import, review, and attach files **only with the right to contribute them**:

    python3 contribution_queue.py --queue /private/queue.json import /private/candidates.json
    python3 contribution_queue.py --queue /private/queue.json attach org.example.app 123 /private/owned-release.apk
    python3 contribution_queue.py --queue /private/queue.json review org.example.app 123 approved

Create a local plan:

    python3 authorized_upload.py plan --queue /private/queue.json --output /private/plan.json

Nothing is uploaded by the plan command. It works without VRSRC credentials. Keep private data and receipts outside the public website and repository, in an owner-only directory.

## Future authorized transfers

The send command is **disabled unless** the operator explicitly confirms provider permission, sets both VRSRC_ACCESS_APPROVED=1 and VRSRC_UPLOAD_AUTHORIZED=1, supplies a private upload.config file, and specifies a destination that is explicitly permitted by that provider. Setting the local flags is **not** provider authorization.

After receiving VRSRC's real authorization and destination instructions, the operator can use the CLI help:

    python3 authorized_upload.py send --help

Send uses rclone copyto for exactly one approved file per invocation, with one transfer and a one-request-per-second limit. It rechecks the file hash immediately before sending, never reads a configuration from the browser, and writes a private receipt after rclone reports success. A successful transfer alone does **not** prove VRSRC accepted the contribution. Investigate possible partial transfers before retrying.

Never copy another application's upload config or API credentials into this repo or service. Only use provider-issued or otherwise explicitly authorized credentials and destinations.

## Remaining limitations

Provider-specific upload configuration, naming rules, contribution acceptance, and permission for a shared download website remain unverified. This generic adapter does **not** by itself satisfy VRSRC's contribution requirement, and no real submissions have been made.
