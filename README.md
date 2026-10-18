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
