# Archived GitHub Container Package Workflows

The production dashboard is a Cloudflare Pages deployment and does not use a
container image. The old GHCR publisher and its cleanup workflow were moved here
so releases cannot create new GitHub package storage.

On 2026-07-19, the legacy cleanup removed the old `0.8` and `0.8.0` container
versions. GitHub refused API deletion of the remaining `1.2.2` version because
that public version had more than 5,000 downloads. It is a protected legacy
package, not an input to the Pages refresh path. GitHub Support is required if
that final historical package must also be removed.

Do not move these files back into `.github/workflows/` unless container release
publishing is intentionally restored.
