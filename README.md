# Public Apple build orchestration

Only orchestration scripts are public. Game inputs, private source, plaintext logs,
signing material and game keys must never be committed here.

## Setup

Create a short-lived fine-grained GitHub PAT for only
`wcc526/nxnative-private-build` with **Contents: Read-only**. Save it here as the
Actions secret `PRIVATE_BUILD_READ_TOKEN`. Never put it in a workflow input, issue,
commit or chat. Revoke it when no longer needed.

Only `wcc526` can manually dispatch this workflow on `main`. It uses a standard
`macos-15` runner, pinned Actions, a fixed private source commit and a fixed input
release. Pull requests cannot trigger builds. Checkout does not persist credentials;
build subprocesses do not receive the private read token.

## Build and retrieve

Select **Actions > Private-input Apple build > Run workflow**. macOS shader
validation is enabled by default. The experimental source excludes 55 reviewed
unsupported shaders; successful compilation does not guarantee playability.

Download `encrypted-build-result` within **one day**. It contains only
`build-result.tar.age`: the intermediate unsigned IPA and diagnostic logs encrypted
with age 1.3.2 to the owner's public recipient. The private identity stays on the
owner's computer and is never uploaded. Failed builds also save encrypted logs when
possible; cancellation or runner termination may prevent that.

Decrypt locally:

```text
age --decrypt --identity /path/to/private-identity.key --output result.tar build-result.tar.age
```

Extract into a fresh local directory. Use the private source's
`scripts/cloud_ipa.py finalize` to merge local game resources. No signing or device
installation occurs. Back up the private identity securely: losing it makes the
encrypted output unrecoverable.

## Visibility and cost

Run metadata and high-level status are public. Detailed build output is redirected
to a private file, and upload is restricted to a single encrypted result. Build
caches are disabled. The public repository never contains a copy of private source.

Standard public GitHub-hosted runner compute is free under GitHub's current policy;
artifact storage has separate allowances. Account restrictions may still prevent
jobs from starting. No billing settings or spending limits are changed.

The age executable comes from its official versioned release; its SHA-256 digest
is checked before execution. No custom encryption format is used.
