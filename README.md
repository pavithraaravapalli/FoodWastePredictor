# FoodWastePredictor

Daily food preparation, sales and waste tracking for cafés, restaurants and highway dhabas,
with demand forecasting, a kitchen preparation planner, waste analytics, reports and traffic notes.
Django + SQLite, server-rendered templates, Chart.js and Lucide (both bundled, so charts and icons work offline).

## Run it (Windows PowerShell)
```powershell
cd FoodWastePredictor
python -m venv venv; .\venv\Scripts\Activate.ps1      # skip if you already use your venv
pip install -r requirements.txt
python manage.py migrate                    # safe: adds columns/tables, keeps all existing data
python manage.py create_owner --promote admin   # or: create_owner --email you@example.com
python manage.py runserver
```
Open http://127.0.0.1:8000/. Your existing superuser is an Owner. Back up first: copy `db.sqlite3`.
For local test accounts: `python manage.py create_dev_users` (DEBUG only; random passwords shown once).
Sample charts: `python manage.py seed_demo_data` (refuses to run when records exist).
Tests: `python manage.py test`

* Roles, permissions and the record workflow: `ROLES_AND_ACCESS.md`
* Forgot-password email: `SETUP_EMAIL.md`   * Live traffic: `SETUP_TRAFFIC.md`

## Pages
| Page | What it does |
|---|---|
| Overview | Date-range KPIs, one insight, sales/waste chart, top items, alerts |
| Food Inventory | Add/edit items, deactivate (history kept), safe delete |
| Daily Records | Filter, search, edit, delete; sold can never exceed prepared |
| Demand Forecast | Weekday-average forecast with optional weather/holiday/event/traffic adjustments and a plain explanation |
| Preparation Planner | Suggested → Approved → Prepared, with who approved and when |
| Traffic Intelligence | Restaurant map, manual traffic entry, optional live readings |
| Waste Analytics | Trend, items, categories, weekday pattern, comparisons, data-backed suggestions |
| Reports | Daily/weekly/monthly, forecast vs actual, CSV export, print |
| Approvals / Team / Activity Log | Review submitted records; manage users and roles; who-did-what history (role-based) |
| Ingredients / Stock / Recipes | Raw ingredients in g, ml or pieces (units are never mixed), purchases, adjustments, low-stock alerts, recipe costs |
| Kitchen | Batches assigned to staff who record actual ingredient use; submitting deducts stock once; consumption analytics |
| Weather / Local Events | Automatic (Open-Meteo, Ticketmaster) or manual entry, with source and last-updated shown |
| Settings | Profile, restaurant details (name, location, currency, alert threshold), password |

## Optional setup
* Live traffic readings: see `SETUP_TRAFFIC.md`
* Password-reset emails print in the terminal while developing.

## How the forecast works (and doesn't)
It is a transparent statistical estimate, **not machine learning**: the average of the last few same-weekday
sales, adjusted only when your own history shows a clear weather/holiday/event/traffic pattern, plus a safety reserve
sized from day-to-day variation. Accuracy is shown only after it can be back-tested on 5+ past days. Time-of-day
patterns are not used because sales times are not recorded. Sold-out days hide real demand, and the app warns about that.

## Before going live
Set `DEBUG = False`, a real `SECRET_KEY` and `ALLOWED_HOSTS` in `foodwaste_project/settings.py`, and configure a real email backend.
This is a single-restaurant app; what each person can see and do depends on their role.
