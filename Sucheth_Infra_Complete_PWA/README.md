# Sucheth Infra — Construction Management System

Mobile-first, multi-project construction accounts and contractor billing web application based on `P.BB.ER.14.08.26.xlsm`.

## Included modules

- Multi-project workspace with independent project/site/client/budget details
- Dashboard: total expenses, amount paid, outstanding balance, receipts, category and vendor summaries
- Master expense ledger, receipts, vendor balances, printable payment vouchers and CSV export
- Contractor invoice register with invoice number/date, contractor, category, work period, site, status, tax, retention and deductions
- Item-wise measurement sheet: serial number, work date, floor/location, description, Nos, length, width, height/depth, quantity, unit, rate, amount and remarks
- Automatic measurement calculation: volume (`Nos × L × W × H`), area (`Nos × L × W`), length (`Nos × L`), or Nos; manual quantity can override the calculated quantity
- Measurement line add/edit/remove, invoice summary, contractor payment/advance/part-payment register
- Invoice totals automatically sync to the main expense ledger; payments sync to the invoice's paid amount
- Printable contractor work bill with browser Print / Save as PDF
- Team accounts: admin, manager, accountant and read-only viewer; passwords are hashed; admin can add/disable accounts
- Self-service password change; audit log table for core invoice/account actions
- Excel import for master `M.Sheet`, `Pay Ledger`, and historical weekly labour summaries in `Hamani` / `Hamani (2)`
- Responsive mobile UI and installable PWA shell

## Local setup

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000` on the computer. On the same Wi-Fi network, open `http://COMPUTER-LAN-IP:5000` from a phone. For local first-run demo access:

- Username: `admin`
- Password: `ChangeMe123!`

Change this immediately using Reports & export → Change your password. New installations can set `ADMIN_USERNAME` and `ADMIN_PASSWORD` environment variables before first startup.

## Workbook data

The included `construction.db` contains the original imported project data from the workbook's master ledger (677 expense transactions and 63 receipt entries) plus 56 weekly contractor bill summary rows read from `Hamani` and `Hamani (2)`. The original workbook is also included unchanged.

For a new project, use Reports & export → Import Excel workbook. The importer reads `M.Sheet` expenses, `Pay Ledger` receipts and the weekly top-left contractor summary registers in the two Hamani sheets. Historical multi-column invoice measurement layouts inside the Hamani sheets are complex/formula-driven and are retained in the original workbook rather than silently converted into new measurement lines. Use the new measurement module for bills created in the application.

## Cloud deployment (Render)

A `Dockerfile` and `render.yaml` are included. To deploy:

1. Create a private Git repository and upload this project folder. Do not commit `.env` or secrets.
2. In Render, create a new Blueprint from the repository and review the service settings.
3. The blueprint creates a persistent disk at `/var/data`, sets `APP_DB` to `/var/data/construction.db`, enables secure cookies and generates a secret key and initial admin password.
4. After deploy, open the Render service's environment settings to retrieve the generated `ADMIN_PASSWORD`, sign in as `admin`, and change it immediately.
5. Import the workbook from the app if you want to seed a fresh cloud database. The local `construction.db` is supplied in this ZIP for local use; the Render persistent disk starts empty unless you explicitly migrate/restore it.

For production use, keep the service private to your team where possible, enable backups for the persistent disk, rotate credentials, and test restore procedures. SQLite is suitable for a small team and single app instance; move to managed PostgreSQL before scaling to multiple app instances or high concurrent write volume. The current release is a functional working build, but it has not undergone an independent security audit or production load test.

## Access roles

- **Admin:** full access, team account management
- **Manager / Accountant:** project and financial record entry and management
- **Viewer:** read-only API access

## Accounting behaviour

- Main expense ledger `amount` is the contractor bill/expense value; `paid` is the amount paid.
- New contractor invoices create/update a linked main-ledger expense automatically. Invoice payments update the linked paid amount.
- Client/funding receipts remain separate cash inflows and are not netted against vendor balances.
- Tax, retention and deductions are applied to the measured subtotal to calculate net payable. Confirm local tax treatment with your accountant before issuing tax invoices.

## Version 3 — full entry and payment workflow

### New-entry workflows

Use the top-bar **New entry** selector to create an expense, receipt, labour bill, or contractor invoice without navigating through multiple screens.

- **Expenses:** entry date, bill/invoice number, category, vendor, description, cost code/WBS, unit rate, quantity, subtotal, tax/GST percentage, discount/deduction, calculated total, due date, payment terms, opening amount paid, payment mode, paid-to/by, transaction reference, status and notes.
- **Receipts:** receipt date/number, payer, purpose, amount, mode, bank/deposit account, UTR/reference and notes. Existing receipts can be edited or deleted.
- **Labour register:** contractor, bill/muster-roll number, bill/work dates, work type, description, worker count, days, quantity, unit, rate, bill total, paid amount, status and notes. Existing entries can be edited or deleted.
- **Expense payment register:** record multiple partial payments against a bill, with payment date, amount, mode, UTR/reference, paid-to/by and notes. Payment history is viewable from the transaction list. The app prevents payments from exceeding the bill balance and calculates paid/outstanding status.
- **Filters:** search by vendor, description, bill number or cost code; filter by category, payment status and date range.
- **Full project Excel export:** creates a multi-sheet workbook for project information, expenses, receipts, labour bills, contractor invoices, invoice measurements, expense payments and invoice payments. CSV export remains available for expenses.

### Accounting rules

- Expense total = entered subtotal (or rate × quantity) + tax amount − discount/deduction.
- Payment status can be calculated automatically or manually set to Open, Part-paid, Paid, On hold or Disputed.
- Expense payments are stored individually and roll up to the transaction's paid amount.
- Contractor invoice payments remain managed within the contractor invoice module to avoid duplicate payment entry.
- Receipts are cash inflows and are not netted against vendor expenses.

### Important deployment note

The package includes the original imported SQLite database. The application applies additive database migrations at startup, preserving existing records. Back up `construction.db` before upgrades or manual database changes. The application code passed Python and JavaScript syntax checks and its new SQLite schema/insert statements were checked. A live Flask runtime test could not be run in the build environment because Flask dependencies were not installed there; the launcher installs requirements on the user's computer. Before production use, run through the smoke-test steps below and configure a strong secret key and admin password.

### First-run smoke test

1. Sign in and change the initial password.
2. Create a second project and switch between both projects.
3. Add an expense with quantity, rate, tax and discount; confirm the calculated amount.
4. Add an opening payment, then add a second partial payment and check payment history and outstanding balance.
5. Add, edit and delete a receipt.
6. Add, edit and delete a labour bill.
7. Create a contractor invoice, add measurement lines and a payment, and print it.
8. Export the full project workbook and verify its sheets.
9. Create a viewer account and confirm it cannot make changes.

## PWA deployment safety notes

- The service worker is registered from the root page and is served at `/sw.js` so it can control the whole app scope.
- Keep `construction.db`, `source-workbook.xlsm`, `.env`, and credentials out of Git. The `.gitignore` in this package excludes them.
- Deploy the service with the included Render Blueprint and persistent disk. The configured `starter` web-service plan and persistent disk are paid Render resources; check current pricing before approving deployment.
- The cloud database starts empty on its persistent disk. After deployment, sign in with the generated `ADMIN_PASSWORD` shown in Render environment settings, then use Reports & export → Import Excel workbook to import the original workbook from your computer. The importer reads the documented sheets; keep the original workbook as your offline backup.
- Back up the database and workbook securely. Do not put the workbook or database in a public GitHub repository.

## Android installation (PWA)

This release is an installable Android web app (PWA), not a compiled native APK. Deploy the app to a domain with HTTPS first; then open that HTTPS address in Chrome on Android, sign in, open Chrome's menu, and choose **Install app** or **Add to Home screen**. The installed app gets its own icon, standalone display and mobile navigation. The backend and database remain hosted on the server, so all authorised team devices can use the same project records. Do not expose the local development server to the public internet.

For local testing, a phone can open the computer's LAN address while both are on the same Wi-Fi, but Android installation generally requires HTTPS (or localhost on the same device). Cloud deployment and secure secrets must be configured before real business use.
