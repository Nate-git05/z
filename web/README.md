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

## Production

- Deploy this `web/` app on Vercel (repo root `vercel.json` builds from `web/`).
- Run FastAPI on an always-on host (e.g. `api.zim-s.com`).
- Set `Z_FRONTEND_URL=https://zim-s.com` on the API so `/`, `/pricing`, and `/login` redirect to Next instead of Jinja.
