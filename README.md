# SegarRasa ERP — Starter Project

Aplikasi web ERP mandiri untuk studi kasus **PT Segar Rasa Riau**, dengan pola navigasi desktop yang terinspirasi workflow ERP/Odoo 17. Ini bukan Odoo dan tidak menggunakan source code/aset Odoo.

## Stack
- Python 3.11+
- Flask 3
- SQLite (database lokal persisten)
- HTML, CSS, Vanilla JavaScript
- Session authentication + role-based authorization (RBAC)

## Menjalankan di VS Code
1. Extract ZIP, lalu buka folder `ptsegarrasa-erp` di VS Code.
2. Buat virtual environment:
   ```bash
   python -m venv .venv
   ```
3. Aktifkan:
   - Windows PowerShell: `.venv\\Scripts\\Activate.ps1`
   - macOS/Linux: `source .venv/bin/activate`
4. Install dependency:
   ```bash
   pip install -r requirements.txt
   ```
5. Salin `env.example` menjadi `.env` (ubah `SECRET_KEY` untuk penggunaan nyata).
6. Jalankan:
   ```bash
   python run.py
   ```
7. Buka `http://127.0.0.1:5000`.

Database SQLite dibuat otomatis di `instance/segarrasa_erp.db` dan baseline seed dimuat pada pertama kali.

## Akun demo
| Role | Username | Password |
|---|---|---|
| Owner | `owner` | `owner123` |
| Sales | `sales` | `sales123` |
| Warehouse | `warehouse` | `warehouse123` |
| Procurement | `procurement` | `procurement123` |
| Finance | `finance` | `finance123` |
| Quality | `quality` | `quality123` |

Ganti password dan secret sebelum deployment publik. Akun ini hanya kredensial demo.

## Modul / cakupan starter
- Home / Dashboard, Contacts, Products, CRM, Sales Orders, Purchase Orders, Inventory, Delivery, Receipt & Quality, Customer Invoices, Vendor Bills, Payments, Internal Transfers, Returns, Accounting, Audit Log.
- Seed master: 6 customer, 4 vendor, 15 SKU, 2 warehouse.
- Seed baseline: stock 749 units, reservation S00004 10 units, PO00003, CRM00009, opening AR/AP/Bank/Inventory and opening journal.
- RBAC enforced at API layer; UI menu visibility is not the security boundary.
- Document sequences and audit events.
- Tax simulation effective 11% (12% x 11/12), half-up per line.
- Stock reservation uses On Hand - Reserved.
- Sales invoice quantity capped by validated delivered quantity not yet invoiced.
- Receipt accepted quantity only increases sellable stock.
- Vendor bill created from accepted receipt quantity.
- Partial payments reject zero/over-residual amounts.
- PO above Rp1,000,000 subtotal enters Pending Approval for Owner.
- Posting endpoints include basic idempotency protection.

## Catatan penting / batas implementasi
Project ini adalah **starter scaffold yang bisa dijalankan**, bukan pengganti ERP production-ready atau bukti bahwa T-01 sampai T-36 telah lulus. Sebelum dinilai, tim harus:
1. Menyelesaikan seluruh layar S-01–S-18 dan seluruh alur UI (sebagian operasi tersedia melalui API).
2. Menambah edit/cancel/revision controls, document smart links, full line editor, access matrix per permission, and comprehensive audit.
3. Memastikan credit exposure, partial delivery/backorder, returns, VAT rounding, vendor three-way match, and accounting entries against every fixture in the PDF.
4. Menulis dan menjalankan Test Results T-01–T-36 dengan expected, actual, pass/fail, screenshots, defect log.
5. Menambah concurrency-safe stock locking, database migrations, CSRF protection, password change/reset, rate limiting, backups, and production deployment configuration.
6. Run security review and accounting reconciliation before public deployment.

### Known simplifications to review
- UI is a compact scaffold; some actions (delivery, invoice posting, receipt validation, payment, return) currently use the JSON API and need forms/buttons in the UI.
- Purchase approval threshold and VAT rate are stored in settings; confirm fixture thresholds and edge cases against all pages of the handout.
- Current return logic is a simplified baseline and needs stronger delivered/invoiced/return traceability and credit-note journal generation.
- Credit exposure and multi-document reservation should be reviewed for all corner cases, especially partial invoicing.
- Accounting module provides journal records and a trial-balance endpoint; implement complete journal validation and every debit/credit posting path before claiming accounting completeness.
- Add a `user_roles` pivot if multi-role users are needed; current assignment is one role per user.
- Use HTTPS, secure cookie settings, proper secrets, and a managed database for public hosting.

## Useful API examples
All endpoints are under `/api`. Login first with `/api/login`.
- `GET /api/dashboard`
- `GET /api/masters/customers`, `/products`, `/vendors`
- `GET /api/stock`
- `POST /api/sales-orders` then `POST /api/sales-orders/<id>/confirm`
- `POST /api/sales-orders/<id>/delivery` then `POST /api/deliveries/<id>/validate`
- `POST /api/invoices/from-order/<id>` then `POST /api/invoices/<id>/post`
- `POST /api/purchase-orders` then `/confirm`
- `POST /api/purchase-orders/<id>/receipt`, `/api/receipts/<id>/validate`
- `POST /api/vendor-bills/from-receipt/<id>`, `/api/vendor-bills/<id>/post`
- `POST /api/payments`, `POST /api/transfers`, `POST /api/returns`
- `GET /api/accounting/trial-balance`, `GET /api/reports`
- `POST /api/seed/reset` (Owner only; destructive reset)

## Project structure
```text
app/
  __init__.py
  auth.py
  db.py
  routes.py
  templates/index.html
  static/style.css
  static/app.js
tests/
docs/
run.py
requirements.txt
```
