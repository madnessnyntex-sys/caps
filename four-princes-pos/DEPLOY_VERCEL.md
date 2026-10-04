# Deploying Four Princes to Vercel (free tier)

Read this before the main `README.md` setup section if your goal is a public
URL rather than a local demo. It covers what had to change to make a
Flask + MySQL + PHP system run on a serverless platform, and exactly how to
deploy it.

## Why two separate Vercel projects, plus an external database

Vercel runs two kinds of things: static files, and short-lived serverless
functions. It does not run a long-lived server process, and it gives
functions no persistent disk between requests. That shapes everything below:

- **The backend deploys as its own Vercel project**, using Vercel's official
  Python runtime (currently in Beta). Flask itself needs no code changes —
  it was already a standard WSGI app.
- **The frontend deploys as its own, separate Vercel project**, using
  `vercel-php`, which is Vercel's own recommended community runtime for PHP.
- **MySQL must live outside Vercel entirely**, on a host that actually keeps
  a disk around between requests. Vercel doesn't offer one. [Aiven's free
  MySQL plan](https://aiven.io/mysql) is genuine, literal MySQL (not just
  wire-compatible) with no trial expiry, which matches what Chapter 1 and
  Chapter 3 of the paper actually say you built.
- **SQLite cannot be used on this deployment at all.** The local "zero-setup"
  SQLite default only works because there's a real, persistent disk under
  `python app.py` on your own machine. On Vercel there isn't one. The
  `DATABASE_URL` environment variable (below) is not optional here — without
  it, the backend doesn't have a disk to fall back to and will fail to start.
- **Backup/restore no longer shells out to `mysqldump`/`mysql`.** Those
  binaries don't exist in Vercel's Python runtime. The code now tries the
  CLI tools first (so your local XAMPP setup is completely unaffected) and
  automatically falls back to a pure-Python equivalent, built on PyMySQL,
  when they're not found. This was tested directly: real MySQL data with
  apostrophes, quotes, unicode text, NULL foreign keys, and decimal values
  was backed up and restored through the fallback path, and every value
  came back exactly as it went in.

Two projects means two URLs and a cross-origin call from frontend to
backend — which is exactly what `Flask-CORS` (`CORS_ORIGINS`) was already
built to handle, so nothing new there either.

## Step 1 — Create the free MySQL database (Aiven)

1. Go to [aiven.io](https://aiven.io) and create a free account.
2. Create a new service → **MySQL** → select the free plan.
3. Once it's running, open the service's **Overview** tab and copy the
   connection details: host, port, username, password, and default database
   name.
4. Build your connection string in this exact format:
   ```
   mysql+pymysql://<username>:<password>@<host>:<port>/<database>
   ```
5. Connect to it once (via the Aiven console's query tool, or any MySQL
   client) and run `backend/schema.sql` to create the 6 tables, or simply
   let the app create them automatically on its first cold start — both
   work, since `db.create_all()` only creates tables that don't already
   exist.
6. Run `backend/seed.py` locally, pointed at this `DATABASE_URL`, to load
   the demo accounts and catalog (optional, but useful for your first look
   at the live deployment).

## Step 2 — Deploy the backend

```bash
npm i -g vercel          # one-time install of the Vercel CLI
cd four-princes-pos/backend
vercel login
vercel
```

When prompted:
- **Link to existing project?** No (first time)
- **Root directory?** `.` (you're already inside `backend/`)
- Accept the auto-detected Python/Flask framework preset.

Then set the required environment variables (replace the values with your
own):

```bash
vercel env add DATABASE_URL production
# paste: mysql+pymysql://<user>:<pass>@<host>:<port>/<db>

vercel env add SECRET_KEY production
# paste a long random string -- do NOT reuse the placeholder from config.py

vercel env add CORS_ORIGINS production
# paste your frontend's URL once you have it from Step 3, e.g.
# https://four-princes-frontend.vercel.app
# (you can come back and set this after Step 3 if you're doing this the first time)
```

Redeploy after adding environment variables so they take effect:

```bash
vercel --prod
```

Note your backend's deployed URL (shown after deploy, something like
`https://four-princes-backend.vercel.app`) — you need it for Step 3.

## Step 3 — Deploy the frontend

```bash
cd ../frontend
vercel login   # if not already logged in
vercel
```

Then set its one required environment variable, using the backend URL from
Step 2:

```bash
vercel env add API_BASE_URL production
# paste: https://four-princes-backend.vercel.app/api

vercel --prod
```

Go back and set the backend's `CORS_ORIGINS` to this frontend URL if you
hadn't already, then redeploy the backend once more.

## Step 4 — Use it

Open the frontend's URL. You should land on the login page (the
`vercel.json` rewrite sends `/` to `index.php`, which redirects to
`login.php`). Log in with `admin` / `admin123` and change it immediately,
same as the local setup — this is now a publicly reachable deployment.

## Honest caveats — read this part

- **The Python runtime is in Beta** on Vercel's own platform as of this
  writing. It's officially supported, not experimental fan code, but "Beta"
  is Vercel's own word for it, not mine.
- **I could not personally test a live Vercel deployment** — doing so needs
  a Vercel account and billing-adjacent setup I don't have access to from
  here. Everything above is built from Vercel's own current documentation
  and tested as thoroughly as I could without one: the Flask app's logic is
  unchanged and fully tested, the pure-Python backup/restore fallback was
  tested end-to-end against a real MySQL server with deliberately tricky
  data, and the PHP changes are the smallest change that could work (one
  environment variable, one rewrite rule). The one piece I genuinely can't
  verify end-to-end myself is the exact routing behavior of the `vercel-php`
  community runtime for a multi-file PHP app with subdirectory includes
  (`includes/header.php` etc.) — if a page loads with a missing-include
  error, that's the first thing to check, and I can help debug it with the
  exact error message.
- **Cold starts are real.** The first request after a period of inactivity
  on Vercel's free tier will be slower than every request after it. This
  isn't a bug, it's how serverless billing works.
- **Free-tier limits exist on both Vercel and Aiven** (function execution
  time, request size, database storage/connection limits) and can change
  over time — check each platform's current limits if you hit an
  unexpected error, rather than assuming it's a bug in this code.

## What stays true to the paper, and what's deployment-only

Flask, Flask-SQLAlchemy, PyJWT, Werkzeug, Flask-CORS, MySQL, and the PHP
frontend are all still exactly what's running — nothing in that list was
swapped out. The only things that differ between this deployment and your
local/paper-matching setup are infrastructure-level: SQLite isn't available
here (MySQL is mandatory), and the backup feature uses its pure-Python
fallback instead of the `mysqldump`/`mysql` binaries, automatically, because
those binaries don't exist on this platform. Your local setup via XAMPP,
and everything described in Chapter 3, is completely unaffected by any of
this — this file only concerns the separate, optional, publicly-reachable
copy.
