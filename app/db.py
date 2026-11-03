import sqlite3, os
from flask import current_app, g
from werkzeug.security import generate_password_hash

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
CREATE TABLE IF NOT EXISTS warehouses (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT
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
 active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sales_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL,
 customer_code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Draft',
 discount_pct REAL NOT NULL DEFAULT 0, subtotal INTEGER NOT NULL DEFAULT 0,
 tax INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
 warehouse_code TEXT NOT NULL, created_by TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, confirmed_at TEXT,
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
 created_at TEXT DEFAULT CURRENT_TIMESTAMP, validated_at TEXT,
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
 FOREIGN KEY(vendor_code) REFERENCES vendors(code)
);
CREATE TABLE IF NOT EXISTS purchase_order_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_cost INTEGER NOT NULL, subtotal INTEGER NOT NULL,
 FOREIGN KEY(order_id) REFERENCES purchase_orders(id), FOREIGN KEY(sku) REFERENCES products(sku)
);
CREATE TABLE IF NOT EXISTS receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, purchase_order_id INTEGER NOT NULL,
 status TEXT DEFAULT 'Draft', warehouse_code TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
);
CREATE TABLE IF NOT EXISTS receipt_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id INTEGER NOT NULL, sku TEXT NOT NULL,
 received_qty INTEGER NOT NULL, accepted_qty INTEGER NOT NULL, rejected_qty INTEGER NOT NULL,
 quality_reason TEXT, FOREIGN KEY(receipt_id) REFERENCES receipts(id)
);
CREATE TABLE IF NOT EXISTS invoices (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, order_id INTEGER NOT NULL,
 status TEXT DEFAULT 'Draft', net_amount INTEGER NOT NULL, tax_amount INTEGER NOT NULL,
 total INTEGER NOT NULL, paid_amount INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(order_id) REFERENCES sales_orders(id)
);
CREATE TABLE IF NOT EXISTS invoice_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, sku TEXT NOT NULL,
 quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL, net_amount INTEGER NOT NULL,
 tax_amount INTEGER NOT NULL, FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);
CREATE TABLE IF NOT EXISTS vendor_bills (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, purchase_order_id INTEGER NOT NULL,
 receipt_id INTEGER NOT NULL, status TEXT DEFAULT 'Draft', net_amount INTEGER NOT NULL,
 tax_amount INTEGER NOT NULL, total INTEGER NOT NULL, paid_amount INTEGER DEFAULT 0,
 FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
 FOREIGN KEY(receipt_id) REFERENCES receipts(id)
);
CREATE TABLE IF NOT EXISTS payments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, document_type TEXT NOT NULL,
 document_id INTEGER NOT NULL, amount INTEGER NOT NULL, payment_date TEXT DEFAULT CURRENT_TIMESTAMP,
 created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, from_warehouse TEXT NOT NULL,
 to_warehouse TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER NOT NULL,
 status TEXT DEFAULT 'Draft', created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, invoice_id INTEGER NOT NULL,
 sku TEXT NOT NULL, quantity INTEGER NOT NULL, status TEXT DEFAULT 'Draft',
 credit_total INTEGER NOT NULL, FOREIGN KEY(invoice_id) REFERENCES invoices(id)
);
CREATE TABLE IF NOT EXISTS journal_entries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, source_type TEXT NOT NULL,
 source_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Posted',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS journal_lines (
 id INTEGER PRIMARY KEY AUTOINCREMENT, journal_id INTEGER NOT NULL, account_code TEXT NOT NULL,
 debit INTEGER DEFAULT 0, credit INTEGER DEFAULT 0,
 FOREIGN KEY(journal_id) REFERENCES journal_entries(id)
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
 document_ref TEXT, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sequences (
 key TEXT PRIMARY KEY, value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT UNIQUE NOT NULL, customer_code TEXT,
 title TEXT NOT NULL, stage TEXT DEFAULT 'New', notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
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

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

def close_db(e=None):
    db = g.pop("db", None)
    if db is not None: db.close()

def init_db(app):
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        db.commit()
    app.teardown_appcontext(close_db)

def next_number(prefix):
    db = get_db()
    row = db.execute("SELECT value FROM sequences WHERE key=?", (prefix,)).fetchone()
    n = (row["value"] + 1) if row else 1
    db.execute("INSERT INTO sequences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (prefix,n))
    return f"{prefix}{n:05d}"

def audit(actor, action, ref=None, details=None):
    get_db().execute("INSERT INTO audit_logs(actor,action,document_ref,details) VALUES(?,?,?,?)",
                     (actor,action,ref,details))

def seed_db():
    db = get_db()
    if db.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]:
        return
    roles = ["Owner","Sales","Warehouse","Procurement","Finance","Quality"]
    users = [
      ("owner","Owner SegarRasa","owner123","Owner"),
      ("sales","Dimas Saputra","sales123","Sales"),
      ("warehouse","Yusuf Hidayat","warehouse123","Warehouse"),
      ("procurement","Nina Febriana","procurement123","Procurement"),
      ("finance","Sari Permata","finance123","Finance"),
      ("quality","Tika Maharani","quality123","Quality"),
    ]
    for u in users:
        db.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)",
                   (u[0],u[1],generate_password_hash(u[2]),u[3]))
    customers = [
      ("C01","Toko Melati","Business","Pekanbaru",2000000,"NET14",400000),
      ("C02","Resto Pagi Segar","Business","Pekanbaru",1000000,"NET07",650000),
      ("C03","Koperasi Sekolah Hijau","Business","Kampar",900000,"NET30",0),
      ("C04","Kedai Riau Jaya","Business","Pekanbaru",600000,"COD",0),
      ("C05","PT Mitra Event Nusantara","Business","Dumai",3000000,"NET14",0),
      ("C06","Grosir Berkah","Business","Siak",1500000,"NET21",0),
    ]
    db.executemany("INSERT INTO customers VALUES(?,?,?,?,?,?,?,1)", customers)
    vendors = [
      ("V01","PT Rasa Sejuk Nusantara","Pekanbaru","NET14","PKP",150000),
      ("V02","CV Tirta Teh Kopi","Kampar","NET21","PKP",70000),
      ("V03","PT Air Bersih Riau","Pekanbaru","NET14","PKP",0),
      ("V04","CV Cemilan Bahagia","Siak","NET30","PKP",0),
    ]
    db.executemany("INSERT INTO vendors VALUES(?,?,?,?,?,?)", vendors)
    products = [
      ("BVG-250-LEM","Sparkling Lemon","250 ml","Kuning","botol",15000,9000,"V01"),
      ("BVG-250-PCH","Sparkling Peach","250 ml","Oranye","botol",16000,10000,"V01"),
      ("BVG-500-LEM","Sparkling Lemon","500 ml","Kuning","botol",25000,15000,"V01"),
      ("BVG-500-PCH","Sparkling Peach","500 ml","Oranye","botol",26000,16000,"V01"),
      ("TEA-350-ORI","Teh Daun","350 ml","Cokelat","botol",12000,7000,"V02"),
      ("TEA-350-LYC","Teh Leci","350 ml","Bening","botol",14000,8000,"V02"),
      ("COF-250-LAT","Kopi Latte","250 ml","Krem","kaleng",22000,13000,"V02"),
      ("COF-250-MOC","Kopi Mocha","250 ml","Cokelat","kaleng",23000,14000,"V02"),
      ("WTR-600-PLN","Air Mineral","600 ml","Bening","botol",7000,3000,"V03"),
      ("WTR-1500-PLN","Air Mineral","1.500 ml","Bening","botol",12000,6000,"V03"),
      ("SNK-060-BBQ","Keripik BBQ","60 g","Merah","pak",10000,6000,"V04"),
      ("SNK-060-SEA","Keripik Rumput Laut","60 g","Hijau","pak",10000,6000,"V04"),
      ("SNK-120-BBQ","Keripik BBQ","120 g","Merah","pak",18000,11000,"V04"),
      ("NUT-080-HNY","Kacang Madu","80 g","Emas","pak",19000,12000,"V04"),
      ("BIS-090-CHO","Biskuit Cokelat","90 g","Cokelat","pak",17000,10000,"V04"),
    ]
    db.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,1)", products)
    db.executemany("INSERT INTO warehouses VALUES(?,?,?)", [
      ("WH-PKU","Gudang Pusat Pekanbaru","Pekanbaru"),
      ("OUT-MRP","Outlet Marpoyan","Pekanbaru")
    ])
    stock_rows = [
      ("BVG-250-LEM", 50, 12),
      ("BVG-250-PCH", 32, 8),
      ("BVG-500-LEM", 30, 10),
      ("BVG-500-PCH", 28, 6),
      ("TEA-350-ORI", 60, 18),
      ("TEA-350-LYC", 48, 12),
      ("COF-250-LAT",26,8),("COF-250-MOC",24,5),("WTR-600-PLN",100,20),
      ("WTR-1500-PLN",45,10),("SNK-060-BBQ",42,14),("SNK-060-SEA",35,9),
      ("SNK-120-BBQ",22,6),("NUT-080-HNY",20,5),("BIS-090-CHO",34,10)
    ]
    for sku, wh, out in stock_rows:
        db.execute("INSERT INTO stock VALUES(?,?,?)",(sku,"WH-PKU",wh))
        db.execute("INSERT INTO stock VALUES(?,?,?)",(sku,"OUT-MRP",out))
    db.execute("INSERT INTO reservations(document_no,sku,warehouse_code,quantity,active) VALUES('S00004','BVG-250-LEM','WH-PKU',10,1)")
    # Seed existing open documents described in the handout
    db.execute("INSERT INTO sales_orders(number,customer_code,status,discount_pct,subtotal,tax,total,warehouse_code,created_by,confirmed_at) VALUES('S00004','C01','Confirmed',0,150000,16500,166500,'WH-PKU','seed','2026-10-01 09:00:00')")
    soid = db.execute("SELECT id FROM sales_orders WHERE number='S00004'").fetchone()["id"]
    db.execute("INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price,discount_pct,subtotal,tax,total) VALUES(?,?,?,?,?,?,?,?)",
               (soid,"BVG-250-LEM",10,15000,0,150000,16500,166500))
    db.execute("INSERT INTO purchase_orders(number,vendor_code,status,subtotal,tax,total,created_by) VALUES('PO00003','V01','Confirmed',180000,19800,199800,'seed')")
    poid = db.execute("SELECT id FROM purchase_orders WHERE number='PO00003'").fetchone()["id"]
    db.execute("INSERT INTO purchase_order_lines(order_id,sku,quantity,unit_cost,subtotal) VALUES(?,?,?,?,?)",
               (poid,"BVG-500-LEM",12,15000,180000))
    db.execute("INSERT INTO leads(number,customer_code,title,stage,notes) VALUES('CRM00009','C03','Prospek 30 Sparkling Lemon 250 ml','Qualified','Baseline opportunity')")
    db.execute("INSERT INTO settings(key,value) VALUES('tax_effective_rate','0.11')")
    db.execute("INSERT INTO settings(key,value) VALUES('large_po_threshold','1000000')")
    # Opening journal
    db.execute("INSERT INTO journal_entries(number,source_type,source_id,status) VALUES('MISC00001','Opening',0,'Posted')")
    db.execute(
    "INSERT OR IGNORE INTO sequences(key, value) VALUES('MISC', 1)"
    )
    jid = db.execute("SELECT id FROM journal_entries WHERE number='MISC00001'").fetchone()["id"]
    for acct, dr, cr in [("1010",7000000,0),("1100",1050000,0),("1200",6314000,0),("2100",0,220000),("3100",0,14144000)]:
        db.execute("INSERT INTO journal_lines(journal_id,account_code,debit,credit) VALUES(?,?,?,?)",(jid,acct,dr,cr))
    audit("seed","Imported baseline","S00004","Confirmed + reserve 10 unit; 2026-10-01 09:00 WIB")
    db.commit()
