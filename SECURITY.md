# Security Policy

AutoOutlook ingests remote weather data, runs scheduled artifact generation, and exposes public API routes. Security reports are welcome, especially around request handling, artifact publishing, workflow permissions, and deployment configuration.

## Supported versions

The `master` branch is the only supported development line.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Email the maintainer at `info@autooutlook.tech` with:

- a concise description of the issue;
- affected files, endpoints, or workflows;
- reproduction steps or proof-of-concept details;
- the expected impact.

The maintainer will review the report and coordinate a fix before public disclosure when appropriate.

## Areas of interest

- SSRF or unsafe URL handling in remote data fetchers.
- Path traversal or unsafe artifact file access.
- Public API errors that disclose credentials, private bucket names, or raw server paths.
- GitHub Actions or deployment workflows with overly broad permissions.
- Supply-chain risks in Node or Python dependencies.
- Unsafe handling of generated GeoJSON, JSON, PNG, or forecast metadata.
