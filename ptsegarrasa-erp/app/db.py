"""Database layer SegarRasa ERP.

Berisi: skema SQLite, migrasi kolom untuk database lama, seed baseline sesuai
handout PT Segar Rasa Riau, penomoran dokumen unik per jenis (SR-15), helper
pajak efektif 11% half-up per line (SR-14), audit trail, stock move, dan jurnal.
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from flask import current_app, g
from werkzeug.security import generate_password_hash

WIB = timezone(timedelta(hours=7))

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
 full_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS customers (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT, city TEXT,
 credit_limit INTEGER NOT NULL DEFAULT 0, payment_terms TEXT,
 opening_ar INTEGER NOT NULL DEFAULT 0, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS vendors (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, payment_terms TEXT,
 tax_status TEXT, opening_ap INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
 sku TEXT PRIMARY KEY, name TEXT NOT NULL, size TEXT, color TEXT, unit TEXT,
 sales_price INTEGER NOT NULL, standard_cost INTEGER NOT NULL,
 default_vendor TEXT, active INTEGER DEFAULT 1,
 FOREIGN KEY(default_vendor) REFERENCES vendors(code)
);
CREATE TABLE IF NOT EXISTS vendor_pricelist (
 vendor_code TEXT NOT NULL, sku TEXT NOT NULL, min_qty INTEGER NOT NULL DEFAULT 1,
 purchase_price INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'IDR',
 PRIMARY KEY(vendor_code, sku),
 FOREIGN KEY(vendor_code) REFERENCES vendors(code), FOREIGN KEY(sku) REFERENCES products(sku)
);
CREATE TABLE IF NOT EXISTS warehouses (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, type TEXT
);
CREATE TABLE IF NOT EXISTS stock (
 sku TEXT NOT NULL, warehouse_code TEXT NOT NULL, on_hand INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(sku, warehouse_code),
 FOREIGN KEY(sku) REFERENCES products(sku),
 FOREIGN KEY(warehouse_code) REFERENCES warehouses(code)
);
CREATE TABLE IF NOT EXISTS reservations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, document_no TEXT NOT NULL,
 sku TEXT NOT NULL, warehouse_code TEXT NOT NULL, quantity INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS stock_moves (
 id INTEGER PRIMARY KEY AUTOINCREMENT, reference TEXT NOT NULL, move_type TEXT NOT NULL,
 sku TEXT NOT NULL, from_location TEXT, to_location TEXT, quantity INTEGER NOT NULL,
 unit_cost INTEGER NOT NULL DEFAULT 0, value INTEGER NOT NULL DEFAULT 0,
 created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, customer_code TEXT,
 title TEXT NOT NULL, stage TEXT DEFAULT 'New', notes TEXT,
 expected_sku TEXT, expected_qty INTEGER DEFAULT 0, quotation_id INTEGER,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lead_stage_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, lead_id INTEGER NOT NULL, from_stage TEXT,
 to_stage TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(lead_id) REFERENCES leads(id)
);
CREATE TABLE IF NOT EXISTS sales_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL,
 customer_code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Quotation',
 discount_pct REAL NOT NULL DEFAULT 0, subtotal INTEGER NOT NULL DEFAULT 0,
 tax INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
 warehouse_code TEXT NOT NULL, created_by TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT,
 lead_id INTEGER, revision_of INTEGER, discount_approved_by TEXT, discount_approved_at TEXT,
 cancelled_at TEXT,
 FOREIGN KEY(customer_code) REFERENCES customers(code)
);
CREATE TABLE IF NOT EXISTS sales_order_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL, discount_pct REAL DEFAULT 0,
 subtotal INTEGER NOT NULL, tax INTEGER NOT NULL, total INTEGER NOT NULL,
 FOREIGN KEY(order_id) REFERENCES sales_orders(id), FOREIGN KEY(sku) REFERENCES products(sku)
);
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, order_id INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'Ready', warehouse_code TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, validated_at TEXT, validated_by TEXT,
 FOREIGN KEY(order_id) REFERENCES sales_orders(id)
);
CREATE TABLE IF NOT EXISTS delivery_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, FOREIGN KEY(delivery_id) REFERENCES deliveries(id)
);
CREATE TABLE IF NOT EXISTS purchase_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, vendor_code TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'RFQ', subtotal INTEGER DEFAULT 0, tax INTEGER DEFAULT 0,
 total INTEGER DEFAULT 0, created_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 confirmed_at TEXT, approved_by TEXT, approved_at TEXT,
 FOREIGN KEY(vendor_code) REFERENCES vendors(code)
);
CREATE TABLE IF NOT EXISTS purchase_order_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_cost INTEGER NOT NULL, subtotal INTEGER NOT NULL,
 tax INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(order_id) REFERENCES purchase_orders(id), FOREIGN KEY(sku) REFERENCES products(sku)
);
CREATE TABLE IF NOT EXISTS receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, purchase_order_id INTEGER NOT NULL,
 status TEXT DEFAULT 'Waiting', warehouse_code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 created_by TEXT, qc_number TEXT, qc_by TEXT, qc_at TEXT, validated_by TEXT, validated_at TEXT,
 FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
);
CREATE TABLE IF NOT EXISTS receipt_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL, sku TEXT NOT NULL,
 received_qty INTEGER NOT NULL, accepted_qty INTEGER NOT NULL DEFAULT 0,
 rejected_qty INTEGER NOT NULL DEFAULT 0, quality_reason TEXT,
 FOREIGN KEY(receipt_id) REFERENCES receipts(id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, order_id INTEGER NOT NULL,
 status TEXT DEFAULT 'Draft', net_amount INTEGER NOT NULL, tax_amount INTEGER NOT NULL,
 total INTEGER NOT NULL, paid_amount INTEGER DEFAULT 0, credited_amount INTEGER DEFAULT 0,
 payment_status TEXT DEFAULT 'Not Paid', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 posted_at TEXT, posted_by TEXT,
 FOREIGN KEY(order_id) REFERENCES sales_orders(id)
);
CREATE TABLE IF NOT EXISTS invoice_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL, net_amount INTEGER NOT NULL,
 tax_amount INTEGER NOT NULL, discount_amount INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);
CREATE TABLE IF NOT EXISTS vendor_bills (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, purchase_order_id INTEGER NOT NULL,
 receipt_id INTEGER NOT NULL, status TEXT DEFAULT 'Draft', net_amount INTEGER NOT NULL,
 tax_amount INTEGER NOT NULL, total INTEGER NOT NULL, paid_amount INTEGER DEFAULT 0,
 payment_status TEXT DEFAULT 'Not Paid', match_status TEXT DEFAULT 'Matched', match_note TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, posted_at TEXT, posted_by TEXT,
 FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
 FOREIGN KEY(receipt_id) REFERENCES receipts(id)
);
CREATE TABLE IF NOT EXISTS vendor_bill_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, bill_id INTEGER NOT NULL, sku TEXT NOT NULL,
 ordered_qty INTEGER NOT NULL, accepted_qty INTEGER NOT NULL, claimed_qty INTEGER NOT NULL,
 billed_qty INTEGER NOT NULL, unit_cost INTEGER NOT NULL, net_amount INTEGER NOT NULL,
 tax_amount INTEGER NOT NULL, FOREIGN KEY(bill_id) REFERENCES vendor_bills(id)
);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, document_type TEXT NOT NULL,
 document_id INTEGER NOT NULL, amount INTEGER NOT NULL, payment_date TEXT DEFAULT CURRENT_TIMESTAMP,
 created_by TEXT NOT NULL, journal_number TEXT
);
CREATE TABLE IF NOT EXISTS transfers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, from_warehouse TEXT NOT NULL,
 to_warehouse TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
 status TEXT DEFAULT 'Ready', created_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 validated_by TEXT, validated_at TEXT
);
CREATE TABLE IF NOT EXISTS returns (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, invoice_id INTEGER NOT NULL,
 sku TEXT NOT NULL, quantity INTEGER NOT NULL, status TEXT DEFAULT 'Requested',
 credit_total INTEGER NOT NULL DEFAULT 0, credit_net INTEGER NOT NULL DEFAULT 0,
 credit_tax INTEGER NOT NULL DEFAULT 0, cogs_reversal INTEGER NOT NULL DEFAULT 0,
 inspection TEXT DEFAULT 'Saleable', reason TEXT, warehouse_code TEXT,
 credit_note_number TEXT, created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 validated_by TEXT, validated_at TEXT,
 FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);
CREATE TABLE IF NOT EXISTS journal_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, source_type TEXT NOT NULL,
 source_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Posted', reference TEXT, memo TEXT,
 entry_date TEXT, created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS journal_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, journal_id INTEGER NOT NULL, account_code TEXT NOT NULL,
 debit INTEGER DEFAULT 0, credit INTEGER DEFAULT 0, label TEXT,
 FOREIGN KEY(journal_id) REFERENCES journal_entries(id)
);
CREATE TABLE IF NOT EXISTS accounts (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, normal_balance TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
 document_ref TEXT, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sequences (
 key TEXT PRIMARY KEY, value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reorder_points (
    sku TEXT NOT NULL,
    warehouse_code TEXT NOT NULL,
    min_available INTEGER NOT NULL CHECK(min_available >= 0),
    PRIMARY KEY (sku, warehouse_code),
    FOREIGN KEY (sku, warehouse_code)
        REFERENCES stock(sku, warehouse_code)
);
"""

# Kolom baru untuk database lama (buatan versi starter). ALTER TABLE ADD COLUMN aman diulang.
MIGRATIONS = {
    "warehouses": [("type", "TEXT")],
    "reservations": [("created_at", "TEXT")],
    "leads": [("expected_sku", "TEXT"), ("expected_qty", "INTEGER DEFAULT 0"), ("quotation_id", "INTEGER")],
    "sales_orders": [("lead_id", "INTEGER"), ("revision_of", "INTEGER"), ("discount_approved_by", "TEXT"),
                     ("discount_approved_at", "TEXT"), ("cancelled_at", "TEXT")],
    "deliveries": [("validated_by", "TEXT")],
    "purchase_orders": [("confirmed_at", "TEXT"), ("approved_by", "TEXT"), ("approved_at", "TEXT")],
    "purchase_order_lines": [("tax", "INTEGER NOT NULL DEFAULT 0")],
    "receipts": [("created_by", "TEXT"), ("qc_number", "TEXT"), ("qc_by", "TEXT"), ("qc_at", "TEXT"),
                 ("validated_by", "TEXT"), ("validated_at", "TEXT")],
    "invoices": [("credited_amount", "INTEGER DEFAULT 0"), ("payment_status", "TEXT DEFAULT 'Not Paid'"),
                 ("posted_at", "TEXT"), ("posted_by", "TEXT")],
    "invoice_lines": [("discount_amount", "INTEGER NOT NULL DEFAULT 0")],
    "vendor_bills": [("payment_status", "TEXT DEFAULT 'Not Paid'"), ("match_status", "TEXT DEFAULT 'Matched'"),
                     ("match_note", "TEXT"), ("created_at", "TEXT"), ("posted_at", "TEXT"), ("posted_by", "TEXT")],
    "payments": [("journal_number", "TEXT")],
    "transfers": [("created_at", "TEXT"), ("validated_by", "TEXT"), ("validated_at", "TEXT")],
    "returns": [("credit_net", "INTEGER NOT NULL DEFAULT 0"), ("credit_tax", "INTEGER NOT NULL DEFAULT 0"),
                ("cogs_reversal", "INTEGER NOT NULL DEFAULT 0"), ("inspection", "TEXT DEFAULT 'Saleable'"),
                ("reason", "TEXT"), ("warehouse_code", "TEXT"), ("credit_note_number", "TEXT"),
                ("created_by", "TEXT"), ("created_at", "TEXT"), ("validated_by", "TEXT"), ("validated_at", "TEXT")],
    "journal_entries": [("reference", "TEXT"), ("memo", "TEXT"), ("entry_date", "TEXT"), ("created_by", "TEXT")],
    "journal_lines": [("label", "TEXT")],
}

# Format penomoran per jenis dokumen (SR-15: unik per jenis).
NUMBER_FORMATS = {
    "SO": "S{n:05d}",           # S00005 (lanjut dari seed S00004)
    "PO": "PO{n:05d}",          # PO00004 (lanjut dari seed PO00003)
    "CRM": "CRM{n:05d}",        # CRM00010
    "DO": "WH/OUT/{n:04d}",     # Delivery Order
    "IN": "WH/IN/{n:04d}",      # Receipt
    "INT": "WH/INT/{n:04d}",    # Internal Transfer
    "RET": "WH/RET/{n:04d}",    # Return
    "QC": "QC{n:05d}",          # Quality Check
    "INV": "INV/{y}/{n:04d}",   # Customer Invoice
    "BILL": "BILL/{y}/{n:04d}", # Vendor Bill
    "RINV": "RINV/{y}/{n:04d}", # Credit Note
    "PAY": "PAY/{y}/{n:04d}",   # Payment
    "MISC": "MISC{n:05d}",      # Journal Entry
}


def now_wib():
    return datetime.now(WIB)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate(db):
    for table, cols in MIGRATIONS.items():
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    # Status lama 'Draft' pada Sales Order diganti menjadi 'Quotation'
    db.execute("UPDATE sales_orders SET status='Quotation' WHERE status='Draft'")
    db.execute("UPDATE receipts SET status='Waiting' WHERE status='Draft'")
    # Pastikan sequence tidak bentrok dengan nomor seed
    for key, minimum in (("SO", 4), ("PO", 3), ("CRM", 9), ("MISC", 1)):
        db.execute("INSERT INTO sequences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=MAX(value, excluded.value)", (key, minimum))


def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        migrate(db)
        db.commit()
    app.teardown_appcontext(close_db)


def begin(db):
    """Mulai transaksi tulis eksklusif agar posting tidak berjalan ganda (SR-15)."""
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")


def next_number(kind):
    db = get_db()
    key = kind
    fmt = NUMBER_FORMATS.get(kind, kind + "{n:05d}")
    year = now_wib().year
    if "{y}" in fmt:
        key = f"{kind}/{year}"
    row = db.execute("SELECT value FROM sequences WHERE key=?", (key,)).fetchone()
    n = (row["value"] + 1) if row else 1
    db.execute("INSERT INTO sequences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, n))
    return fmt.format(n=n, y=year)


def setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def half_up(x):
    """Pembulatan rupiah terdekat metode half-up (x boleh Fraction)."""
    x = Fraction(x)
    if x >= 0:
        return int((x + Fraction(1, 2)) // 1)
    return -int((-x + Fraction(1, 2)) // 1)


def tax_of(net):
    """PPN efektif = tarif nominal 12% x DPP nilai lain 11/12 = 11%, half-up per line (SR-14)."""
    nominal = Fraction(setting("tax_nominal_rate", "12")) / 100
    factor = Fraction(setting("tax_base_factor", "11/12"))
    return half_up(Fraction(int(net)) * nominal * factor)


def audit(actor, action, ref=None, details=None):
    get_db().execute("INSERT INTO audit_logs(actor,action,document_ref,details) VALUES(?,?,?,?)",
                     (actor, action, ref, details))


def stock_move(reference, move_type, sku, from_loc, to_loc, qty, actor):
    db = get_db()
    cost = db.execute("SELECT standard_cost FROM products WHERE sku=?", (sku,)).fetchone()["standard_cost"]
    db.execute("INSERT INTO stock_moves(reference,move_type,sku,from_location,to_location,quantity,unit_cost,value,created_by)"
               " VALUES(?,?,?,?,?,?,?,?,?)", (reference, move_type, sku, from_loc, to_loc, qty, cost, qty * cost, actor))
    return cost


def post_journal(source_type, source_id, reference, lines, actor, memo=None):
    """Buat jurnal berpasangan. lines = [(akun, debit, kredit, label)]. Menolak jurnal tidak seimbang."""
    lines = [l for l in lines if (l[1] or l[2])]
    dr = sum(l[1] for l in lines)
    cr = sum(l[2] for l in lines)
    if dr != cr:
        raise ValueError(f"Jurnal tidak seimbang: debit {dr} vs kredit {cr}")
    db = get_db()
    no = next_number("MISC")
    cur = db.execute("INSERT INTO journal_entries(number,source_type,source_id,status,reference,memo,entry_date,created_by)"
                     " VALUES(?,?,?,'Posted',?,?,?,?)",
                     (no, source_type, source_id, reference, memo, now_wib().strftime("%Y-%m-%d"), actor))
    jid = cur.lastrowid
    for acct, d, c, label in lines:
        db.execute("INSERT INTO journal_lines(journal_id,account_code,debit,credit,label) VALUES(?,?,?,?,?)",
                   (jid, acct, d, c, label))
    return no


def seed_db():
    db = get_db()
    if db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]:
        return
    users = [
        ("owner", "Ratna Putri", "owner123", "Owner"),
        ("sales", "Dimas Saputra", "sales123", "Sales"),
        ("warehouse", "Yusuf Hidayat", "warehouse123", "Warehouse"),
        ("procurement", "Nina Febriana", "procurement123", "Procurement"),
        ("finance", "Sari Permata", "finance123", "Finance"),
        ("quality", "Tika Maharani", "quality123", "Quality"),
    ]
    for u in users:
        db.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)",
                   (u[0], u[1], generate_password_hash(u[2]), u[3]))
    customers = [
        ("C01", "Toko Melati", "Business", "Pekanbaru", 2000000, "NET14", 400000),
        ("C02", "Resto Pagi Segar", "Business", "Pekanbaru", 1000000, "NET07", 650000),
        ("C03", "Koperasi Sekolah Hijau", "Business", "Kampar", 900000, "NET30", 0),
        ("C04", "Kedai Riau Jaya", "Business", "Pekanbaru", 600000, "COD", 0),
        ("C05", "PT Mitra Event Nusantara", "Business", "Dumai", 3000000, "NET14", 0),
        ("C06", "Grosir Berkah", "Business", "Siak", 1500000, "NET21", 0),
    ]
    db.executemany("INSERT INTO customers VALUES(?,?,?,?,?,?,?,1)", customers)
    vendors = [
        ("V01", "PT Rasa Sejuk Nusantara", "Pekanbaru", "NET14", "PKP", 150000),
        ("V02", "CV Tirta Teh Kopi", "Kampar", "NET21", "PKP", 70000),
        ("V03", "PT Air Bersih Riau", "Pekanbaru", "NET14", "PKP", 0),
        ("V04", "CV Cemilan Bahagia", "Siak", "NET30", "PKP", 0),
    ]
    db.executemany("INSERT INTO vendors VALUES(?,?,?,?,?,?)", vendors)
    products = [
        ("BVG-250-LEM", "Sparkling Lemon", "250 ml", "Kuning", "botol", 15000, 9000, "V01"),
        ("BVG-250-PCH", "Sparkling Peach", "250 ml", "Oranye", "botol", 16000, 10000, "V01"),
        ("BVG-500-LEM", "Sparkling Lemon", "500 ml", "Kuning", "botol", 25000, 15000, "V01"),
        ("BVG-500-PCH", "Sparkling Peach", "500 ml", "Oranye", "botol", 26000, 16000, "V01"),
        ("TEA-350-ORI", "Teh Daun", "350 ml", "Cokelat", "botol", 12000, 7000, "V02"),
        ("TEA-350-LYC", "Teh Leci", "350 ml", "Bening", "botol", 14000, 8000, "V02"),
        ("COF-250-LAT", "Kopi Latte", "250 ml", "Krem", "kaleng", 22000, 13000, "V02"),
        ("COF-250-MOC", "Kopi Mocha", "250 ml", "Cokelat", "kaleng", 23000, 14000, "V02"),
        ("WTR-600-PLN", "Air Mineral", "600 ml", "Bening", "botol", 7000, 3000, "V03"),
        ("WTR-1500-PLN", "Air Mineral", "1.500 ml", "Bening", "botol", 12000, 6000, "V03"),
        ("SNK-060-BBQ", "Keripik BBQ", "60 g", "Merah", "pak", 10000, 6000, "V04"),
        ("SNK-060-SEA", "Keripik Rumput Laut", "60 g", "Hijau", "pak", 10000, 6000, "V04"),
        ("SNK-120-BBQ", "Keripik BBQ", "120 g", "Merah", "pak", 18000, 11000, "V04"),
        ("NUT-080-HNY", "Kacang Madu", "80 g", "Emas", "pak", 19000, 12000, "V04"),
        ("BIS-090-CHO", "Biskuit Cokelat", "90 g", "Cokelat", "pak", 17000, 10000, "V04"),
    ]
    db.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,1)", products)
    for p in products:  # vendor_pricelist: harga beli = standard cost
        db.execute("INSERT INTO vendor_pricelist VALUES(?,?,1,?,'IDR')", (p[7], p[0], p[6]))
    db.executemany("INSERT INTO warehouses VALUES(?,?,?,?)", [
        ("WH-PKU", "Gudang Utama Pekanbaru", "Pekanbaru", "Internal Warehouse"),
        ("OUT-MRP", "Titik Ambil Marpoyan", "Pekanbaru", "Internal Outlet"),
    ])
    stock_rows = [
        ("BVG-250-LEM", 50, 12), ("BVG-250-PCH", 32, 8), ("BVG-500-LEM", 30, 10), ("BVG-500-PCH", 28, 6),
        ("TEA-350-ORI", 60, 18), ("TEA-350-LYC", 48, 12), ("COF-250-LAT", 26, 8), ("COF-250-MOC", 24, 5),
        ("WTR-600-PLN", 100, 20), ("WTR-1500-PLN", 45, 10), ("SNK-060-BBQ", 42, 14), ("SNK-060-SEA", 35, 9),
        ("SNK-120-BBQ", 22, 6), ("NUT-080-HNY", 20, 5), ("BIS-090-CHO", 34, 10),
    ]
    for sku, wh, out in stock_rows:
        db.execute("INSERT INTO stock VALUES(?,?,?)", (sku, "WH-PKU", wh))
        db.execute("INSERT INTO stock VALUES(?,?,?)", (sku, "OUT-MRP", out))
    accounts = [
        ("1010", "Bank", "Asset", "Debit"), ("1020", "Cash on Hand", "Asset", "Debit"),
        ("1100", "Accounts Receivable", "Asset", "Debit"), ("1200", "Stock Valuation", "Asset", "Debit"),
        ("1210", "Stock Interim Received / GRNI", "Liability", "Credit"), ("1300", "VAT Input", "Asset", "Debit"),
        ("2100", "Accounts Payable", "Liability", "Credit"), ("2200", "VAT Output", "Liability", "Credit"),
        ("3100", "Opening Equity", "Equity", "Credit"), ("4100", "Sales Revenue", "Revenue", "Credit"),
        ("4110", "Sales Discounts", "Contra Revenue", "Debit"), ("5100", "Cost of Goods Sold", "Expense", "Debit"),
        ("6100", "Quality Loss", "Expense", "Debit"), ("6200", "Shipping Expense", "Expense", "Debit"),
        ("6300", "Bank Charges", "Expense", "Debit"),
    ]
    db.executemany("INSERT INTO accounts VALUES(?,?,?,?)", accounts)

    # Dokumen terbuka baseline (existing_documents)
    db.execute("INSERT INTO sales_orders(number,customer_code,status,discount_pct,subtotal,tax,total,warehouse_code,"
               "created_by,created_at,confirmed_at) VALUES('S00004','C01','Confirmed',0,150000,16500,166500,'WH-PKU',"
               "'seed','2026-10-01 02:00:00','2026-10-01 02:00:00')")
    soid = db.execute("SELECT id FROM sales_orders WHERE number='S00004'").fetchone()["id"]
    db.execute("INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price,discount_pct,subtotal,tax,total) "
               "VALUES(?,?,?,?,?,?,?,?)", (soid, "BVG-250-LEM", 10, 15000, 0, 150000, 16500, 166500))
    db.execute("INSERT INTO reservations(document_no,sku,warehouse_code,quantity,active,created_at) "
               "VALUES('S00004','BVG-250-LEM','WH-PKU',10,1,'2026-10-01 02:00:00')")
    db.execute("INSERT INTO purchase_orders(number,vendor_code,status,subtotal,tax,total,created_by,created_at,confirmed_at) "
               "VALUES('PO00003','V01','Confirmed',180000,19800,199800,'seed','2026-10-01 02:00:00','2026-10-01 02:00:00')")
    poid = db.execute("SELECT id FROM purchase_orders WHERE number='PO00003'").fetchone()["id"]
    db.execute("INSERT INTO purchase_order_lines(order_id,sku,quantity,unit_cost,subtotal,tax) VALUES(?,?,?,?,?,?)",
               (poid, "BVG-500-LEM", 12, 15000, 180000, 19800))
    db.execute("INSERT INTO leads(number,customer_code,title,stage,notes,expected_sku,expected_qty,created_at) "
               "VALUES('CRM00009','C03','Prospek 30 Sparkling Lemon 250 ml','Qualified','Baseline opportunity',"
               "'BVG-250-LEM',30,'2026-10-01 02:00:00')")
    for key, value in [("tax_code", "VAT11"), ("tax_nominal_rate", "12"), ("tax_base_factor", "11/12"),
                       ("tax_effective_rate", "0.11"), ("tax_rounding", "Half-up per invoice line"),
                       ("large_po_threshold", "1000000"), ("discount_approval_pct", "10"),
                       ("max_discount_pct", "20")]:
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, value))
    for key, value in (("SO", 4), ("PO", 3), ("CRM", 9), ("MISC", 1)):
        db.execute("INSERT OR REPLACE INTO sequences(key,value) VALUES(?,?)", (key, value))

    # Jurnal pembukaan 01/10/2026
    cur = db.execute("INSERT INTO journal_entries(number,source_type,source_id,status,reference,memo,entry_date,created_by,"
                     "created_at) VALUES('MISC00001','Opening',0,'Posted','OPENING','Saldo awal','2026-10-01','seed',"
                     "'2026-10-01 02:00:00')")
    jid = cur.lastrowid
    for acct, dr, cr, label in [("1010", 7000000, 0, "Opening bank"), ("1100", 1050000, 0, "Opening AR C01+C02"),
                                ("1200", 6314000, 0, "Opening inventory"), ("2100", 0, 220000, "Opening AP V01+V02"),
                                ("3100", 0, 14144000, "Opening equity")]:
        db.execute("INSERT INTO journal_lines(journal_id,account_code,debit,credit,label) VALUES(?,?,?,?,?)",
                   (jid, acct, dr, cr, label))
    db.execute("INSERT INTO audit_logs(actor,action,document_ref,details,created_at) VALUES(?,?,?,?,?)",
               ("seed", "Confirmed + reserve", "S00004", "Imported baseline; reservasi 10 unit BVG-250-LEM WH-PKU",
                "2026-10-01 02:00:00"))
    db.execute("INSERT INTO audit_logs(actor,action,document_ref,details,created_at) VALUES(?,?,?,?,?)",
               ("seed", "Imported baseline", "PO00003", "Confirmed; belum diterima", "2026-10-01 02:00:00"))
    db.execute("INSERT INTO audit_logs(actor,action,document_ref,details,created_at) VALUES(?,?,?,?,?)",
               ("seed", "Imported baseline", "CRM00009", "Stage Qualified", "2026-10-01 02:00:00"))
    db.commit()


RESET_TABLES = ["journal_lines", "journal_entries", "returns", "transfers", "payments", "vendor_bill_lines",
                "vendor_bills", "invoice_lines", "invoices", "receipt_lines", "receipts", "purchase_order_lines",
                "purchase_orders", "delivery_lines", "deliveries", "sales_order_lines", "sales_orders",
                "reservations", "stock_moves", "lead_stage_history", "leads", "audit_logs", "reorder_points",
                "stock", "vendor_pricelist", "products", "vendors", "customers", "warehouses", "accounts",
                "sequences", "settings", "users"]
