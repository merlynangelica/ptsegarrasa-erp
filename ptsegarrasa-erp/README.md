# SegarRasa ERP — PT Segar Rasa Riau

Aplikasi web ERP mandiri untuk studi kasus **PT Segar Rasa Riau** dengan struktur navigasi terinspirasi Odoo Web 17 (App switcher → List/Kanban → Form dengan status bar, smart buttons, Log Notes). Bukan Odoo dan tidak memakai source code/aset Odoo.

## Stack
Python 3.11+ · Flask 3 · SQLite · HTML/CSS/Vanilla JS · session auth + RBAC di API.

## Menjalankan di Antigravity (atau VS Code)
1. Extract ZIP, lalu di Antigravity pilih **File → Open Folder** dan buka folder `ptsegarrasa-erp`.
2. Buka terminal (**Terminal → New Terminal**) lalu buat virtual environment:
   ```bash
   python -m venv .venv
   ```
3. Aktifkan:
   - Windows PowerShell: `.venv\Scripts\Activate.ps1` (jika diblokir: `Set-ExecutionPolicy -Scope Process Bypass`)
   - macOS/Linux: `source .venv/bin/activate`
4. Install dependency: `pip install -r requirements.txt`
5. Salin `env.example` menjadi `.env` (ganti `SECRET_KEY` untuk deployment).
6. **Penting jika sebelumnya sudah pernah run versi lama:** hapus `instance/segarrasa_erp.db` supaya baseline seed dan penomoran (S00005, PO00004, CRM00010) dibuat ulang. Alternatif: login Owner → Home → *Reset baseline seed*.
7. Jalankan `python run.py` lalu buka `http://127.0.0.1:5000`.
8. Jalankan uji otomatis T-01–T-36: `python -m pytest -q tests/` (pip install pytest bila belum ada).

## Akun uji (password = username + `123`)
owner (Ratna Putri) · sales (Dimas Saputra) · warehouse (Yusuf Hidayat) · procurement (Nina Febriana) · finance (Sari Permata) · quality (Tika Maharani). Ganti sebelum deployment publik.

## Peta alur di aplikasi
- **Order to Cash:** CRM Pipeline → Convert to Quotation → Confirm (cek diskon, credit exposure, Available) → Create Delivery (partial/backorder) → Validate (COGS) → Create Invoice (≤ delivered) → Post → Register Payment → Return + Credit Note.
- **Procure to Pay:** New RFQ → Confirm (> Rp1.000.000 = Pending Approval Owner) → Receive → Quality Check Pass/Fail → Validate (accepted only, Dr 1200 / Cr 1210 GRNI) → Create Vendor Bill (three-way match) → Post → Register Payment.
- **Inventory:** Stock & Reservation, Internal Transfer (Ready → Validate, idempotent), Stock Moves.
- **Reporting:** KPI Dashboard (drill-through ke dokumen/ledger), Audit Log, Product Analysis, Customer Profitability.

## Akuntansi yang dibukukan otomatis
- Delivery: Dr 5100 COGS / Cr 1200 Stock (standard cost)
- Receipt: Dr 1200 / Cr 1210 GRNI (accepted qty saja)
- Vendor Bill: Dr 1210 + Dr 1300 VAT Input / Cr 2100 AP
- Customer Invoice: Dr 1100 AR + Dr 4110 diskon / Cr 4100 Revenue + Cr 2200 VAT Output
- Payment: Dr 1010 / Cr 1100 (customer) · Dr 2100 / Cr 1010 (vendor)
- Return: Dr 1200 / Cr 5100 (saleable) atau Dr 6100 / Cr 5100 (damaged) + Credit Note Dr 4100 + Dr 2200 / Cr 1100
- Setiap jurnal divalidasi debit = kredit sebelum disimpan.

## Batas yang masih perlu tim kerjakan
- Test Results T-01–T-36 tetap harus dijalankan manual di UI dengan screenshot, expected vs actual, dan defect log. `tests/test_scenarios.py` hanya membuktikan logika API.
- Decision Log, Role Assignment Sheet, Team Agreement, contribution table, video demo.
- Deployment publik: HTTPS, `SESSION_COOKIE_SECURE=1`, `SECRET_KEY` acak, CSRF token, rate limiting login, backup database. SQLite cocok untuk demo; gunakan disk persisten di hosting.
- Pembayaran terhadap opening AR/AP belum dimodelkan (hanya terhadap invoice/bill yang di-post di aplikasi).

## Struktur
```text
app/__init__.py        app factory, konfigurasi session
app/auth.py            RBAC 6 role + daftar 7 app
app/db.py              skema, migrasi, seed baseline, penomoran, pajak, jurnal, stock move
app/routes.py          seluruh API + aturan bisnis SR-01..SR-20
app/templates/index.html
app/static/app.js      UI S-01..S-18
app/static/style.css
tests/test_scenarios.py  uji otomatis T-01..T-36
docs/                  traceability, data model, checklist
```
