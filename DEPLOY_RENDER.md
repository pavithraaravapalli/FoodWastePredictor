# Deploy to Render with a shared PostgreSQL database

Everyone uses the same website and the same PostgreSQL database, so data survives logout, refresh,
restarts and redeploys, and is identical on every laptop. Local SQLite stays available for development.

## 1. Put the project on a private GitHub repository
```powershell
cd FoodWastePredictor
git init
git add .
git status          # make sure db.sqlite3, .env and venv are NOT listed (.gitignore excludes them)
git commit -m "FoodWastePredictor"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/foodwaste-predictor.git   # create the repo as PRIVATE first
git push -u origin main
```

## 2. Create the database and web service
Easiest: Render dashboard -> **New** -> **Blueprint** -> pick your repo. `render.yaml` creates
`foodwaste-db` (PostgreSQL) and `foodwaste-predictor` (web) and wires `DATABASE_URL` for you.

Manual alternative:
1. **New -> PostgreSQL**, name `foodwaste-db`, copy the *Internal Database URL*.
2. **New -> Web Service**, your repo, Runtime *Python*, Build `./build.sh`,
   Start `gunicorn foodwaste_project.wsgi:application --bind 0.0.0.0:$PORT --workers 3`.
3. Environment variables: `DATABASE_URL` (the URL above), `DJANGO_SECRET_KEY` (long random text),
   `DJANGO_DEBUG=0`. `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for your `*.onrender.com` address are added
   automatically; add your own domain with `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS=https://your-domain`.
4. Optional keys: `TOMTOM_API_KEY`, `TICKETMASTER_API_KEY`, and `EMAIL_*` (see `.env.example`). Keys live only in Render.

## 3. Create the first Owner (Render dashboard -> your service -> Shell)
```
python manage.py create_owner --email you@example.com --name "Your Name"
```
Then sign in at your site, open **Team -> Add user** and create Manager and Staff accounts. Everyone gets their own login.

## 4. Copy your existing local data into PostgreSQL (once, optional)
Run on your laptop, with the *External Database URL* from Render:
```powershell
python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.permission -e sessions -e admin.logentry --indent 2 --output data.json
$env:DATABASE_URL = "postgres://USER:PASSWORD@HOST/DBNAME?sslmode=require"
python manage.py migrate
python manage.py loaddata data.json
Remove-Item Env:DATABASE_URL
```
(`--output` avoids PowerShell's UTF-16 redirection problem.) Do this before staff start entering data, never twice.

## 5. Backups
* Render **paid** PostgreSQL plans include automatic daily backups and point-in-time recovery - use one for real data.
* Render **free** PostgreSQL databases expire after a limited period and have no backups: fine for a trial, not for real data.
* Extra safety: `pg_dump "<External Database URL>" > backup.sql` on a schedule you control.

## 6. Check it works
Add a record, log out, log in from another browser: the data is there. Redeploy: it is still there.
Run `python manage.py check --deploy` locally with the production variables for Django's own checklist.

## Local development (unchanged)
Leave `DATABASE_URL` unset and run `python manage.py runserver`; it uses `db.sqlite3`.
To test against a local PostgreSQL: `$env:DATABASE_URL = "postgres://user:pass@localhost:5432/foodwaste"`.
