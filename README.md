# ci-workflows

Reusable CI workflows for the FreeGameStore fleet.

This repository hosts shared GitHub Actions workflows used by all game repositories in the `freegamestore-online` organization.

## Workflows

- **game-deploy.yml** — Keyless successor of game-ci.yml (freegamestore-online/platform#488):
  the same build/compliance/smoke job, then a deploy job that uploads with a GitHub OIDC
  token through admin.freegamestore.online. No R2 secret. Callers pin it by commit SHA and
  grant `id-token: write`; `fgs publish` and the fleet tool write that caller
  (platform `packages/compliance` `GAME_DEPLOY_WORKFLOW`).
- **game-ci.yml** — Legacy pipeline (R2 secrets in the deploy job). Kept until every caller
  has moved to game-deploy.yml.

## Usage

Reference workflows from this repo in your game repo's `.github/workflows/` files:

```yaml
uses: freegamestore-online/ci-workflows/.github/workflows/game-ci.yml@main
```

## Maintenance

These workflows are maintained centrally to ensure consistency across all games in the fleet.

### Bumping the compliance CLI pin

`game-ci.yml` pins `@freegamestore/cli` through the `compliance_cli_version`
input default. The same pin is repeated in every `template-game-*` repo's
`.github/workflows/compliance.yml`, which runs `fgs check` on a scaffolded copy
of the template. Move both together: if only one side moves, a template can pass
its own CI while a game scaffolded from it fails here (or the reverse). Before
bumping, run the old and new CLI against every game that calls this workflow and
confirm no result changes (see #5 for how that was done for 0.2.3 → 0.2.7).
