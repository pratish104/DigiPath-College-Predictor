# Deployment

DigiPath deploys as one Render Python web service. The same Uvicorn process serves FastAPI APIs, Jinja pages, static files, bundled datasets, and document-processing endpoints. The checked-in [render.yaml](../render.yaml) is the baseline service definition.

## Render service

Create a Render Web Service from the repository and keep these values aligned with `render.yaml`:

| Setting | Value |
| --- | --- |
| Environment | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
| Health check | `/health` |

`GET /health` returns `{"status":"ok"}`. It is a process-health check, not proof that every authenticated or data-dependent workflow has been exercised.

## Environment variables

Configure secrets in Render's environment settings; do not commit them in `.env`, source code, templates, or static assets.

| Variable | Production use |
| --- | --- |
| `DATABASE_URL` | Required MySQL/PyMySQL SQLAlchemy connection URL. |
| `SECRET_KEY` | Required unique signing key for JWTs. Do not use the `.env.example` placeholder. |
| `ALLOWED_ORIGINS` | Comma-separated explicit browser origins. Include the final HTTPS origin when a custom domain is used. |
| `COOKIE_SECURE` | Set to `true` behind Render HTTPS. |
| `LOG_LEVEL` | Normally `INFO`; do not log credentials, tokens, or uploaded document content. |
| `BOOTSTRAP_ADMIN_EMAIL` | Optional one-time first-admin email. |
| `BOOTSTRAP_ADMIN_PASSWORD` | Optional one-time first-admin password; remove after provisioning. |
| `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_ENV` | Configure only when enabling and validating Cashfree. |

`render.yaml` requests `DATABASE_URL` and `ALLOWED_ORIGINS` as manually supplied values, generates `SECRET_KEY`, and sets `COOKIE_SECURE` to `true`.

## Aiven MySQL on Render

DigiPath uses SQLAlchemy with the PyMySQL driver. Keep the managed database URL in the `mysql+pymysql://` form supplied for the service. For Aiven, the URL may include `ssl-mode=REQUIRED`.

PyMySQL does not accept `ssl-mode` as a direct connection keyword. `database.py` recognizes that option only for `mysql+pymysql` URLs, removes it before engine creation, and passes a verified Python `SSLContext` to PyMySQL. Certificate and hostname verification remain enabled.

To trust Aiven's CA chain:

1. In Render, add a **Secret File** named `ca.pem` containing the Aiven CA certificate.
2. Render makes it available at `/etc/secrets/ca.pem`.
3. On startup, DigiPath checks whether that exact path exists. If it does, the TLS context calls `load_verify_locations(cafile="/etc/secrets/ca.pem")`.

Do not disable certificate verification to work around CA errors. If the secret file is absent, the application retains the platform's default trusted CA configuration; provide the Aiven CA secret when the server chain requires it.

## Database startup behavior

At application startup, `database.create_database_tables()` calls SQLAlchemy metadata creation and includes limited current-user column adjustments. Test this behavior against a non-production database before deployment. Take database backups independently of the Render instance.

This is an early-stage schema strategy, not a replacement for reviewed versioned migrations. Introduce migrations before making nontrivial production schema changes.

## First administrator

To provision the first administrator, temporarily set both `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` (the password must satisfy the implementation's minimum length). The lifespan startup code invokes the seeding path. Once the account exists, remove `BOOTSTRAP_ADMIN_PASSWORD` from Render.

The application does not require a default administrator credential in source control.

## Custom domain and cookies

After the Render URL is healthy:

1. Add the custom domain in Render and configure the DNS record Render supplies.
2. Wait for HTTPS issuance.
3. Set `ALLOWED_ORIGINS` to the final `https://` origin (plus any intentionally supported development origin).
4. Keep `COOKIE_SECURE=true`.
5. Verify registration, login/logout, a protected page, and profile update over the final domain.

## Cashfree

Cashfree routes remain unavailable while credentials use their placeholder/default values. Before enabling payments, configure provider credentials deliberately, select the intended `CASHFREE_ENV`, and validate order creation, provider verification, and signed webhook handling against the appropriate provider environment. Do not enable it solely because environment variables exist.

## Release checks

```powershell
python -m unittest discover -s tests -v
```

Then use non-production data to verify:

- `GET /health`;
- Aiven/MySQL startup and TLS connection;
- registration, login/logout, and protected pages;
- profile save and avatar behavior if enabled;
- CET and Diploma/DSE request validation;
- one bounded PDF/DOCX resume workflow;
- scam-screening behavior; and
- Cashfree only when intentionally enabled.

See [Architecture](ARCHITECTURE.md) for service boundaries and [Tutorial](TUTORIAL.md) for local developer setup.
