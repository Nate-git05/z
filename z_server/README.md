# Z Auth Web Application

FastAPI + **SQLAlchemy** + **PostgreSQL** backend for Z user accounts and workspaces.

The CLI (`z login`) talks to this API. Model API keys stay bring-your-own and are **not** stored here.

## Models (SQLAlchemy)

| Table | Purpose |
|-------|---------|
| `users` | Account identity (email / phone / Google) |
| `workspaces` | Team/workspace |
| `workspace_memberships` | User ↔ workspace + role |
| `auth_sessions` | Issued CLI/web access tokens (hashed) |
| `verification_challenges` | Email OTP / magic link / phone Verify |
| `oauth_states` | Google PKCE state for CLI browser flow |

## Configure Postgres

```bash
export DATABASE_URL="postgresql+psycopg://z:z@localhost:5432/z"
export Z_SECRET_KEY="replace-me"
export Z_PUBLIC_BASE_URL="http://127.0.0.1:8080"

# Optional — email delivery (otherwise OTP is printed to server logs)
export Z_SMTP_HOST=...
export Z_EMAIL_FROM=noreply@yourdomain.com

# Optional — Twilio Verify
export TWILIO_ACCOUNT_SID=...
export TWILIO_AUTH_TOKEN=...
export TWILIO_VERIFY_SERVICE_SID=...

# Optional — Google OAuth
export Z_GOOGLE_CLIENT_ID=...
export Z_GOOGLE_CLIENT_SECRET=...
```

## Run

```bash
pip install -r requirements/requirements-web.txt
# create tables (dev) or use Alembic:
alembic revision --autogenerate -m "init"
alembic upgrade head

uvicorn z_server.app:app --host 0.0.0.0 --port 8080
```

Point the CLI at it:

```bash
export Z_AUTH_URL="http://127.0.0.1:8080"
export Z_AUTH_DEV=0
z login
```

## API (CLI contract)

- `POST /v1/auth/email/start` `{email, name}`
- `POST /v1/auth/email/verify` `{email, code, name}`
- `GET  /v1/auth/email/session/{id}`
- `POST /v1/auth/phone/start` `{phone}`
- `POST /v1/auth/phone/verify` `{phone, code}`
- `GET  /v1/auth/google/start?...` → redirect to Google
- `POST /v1/auth/google/exchange` `{code, code_verifier, redirect_uri}`
- `GET  /v1/auth/me` (Bearer token)
