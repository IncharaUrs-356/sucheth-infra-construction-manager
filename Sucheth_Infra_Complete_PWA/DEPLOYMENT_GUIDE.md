# Sucheth Infra — Complete PWA package

This ZIP contains the application source, PWA files, local SQLite database, and the original Excel workbook.

## 1. Run locally on Windows (VS Code)

1. Extract this ZIP to a normal folder (not inside the ZIP preview).
2. Open the extracted folder in VS Code: **File → Open Folder**.
3. Open **Terminal → New Terminal**.
4. Run:

   ```powershell
   .\run_windows.bat
   ```

5. Wait until Flask reports `Running on http://127.0.0.1:5000`.
6. On the laptop, open `http://127.0.0.1:5000`.
7. On a phone on the same Wi-Fi, use the laptop's Wi-Fi IPv4 address, for example `http://192.168.x.x:5000`. Keep the laptop and server running.

Local demo login (only for the bundled local database):
- Username: `admin`
- Password: `ChangeMe123!`

Change the password before entering new real business data. Keep a backup of `construction.db` and `source-workbook.xlsm`.

## 2. Deploy online for Android PWA installation

The PWA requires HTTPS to be installable from a remote phone. The local LAN URL is not a permanent public app address.

1. Create a **private** GitHub repository.
2. Upload the application source from this folder, but do **not** upload `construction.db`, `source-workbook.xlsm`, `.env`, passwords, or other business data. The included `.gitignore` excludes these data files.
3. In Render, create a new Blueprint from that private repository and review `render.yaml` before deploying.
4. The blueprint uses a paid Starter web service and a 1 GB persistent disk mounted at `/var/data`. Confirm current Render pricing before approving charges.
5. Render generates `SECRET_KEY` and `ADMIN_PASSWORD`. Retrieve the generated `ADMIN_PASSWORD` from the service environment settings. The initial cloud database is new/empty because local business data is intentionally excluded from Git.
6. After the HTTPS site is live, sign in and import the workbook using the app's Excel import feature, or arrange a controlled database migration. Verify project totals and sample records before using it operationally.
7. On Android Chrome, open the HTTPS URL → menu → **Install app**. The manifest and service worker are included and the page registers the service worker.

## Important data and security notes

- The bundled `construction.db` and `source-workbook.xlsm` are provided for local use and reference. They contain business records; do not publish them in a public repository or commit them to GitHub.
- `.gitignore` excludes both files from Git. Check `git status` before pushing.
- Keep independent backups. A persistent disk is not a substitute for backups.
- The included app is a functional starting build, not an independently security-audited production ERP. Test all accounting totals, user permissions, backup/restore, and import results before relying on it for live business operations.
- SQLite on one persistent disk is intended for a small, single-instance deployment. For higher concurrency or multiple app instances, migrate to managed PostgreSQL.
- Do not share generated admin passwords or expose the local demo credentials.
