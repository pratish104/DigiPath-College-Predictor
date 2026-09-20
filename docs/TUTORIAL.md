# Developer tutorial

This guide takes a contributor from a local checkout to tracing a real DigiPath request. It describes the repository as it exists today: a FastAPI/Jinja application backed by a MySQL-compatible database and bundled datasets.

## Prerequisites

- Python 3.10 or later
- Git
- A reachable local or development MySQL-compatible database
- PowerShell on Windows for the commands below (equivalent shell commands are fine elsewhere)

Do not point a local checkout at production credentials or production student data.

## Clone, configure, and run

```powershell
git clone <repository-url>
cd DigiPath
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` before starting the application:

```dotenv
DATABASE_URL=mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/digipath_db
SECRET_KEY=replace-with-a-new-unique-secret
ALLOWED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
COOKIE_SECURE=false
LOG_LEVEL=INFO
```

Use a real database name, user, and password. `database.py` attempts the configured local address and has a localhost/`127.0.0.1` fallback for local URLs. It connects eagerly during import/startup, so the database must be reachable before Uvicorn starts.

Start the app:

```powershell
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`. Use `http://127.0.0.1:8000/docs` to inspect the generated API schema. On startup, the application prepares tables, loads the institute registry, and may seed an administrator only when both bootstrap environment variables are explicitly set.

## Repository orientation

| Path | Start here when you need to understand… |
| --- | --- |
| `app.py` | App startup, CORS, static mounting, Jinja pages, and router inclusion. |
| `database.py`, `models.py` | Engine/session lifecycle and persisted models. |
| `auth_routes.py`, `auth_service.py` | Registration, login, token creation, cookies, and guards. |
| `backend_routes.py` | Profile, institute, job, scam, bookmark, and supporting APIs. |
| `predictor_routes.py`, `cet_predictor.py`, `data_loader.py` | Predictor API contracts, filtering/ranking, and dataset normalization. |
| `resume_routes.py`, `resume_service.py` | Bounded document upload and resume processing. |
| `chat_routes.py` | Bounded assistant conversation and local intent routing. |
| `templates/`, `static/` | Server-rendered UI and browser assets. |
| `data/` | Bundled admission, institute, job, and supporting data. |
| `tests/` | Current unittest regression suite. |

## Trace a page and profile request

1. A signed-in browser requests `GET /dashboard`.
2. `app.py` calls its page-user resolver, which accepts the bearer token or `access_token` cookie and resolves the user through the database.
3. The route renders `templates/dashboard.html` with that user context.
4. The shared frontend uses the existing profile API where it needs current profile data.
5. A profile update posts to `POST /api/user/profile` in `backend_routes.py`.
6. That endpoint verifies ownership from the token/cookie, updates `models.User` and its `profile_settings` JSON, commits through `get_db()`, and returns a success response.

The key rule for profile work: extend the existing page and `/api/user/profile` flow rather than creating a second profile store or duplicate route.

## Understand the predictor/data flow

The admission path is deliberately stricter than a generic CSV search:

1. `data_loader.py` selects only its manifest-listed CET and Diploma/DSE inputs from `data/`.
2. It maps source column aliases into canonical fields such as college code, branch, category, cutoff, year, round, and seat code.
3. `CETPredictor` uses a percentile; `DiplomaPredictor` uses a percentage.
4. Predictor routes validate input and pass category, explicit seat context, special eligibility, university scope, branch, city, college type, year, and round to the appropriate engine.
5. The engine filters normalized records and returns recommendation/result data. Authenticated calls can store prediction history.

When changing data behavior, do not silently substitute an unlisted CSV or merge CET and DSE scoring semantics. Add or update a focused test in `tests/test_predictor_categories.py` for category, seat-code, or filter behavior.

## Authentication flow

Registration in `POST /api/auth/register` stores an Argon2 password hash. Login in `POST /api/auth/login` verifies it, creates a JWT, returns token information, and sets an HTTP-only `access_token` cookie. Protected API/page paths accept either the Authorization bearer token or that cookie and then load the matching user record.

For local HTTP development, keep `COOKIE_SECURE=false`. For deployed HTTPS, set it to `true`. Never hardcode tokens or test passwords in templates, JavaScript, or committed files.

## Run the tests

```powershell
python -m unittest discover -s tests -v
```

The current unit suite covers predictor category/seat behavior and scam-service behavior. `test_register.py` at the repository root is a manual live-server probe, not part of `tests/`; it requires a server already running on port 8000.

## Make a small change safely

For a small navigation-label change, for example:

1. Locate the relevant existing page route in `app.py`.
2. Update the matching shared/sidebar markup in the existing template rather than adding a duplicate route or feature page.
3. Parse the template and run focused checks:

```powershell
python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('dashboard.html'); print('template parse: PASS')"
python -m unittest discover -s tests -v
git diff --check -- templates/dashboard.html
```

4. Review `git diff` for accidental changes outside the intended surface.

For Python changes, also run `python -m compileall -q <changed-file>.py` and the affected test module or full suite. Use a non-production database for any manual end-to-end test.

## Common change locations

| Goal | Existing extension point |
| --- | --- |
| Add/adjust a protected page | `app.py` page route plus its existing template. |
| Change profile fields | `backend_routes.py` profile endpoints, `models.py`/profile settings, and `templates/profile.html`. |
| Change predictor interpretation | `data_loader.py`, predictor classes, predictor request models/routes, and predictor tests. |
| Change resume behavior | `resume_routes.py` and `resume_service.py`; preserve file limits and cleanup. |
| Change job or scam workflows | `backend_routes.py` plus `job_service.py` or `scam_service.py`. |
| Change assistant behavior | `chat_routes.py` and the corresponding local service; retain bounded context and routing restrictions. |
| Change production settings | `.env.example`, `render.yaml`, and [Deployment](DEPLOYMENT.md), only when behavior actually changes. |

## Production note: Aiven TLS

For Render and Aiven MySQL, use a `mysql+pymysql://` URL with the provider's TLS requirement. DigiPath normalizes `ssl-mode=REQUIRED` for PyMySQL and loads `/etc/secrets/ca.pem` into a verified TLS context when Render mounts that secret file. See [Deployment](DEPLOYMENT.md#aiven-mysql-on-render); do not disable verification to bypass CA errors.

## Contributing and security

Read [CONTRIBUTING.md](../CONTRIBUTING.md) before opening a change. Do not commit credentials, resumes, profile data, payment data, or unverified allegations. Report vulnerabilities through the private process in [SECURITY.md](../SECURITY.md).
