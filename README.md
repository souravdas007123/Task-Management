# Taskboard

Django task management app for small teams.

## Run it

```
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py test
```

## Features

- Dashboard with status breakdown, search, filters (status, priority, assignee, label, overdue), sorting and pagination
- My tasks view, Kanban board (drag cards between columns), calendar view
- Projects and labels, subtasks as a checklist (progress updates by itself), comments, multiple file attachments, time logging
- History of every change on a task, in-app notifications and email
- Repeating tasks (daily, weekly, monthly): completing one creates the next
- Reports with CSV download and print or save as PDF, analytics page
- Calendar export (`/calendar.ics`) that you can import into Google, Outlook or Apple Calendar
- JSON API: `GET /api/tasks/`, `GET /api/tasks/<id>/`, `POST /api/tasks/<id>/status/` (login required)

## Roles

Set a role for each user in the admin (Users, then the Profile section at the bottom).

| Role | Can do |
| --- | --- |
| Admin (superuser) | Everything |
| Manager | Edit and delete any task, manage projects and labels |
| Member (default) | Edit tasks assigned to them or created by them, delete only tasks they created, comment on any task |

## Email and reminders

Emails print to the terminal by default. To send real email, set these environment variables:

```
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=smtp.example.com
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_USER=...
DJANGO_EMAIL_PASSWORD=...
DJANGO_EMAIL_USE_TLS=True
DJANGO_FROM_EMAIL="Taskboard <you@example.com>"
```

Users only get emails if they have an email address on their account.

Send reminders for tasks due tomorrow or overdue (run once a day with cron or Task Scheduler):

```
python manage.py send_due_reminders
python manage.py send_due_reminders --days 3
```

## Production settings

```
DJANGO_SECRET_KEY=a-long-random-string
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=example.com
```

Run `python manage.py collectstatic` and serve `static/` and `media/` from your web server.

## Work management and budget

- **Estimates and start dates**: every task can have a start date and estimated hours. The task page compares the estimate with logged time.
- **Workload** (`/workload/`): per person, open hours due in the next 7 days (plus overdue) against weekly capacity.
- **Timeline** (`/timeline/`): 8-week Gantt-style view from start date to due date; filter by project or "only my tasks".
- **Budget** (managers only, `/budget/`): set a budget on a project. Spent = approved expenses + labour cost (logged time x hourly rate). Managers are notified at 80% and 100%.
- **Expenses** (`/expenses/`): anyone can submit one (optional receipt). Manager expenses are approved immediately; others wait for review. Only approved expenses count. Managers can export CSV.
- **Hourly rate and weekly capacity** are set on each user's Profile in the admin site. Time logged by people without a rate is shown as a warning and not costed.
- Currency symbol: environment variable `DJANGO_CURRENCY_SYMBOL` (default `₹`).
- A project that has expenses cannot be deleted.

After pulling: `python manage.py migrate` (adds migration 0009).

## Office features

- **Private projects**: on a project, tick "Private" and choose members. Its tasks are then visible only to members, managers/admins and the task's assignee/creator. This applies to the dashboard, board, calendar, timeline, reports, API and calendar export. A private project that still has tasks cannot be deleted. Note: uploaded files are served by the web server by URL, so use a protected media setup if file secrecy matters.
- **Task dependencies**: "Blocked by" on a task. A blocked task cannot be completed (form, board, API) until its blockers are completed. Loops are rejected.
- **@mentions**: write `@username` in a comment to notify that person (only if they can see the task).
- **Leave**: `/leave/`. Anyone requests leave, managers approve or reject (nobody reviews their own, except a superuser). Approved leave shows "On leave" on the Workload page.
- **Audit log** (managers): `/audit/`, every task change with person, text and date filters.
- **Passwords**: "Forgot your password?" on the login page (needs working email settings), and a change-password button in the sidebar.

## Deploying for an office

1. Copy `.env.example` to `.env` and fill it in (`DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_DB_PASSWORD`, email).
2. `docker compose up -d --build` starts PostgreSQL, the app (gunicorn) and nginx on port 8080.
3. `docker compose exec web python manage.py createsuperuser`
4. Put HTTPS in front (your proxy or load balancer), then set `DJANGO_HTTPS=True`.
5. Back up the `dbdata` and `media` volumes regularly, e.g. `docker compose exec db pg_dump -U taskboard taskboard > backup.sql`.

Without Docker: `pip install -r requirements-prod.txt`, set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, `DJANGO_DB_ENGINE=postgres` plus the `DJANGO_DB_*` variables, run `python manage.py migrate` and `collectstatic`, and serve `staticfiles/` and `media/` with nginx. With `DJANGO_DEBUG=False` the app refuses to start without a real secret key.
