# Contributing to AutoOutlook

AutoOutlook is an early-stage open-source severe-weather dashboard and artifact pipeline. Contributions are welcome when they improve reproducibility, forecast inspection, security, or maintainer workflows.

## Good first contribution areas

- Documentation for setup, deployment, and artifact refresh workflows.
- Tests around public API response shapes and generated artifact contracts.
- Frontend fixes for forecast-hour navigation, map rendering, accessibility, and export workflows.
- Backend hardening for remote data ingestion, path handling, scheduler reliability, and clear error responses.
- Verification improvements against SPC outlook data after prediction artifacts are generated.

## Local development

Install frontend dependencies:

```powershell
npm install
npm run dev
```

Run the backend when you need live HRRR/NOMADS-backed API routes:

```powershell
python -m backend.server
```

Generate deployable outlook artifacts:

```powershell
python -m backend.ml.outlook_pipeline
```

## Before opening a pull request

Run the checks that match your change:

```powershell
npm run build
python -m unittest backend.tests.test_deployable_outlook_pipeline
```

For documentation-only changes, a focused proofread is usually enough. For backend artifact changes, include a short note describing which artifact files or API routes are affected.

## Pull request expectations

- Keep changes focused and explain the user-visible behavior.
- Do not commit generated runtime artifacts from `backend/artifacts/`.
- Do not introduce official SPC data into model features; SPC data is for verification after prediction artifacts are written.
- Avoid exposing raw storage paths, credentials, or private deployment details in public API errors.

## License

This repository does not currently declare an open-source license. Until a license is added by the maintainer, ask before reusing the code outside normal GitHub contribution workflows.
