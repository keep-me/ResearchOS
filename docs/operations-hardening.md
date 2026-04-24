# Operations Hardening

This project is safe to run locally with the default SQLite database and no site
password. Public or shared deployments need explicit hardening.

## Authentication

- Keep `APP_ENV=dev` only for local development.
- For public deployments, set `APP_ENV=prod`, `AUTH_PASSWORD_HASH`, and
  `AUTH_SECRET_KEY`.
- The default Docker Compose file is a localhost-bound development profile. Set
  `BACKEND_PORT_BIND` / `FRONTEND_PORT_BIND` deliberately before exposing it on
  a shared network.
- To intentionally run without auth outside dev, set
  `ALLOW_UNAUTHENTICATED=true`. The API refuses unauthenticated non-dev startup
  unless that override is present.
- Set `EXPOSE_API_DOCS=false` when `/docs`, `/redoc`, and `/openapi.json`
  should not be exposed.
- Browser-only asset surfaces use short-lived path tokens minted by
  `/auth/path-token`; do not put the long-lived bearer token into image, PDF,
  EventSource, or WebSocket URLs.

## Database

- SQLite is the default for local and single-user deployments.
- Multi-worker, multi-user, or agent-heavy deployments should use a server
  database via `DATABASE_URL`; SQLite WAL and busy timeout reduce lock errors but
  do not remove write contention.
- `DATABASE_URL` directories are created only for SQLite URLs. Non-SQLite URLs
  are passed through to the database driver.

## Frontend Assets And CSP

- PDF.js, KaTeX, Mermaid, and DOMPurify are loaded from the bundled frontend
  dependencies. A production CSP can keep `script-src` and `connect-src` on
  `'self'` plus the configured API origin.
- Mermaid SVG and HTML brief previews are sanitized before DOM insertion.
- KaTeX renders with `trust=false` and sanitized output.

## Dependency Management

- The repository tracks `frontend/package-lock.json`; use `npm ci` in CI and
  deployment builds.
- CI and container builds use `constraints.txt` to keep Python transitive
  dependencies reproducible.
- The root `package.json` is for repository smoke tooling. The actual browser
  application lives in `frontend/`.
- Dependency auditing runs in CI with `npm audit --audit-level=moderate` and
  `pip-audit`.

## Runtime Artifacts

The following remain local-only and must not be committed:

- `.env`
- `data/`
- `logs/`
- `frontend/dist/`
- virtual environments and `node_modules/`
