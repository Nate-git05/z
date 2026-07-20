# Z web (Next.js)

Public client for landing, pricing, and login/signup. FastAPI (`z_server`) remains the API and still serves `/app/*` Jinja pages.

## Local development

```bash
# Terminal 1 — API
Z_SERVER_DEV=1 Z_SECRET_KEY=dev python3 -m uvicorn z_server.app:app --host 0.0.0.0 --port 8080

# Terminal 2 — Next
cd web
cp .env.example .env.local   # optional
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8080 npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

- `/`, `/pricing`, `/login` → Next.js
- `/v1/*`, `/app/*`, `/static/*` → proxied to FastAPI

Dev email OTP: use code `000000`.

## Deploy on Vercel

**Root Directory must be `web`** (Project Settings → General → Root Directory).

The Next.js `package.json` lives in `web/`, not the repo root. If Root Directory is blank/`.`, Vercel fails with “No Next.js version detected.”

1. Import the GitHub repo
2. Set **Root Directory** → `web`
3. Framework preset: Next.js (auto)
4. Optional env: `NEXT_PUBLIC_API_BASE_URL=https://api.zim-s.com` (once the API is live)
5. Deploy

API proxy rewrites are in `web/vercel.json` (`/v1`, `/app`, `/static` → `api.zim-s.com`).

On the FastAPI host, set `Z_FRONTEND_URL` to your Vercel (or custom) domain so `/`, `/pricing`, and `/login` redirect to Next.
