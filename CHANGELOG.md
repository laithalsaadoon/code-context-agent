## v3.1.2 (2026-04-06)

### Fix

- **ci**: use tag ref for SLSA provenance generator

## v3.1.1 (2026-04-06)

### Fix

- resolve MCP tool loading in swarm agents

## v3.1.0 (2026-04-06)

### Feat

- add framework detection to heuristic summary

## v3.0.0 (2026-04-06)

### Fix

- **ci**: align SARIF categories between security and rescan workflows
- remove cairosvg dep (LGPL-3.0 blocked by license check)


- revamp documentation for v10 coordinator architecture

## v2.0.0 (2026-04-06)

### Feat

- shift-left security hardening with betterleaks migration

### Fix

- betterleaks CI install checksum verification

## v1.0.0 (2026-04-06)

### Feat

- add change verdict engine, temporal risk intelligence, and CI/CD integration
- replace custom code intelligence with GitNexus MCP

### Fix

- address CodeQL findings (implicit concat, unused var, empty except)
- **ci**: remove defunct ui/pnpm setup from test job

## v0.3.2 (2026-04-04)

### Fix

- **ci**: restore ty: ignore directives and set private-repository for SLSA

## v0.3.1 (2026-04-04)

### Fix

- use grype outputs.cmd path and remove unused ty: ignore directives

## v0.3.0 (2026-04-03)

### Feat

- add grype, SLSA provenance, rescan-on-advisory, and SBOM enhancements

### Fix

- harden shift-left security and resolve ACAT findings

## v0.2.1 (2026-03-31)

### Fix

- wire team swarm timeouts to config and scale with analysis mode

## v0.2.0 (2026-03-31)

### Feat

- increase Bedrock retry to 10 attempts with adaptive mode

## v0.1.0 (2026-03-30)

### Feat

- Reset versioning to v0.1.0 (fresh start from former v10.2.0)
