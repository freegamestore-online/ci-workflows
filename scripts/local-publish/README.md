# Local fallback for the SDK npm publish

Use this when GitHub Actions can't run the publish, for example when Actions
credits are out. It runs the same publish job on this laptop (`pink-laptop`).

**Source of truth:** `.github/workflows/publish.yml` in `freegamestore-online/platform`.
`publish-local.py` reads that file when it runs and executes the job's `run:`
steps in order, once per matrix package (`games-sdk`, `fgs-cli`, `compliance`).
It does not copy the commands, so a change to the YAML is picked up the next time
you run it. If the YAML uses something the script doesn't understand, the script
stops with an error and does not guess.

## Prerequisites

- Platform checkout on `main`, clean, at `~/dev/stores/fgs/platform`
  (or set `FGS_PLATFORM_DIR`).
- Node 24 (the job uses `node-version: 24`).
- pnpm matching the repo's `packageManager` pin. Run `corepack enable` once; the
  script refuses to run on a mismatch.
- PyYAML: `python3 -m pip install --user pyyaml`.
- An npm token with publish rights for the `@freegamestore` scope. Only needed
  for a real publish, not for a dry run.

## Run it

```sh
# Dry run (default): builds, tests, and runs `npm publish --dry-run`. Publishes nothing.
scripts/local-publish/publish-local.py --platform-dir ~/dev/stores/fgs/platform

# One package only
scripts/local-publish/publish-local.py --platform-dir ~/dev/stores/fgs/platform --package compliance

# Real publish. Needs NPM_TOKEN. Packages with an unchanged version are skipped, as in CI.
NPM_TOKEN="$(security find-generic-password -a npm -s fgs-npm-publish -w)" \
  scripts/local-publish/publish-local.py --platform-dir ~/dev/stores/fgs/platform --publish
```

Store the token once in the macOS Keychain:

```sh
security add-generic-password -a npm -s fgs-npm-publish -w
```

## How it differs from CI

- **Auth:** CI uses OIDC trusted publishing, which only exists inside GitHub
  Actions. Locally the script uses `NPM_TOKEN`.
- **No provenance:** `--provenance` needs the GitHub OIDC token, so the script
  removes it. Tarballs published from the laptop have no provenance attestation.
- **Dry run by default:** nothing is published without `--publish`.
- **Only from `main`:** the script refuses a checkout on any other branch, as the
  workflow only publishes from `main`.
- **Actions steps are checks, not runs:** `checkout`, `setup-node` and
  `pnpm/action-setup` become checks on the laptop (Node major, pnpm pin, checkout).
  A new `uses:` step makes the script stop until someone adds a check for it.

## Not covered

- `act` was considered and not used: it needs Docker and can't provide the OIDC
  token that the publish job depends on.
- Running the publish job in CI doesn't use this script; it is only the fallback.

Refs: freegamestore-online/ci-workflows#2
