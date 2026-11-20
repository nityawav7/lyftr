# Deployment

Backend on **Render** (Docker), frontend on **Vercel** (or Render static). About 15 minutes, all free tiers.

## 0. Push to GitHub

```bash
git remote add origin https://github.com/<you>/lyftr-llm-chat.git
git push -u origin main
```

The repository must be **public**.

## 1. Backend on Render

1. <https://dashboard.render.com> → **New → Web Service** → connect the repo.
2. Settings:
   * **Root Directory:** `backend`
   * **Runtime / Language:** Docker (it picks up `backend/Dockerfile`)
   * **Instance type:** Free
   * **Health Check Path:** `/health` (under *Advanced*)
3. Environment variables:

   | Key | Value |
   |---|---|
   | `LLM_API_KEYS` | `key-one,key-two` (from two different Google Cloud projects) |
   | `LLM_MODEL` | `gemini/gemini-3.5-flash-lite` |
   | `DATABASE_URL` | `sqlite:////data/app.db` |
   | `ENABLE_FAILURE_SIMULATION` | `true` |
   | `ALLOWED_ORIGINS` | `http://localhost:3000` for now, updated in step 3 |

4. Create the service. When the deploy is live, open `https://<backend>.onrender.com/health`. It should show `"status":"ok","keys_configured":2`.

> **Persistence on Render.** The free plan has no persistent disk: SQLite survives restarts of a running instance but not a redeploy or spin-down. On a paid instance add a Disk with mount path `/data` and nothing else changes. Railway and Fly.io volumes also work: mount at `/data`. On Railway, set `RAILWAY_RUN_UID=0` so the volume is writable.

*Alternative:* **New → Blueprint** with `render.yaml` creates the backend and a static frontend in one go.

## 2. Frontend on Vercel

1. <https://vercel.com/new> → import the repo.
2. **Root Directory:** `frontend` (framework auto-detects as Vite; `vercel.json` sets build output and SPA rewrites).
3. Environment variable: `VITE_API_BASE_URL` = `https://<backend>.onrender.com` (no trailing slash).
4. Deploy. Note the URL, e.g. `https://lyftr-chat.vercel.app`.

## 3. Connect them (CORS)

Back in Render, set:

```
ALLOWED_ORIGINS=https://lyftr-chat.vercel.app,http://localhost:3000
```

Save (Render redeploys). Open the Vercel URL: the header should say *API online with 2 keys*.

## 4. Verify the live deployment

```bash
python backend/scripts/contract_check.py https://<backend>.onrender.com
```

All 26 checks should pass. Then put both URLs in the README's *Live URLs* table.

## Troubleshooting

| Symptom | Fix |
|---|---|
| UI says *API unreachable* | Wrong `VITE_API_BASE_URL` (redeploy Vercel after changing it), or the backend is asleep: open `/health` and wait. |
| Browser console shows a CORS error | `ALLOWED_ORIGINS` must match the frontend origin exactly: scheme, no trailing slash. |
| Every message returns 503 | Check the `attempts` field in the 503 body. `401` means a key is invalid; `429` on both keys usually means they share one project's quota. |
| `model not found` | Pick a model your keys can use (AI Studio lists them) and set `LLM_MODEL`. |
