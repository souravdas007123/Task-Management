# Office Hub

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
DJANGO_FROM_EMAIL="Office Hub <you@example.com>"
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

## HR: people, leave balance, salary and payslips

- **Roles**: a new `HR` role (set on the user's profile in the admin site). Only **admin and HR** can see salary data. Managers cannot. An employee sees only their own published payslips.
- **People** (`/people/`, `/people/org/`): directory and org chart for everyone (post, department, who they report to). Phone numbers are visible to HR only. Everyone edits their own phone and emergency contact on **My profile**.
- **HR** (`/hr/`): employee details (post, department, employee ID, joining date, manager), departments, holidays, leave policy, salary, payroll.
- **Leave balance**: yearly quota per leave type (defaults: annual 18, sick 8, casual 6; unpaid and other have no limit). Days are working days: Saturday, Sunday and office holidays are not counted. A request that exceeds the balance is refused. Change quotas and which types are paid on **HR > Leave policy**.
- **Salary**: per-month basic, HRA, allowances, PF % of basic, tax and other deductions. Each revision is a new row, so history is kept; a revision applies from the month of its start date.
- **Payroll** (`HR > Payroll`): create draft payslips for a month, adjust bonus or extra deduction, recalculate, publish. Unpaid leave is deducted as gross / working days of the month per unpaid day. Payslips are snapshots, so later salary changes do not alter old ones. Employees can print or save a payslip as PDF from the browser. CSV export is available for HR.
- Not included: pro-rating for mid-month joiners (adjust by hand), automatic tax slabs (enter TDS per month), bank transfer files.
- Salary and payslip tables are deliberately not registered in the Django admin site.

## Attendance, announcements, assets and onboarding

- **Time zone**: the default is now `Asia/Kolkata` (it was UTC). Set `DJANGO_TIME_ZONE` for another country. Due dates and attendance use this zone.
- **Attendance** (`/attendance/`): Check in and Check out buttons, one pair per day. Check-in after the start time plus grace counts as late (`DJANGO_WORK_START=09:30`, `DJANGO_WORK_GRACE_MINUTES=15`). Hours of `DJANGO_FULL_DAY_HOURS=8` or more is a full day, `DJANGO_HALF_DAY_HOURS=4` or more is a half day. Absent means a past working day (not weekend, holiday or approved leave) with no check-in. A day without check-out counts as present and is flagged for HR to fix.
- **Team attendance** (`/attendance/team/`): HR and admin see everyone, a manager sees the people who report to them (set on the employee record), with CSV export. HR can add, fix or delete an entry.
- **Announcements** (`/announcements/`): HR posts, everyone reads. Pin to the top, optional "hide after" date. Everyone gets an in-app notification and the latest three show on the dashboard.
- **Assets** (`/hr/assets/`, HR only): laptops, phones and so on with tag, serial, cost. Assign, return (optionally mark for repair), retire. Full history is kept, an asset can be with only one person at a time, and an employee sees their assets on My profile.
- **Onboarding and offboarding** (`/hr/checklists/`): start a checklist for a person from an editable template (one step per line, `Owner | Step`). Employees tick their own steps, managers tick manager steps, HR can tick anything. HR is notified when a checklist completes. Offboarding also lists assets still with the person.

## Rooms, visitors, documents, helpdesk, recruitment and reviews

- **Meeting rooms** (`/rooms/`): HR adds rooms; everyone books by date and time. Double booking is blocked (back-to-back is fine), bookings are capped at 12 hours, and only the booker or HR can cancel. Deactivate a room to stop new bookings and keep history.
- **Visitors** (`/visitors/`): any employee registers a visitor for themselves; HR (reception) can register for anyone, checks visitors in and out with a badge number, and the host is notified on arrival.
- **Documents and policies** (`/documents/`): HR uploads PDF, Office, text or image files. Mark a document "everyone must acknowledge" and each employee confirms once; uploading a new file creates the next version and asks again. HR sees who has not acknowledged.
- **Helpdesk** (`/helpdesk/`): IT, HR, admin and finance tickets with replies. Only the person who raised it, the assignee and HR/admin can see a ticket. HR sets status, priority and assignee. A reply from the requester reopens a resolved ticket.
- **Recruitment** (`/hr/jobs/`, HR only): job openings and a candidate pipeline (Applied, Screening, Interview, Offer, Hired, Rejected) with resume upload.
- **Performance reviews** (`/reviews/`): HR creates a cycle and adds everyone; the reporting manager is the reviewer by default. The employee writes a self review (once), the reviewer rates 1 to 5 with feedback and goals, and the employee sees the manager feedback only after it is submitted. Closed cycles accept no more submissions.
- The dashboard shows a "Waiting for you" list (documents to acknowledge, reviews, tickets, visitors).
- The sidebar is grouped into Work, Me, Office, Management and HR.

Limits: uploaded documents and resumes are served by URL like the other uploads, so use a protected media setup if file secrecy matters. There is no calendar sync for room bookings, and expense claims are not yet paid out through payroll.

## Claims, attendance requests, exits and celebrations

- **Claims** (`/claims/`): an employee files money they spent for work (with a receipt). The reporting manager (or HR/admin) approves; nobody approves their own claim, except the admin role. When payroll is generated or recalculated, approved claims up to the end of that month go into the payslip as a tax-free "Reimbursements" line, and each claim is paid only once. Deleting a draft payslip frees its claims. A claim already in a payslip can no longer be rejected.
- **Attendance requests** (`/attendance/requests/`): fix a missed check-in/out in the last 30 days (times required) or ask to work from home (today up to 30 days ahead). The reporting manager or HR approves; an approved fix creates or completes the attendance entry, and a day that already has a complete entry must be edited by HR. Work-from-home days are tagged "WFH" when the employee checks in.
- **Resignation and exit** (`/resign/`, HR at `/hr/exits/`): the employee submits a resignation with a proposed last day and can withdraw it. HR accepts (confirming the last working day, which starts the offboarding checklist) or rejects it. HR cannot decide on their own resignation. "Complete exit" works on or after the last day, needs all assets returned, never disables the only active superuser, disables the login and records the exit date; no payslips are made for months after the exit month.
- **Birthdays and anniversaries**: the dashboard lists the next 14 days. Date of birth is set by HR or by the employee on My profile; only the day and month are shown. A 29 Feb birthday is shown on 28 Feb in other years.

Limits: no pro-rata salary for the exit month (the full month is paid unless HR adjusts it), no final settlement or notice-period recovery, and claims are not split across several payslips.

## Salary advances, employee documents and purchase requests

- **Salary advance** (`/advances/`): an employee asks for an advance (up to 3 months of gross salary, `DJANGO_ADVANCE_MAX_MONTHS`) to repay over 1 to 36 months. Only HR/admin decides and picks the first deduction month; nobody decides on their own request except the admin role. Only one advance can be pending or unpaid at a time. Each payslip generated or recalculated takes the monthly instalment (the last instalment takes whatever is left so the total is exact) and shows it as "Salary advance repayment". The balance counts only published payslips. HR can close an advance early, and the exit page shows any balance. Generate payslips month by month in order. The system tracks the repayment; it does not move money.
- **Employee documents** (`/me/documents/`, HR at `/hr/vault/`): PAN, Aadhaar, passport, certificates and so on. Unlike other uploads these are stored outside the media folder (`DJANGO_PRIVATE_ROOT`, default `private/`), have no public URL, and are only downloadable through a permission-checked view by the employee and HR/admin. HR can mark a document verified (a verified document can only be deleted by HR), and expiry dates feed dashboard reminders for the employee and HR. In Docker the `private` volume must be kept and backed up with the database.
- **Purchase requests** (`/purchases/`, vendors at `/hr/vendors/`): the reporting manager (or HR when there is none) approves first. Above `DJANGO_PURCHASE_LIMIT` (default 10000) a second approval from HR/admin is needed, and it cannot be the same person (the admin role can do both). HR marks the request ordered (vendor, PO number) and received (real cost). If the request is charged to a project, receiving it adds an approved expense to that project's budget and triggers the budget alerts.

Limits: older uploads (task attachments, claim receipts, candidate resumes, company documents) are still served by URL; only employee documents are private. Advances are deducted from payslips, so an employee with no payslip in a month is not charged that month.

## Employee Home, sign up and accounts

- **Home** (`/`): every employee's own dashboard: check in/out, "waiting for you", my tasks, leave balance, next holidays, latest payslip, announcements, birthdays and quick actions. The old team dashboard is now **All tasks** (`/tasks/`).
- **Sign up** (`/signup/`): linked from the login page. By default an account is inactive until HR approves it under **HR > Sign ups and new staff**. Rejecting deletes the pending account.
- **Add employee** (HR): HR creates a login with a temporary password. The employee must choose a new password at first login. Only admin can create HR accounts.
- Settings (environment variables):
  - `DJANGO_SIGNUP_MODE`: `approval` (default), `open` (active immediately) or `off`.
  - `DJANGO_SIGNUP_DOMAINS`: comma-separated allowed email domains (empty = any).
  - `DJANGO_SIGNUP_MAX_PER_HOUR`: sign ups per IP per hour (default 5). Uses the Django cache.
- Run `python manage.py migrate` (migration 0016).
