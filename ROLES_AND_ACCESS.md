# Roles and access

Three roles, stored on `UserProfile.role` and mirrored into Django Groups (Owner, Manager, Staff).
Every sensitive view is protected on the **server** with Django permissions (`permission_required`);
hiding a menu item is only a convenience. Missing permission -> HTTP 403 with
"You do not have permission to perform this action."

| Capability | Owner | Manager | Staff |
|---|:--:|:--:|:--:|
| Dashboard | full | operations | simplified |
| Add daily records | yes (auto-approved) | yes (auto-approved) | yes (Draft / Submitted) |
| Edit records | all | all operational | own Draft/Rejected only |
| Delete records | yes | yes | no |
| Approve / reject submitted records | yes | yes | no |
| Food items: add / edit / deactivate / delete | yes | yes | only if granted per user |
| Change item prices | yes | yes | no |
| Forecasts, preparation plans | yes | yes | view approved plans only |
| Analytics, reports, CSV export (financial) | yes | yes | no |
| Traffic intelligence | yes | yes | no |
| Ingredients, recipes, stock (purchases, adjustments) | yes | yes | recipes: read only, no costs |
| Create / assign preparation batches, review consumption, send back for correction | yes | yes | no |
| Record actual ingredient usage and submit a batch | only own batches | only own batches | only batches assigned to them |
| Restaurant settings, integrations | yes | no | no |
| Team / users / roles / password-reset emails | yes | no | no |
| Activity log | yes | only if granted per user | no |
| Django admin | superuser only | no | no |

* Staff see only **their own** records. Approved or submitted records are locked for staff:
  "This record has already been approved and is locked." / "Waiting for manager approval."
* Per-user extras (Team > Edit): "Manage food items" and "View the activity log".
* Nobody can change their own role or deactivate themselves; the last active Owner is protected;
  a Manager can never create, edit or deactivate an Owner.
* Public sign-up creates **inactive Staff** accounts that wait for the Owner to approve them.
  An Owner can only be created by `create_owner`, by promoting an existing user, or by an Owner in Team.
* Existing superusers (your `admin`) are Owners.

## Record workflow
Draft -> Submitted -> Approved / Rejected (with a reason). A rejected record can be corrected and resubmitted.
Only **Approved** records feed forecasts, analytics, reports and the dashboard.
Records that existed before this update were migrated as Approved.

## Commands
```powershell
python manage.py create_owner --email owner@example.com --name "Your Name"   # prompts for a password
python manage.py create_owner --promote admin                                # promote an existing user
python manage.py create_dev_users                                            # DEBUG only: dev-owner / dev-manager / dev-staff with random passwords (printed once)
```

## Kitchen consumption and stock
Forecast -> plan -> **batch** assigned to a person -> that person records the **actual** ingredients used and portions
made -> **Submit** -> stock is deducted once from the actual quantities (never from planned ones).
Every movement is an immutable `StockTransaction` with a unique reference, who did it and when. A reviewer can
**reverse and send back** a submitted batch: the deduction is reversed with new entries (originals stay in the history)
and the person resubmits corrected numbers. Stock may go negative if usage exceeds what the books say; that is shown
as a recount warning instead of hiding it.
