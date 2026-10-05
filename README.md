# ci-workflows

Reusable CI workflows for the FreeGameStore fleet.

This repository hosts shared GitHub Actions workflows used by all game repositories in the `freegamestore-online` organization.

## Workflows

- **game-ci.yml** — Standard CI pipeline for game repos (build, test, deploy)

## Usage

Reference workflows from this repo in your game repo's `.github/workflows/` files:

```yaml
uses: freegamestore-online/ci-workflows/.github/workflows/game-ci.yml@main
```

## Maintenance

These workflows are maintained centrally to ensure consistency across all games in the fleet.
