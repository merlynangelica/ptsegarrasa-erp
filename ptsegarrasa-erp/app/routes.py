"""API SegarRasa ERP — seluruh aturan bisnis SR-01..SR-20 ditegakkan di sini (bukan di UI)."""
from functools import wraps
from flask import Blueprint, request, jsonify, session, g
from werkzeug.security import check_password_hash
from .db import (get_db, next_number, audit, tax_of, half_up, setting, begin, stock_move,
                 post_journal, RESET_TABLES, seed_db)
from .auth import require, allowed, deny, APPS, permissions_for
from fractions import Fraction

bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------- helpers
def ok(data=None, status=200):
    return jsonify(data if data is not None else {"ok": True}), status


def err(msg, status=400, **extra):
    return jsonify({"error": msg, **extra}), status


def rowdicts(rows):
    return [dict(r) for r in rows]


def user():
    return session.get("user", {}).get("username", "system")


def role():
    return session.get("user", {}).get("role")


def idr(n):
    return "Rp" + f"{int(n or 0):,}".replace(",", ".")


def money(n):  # dipakai endpoint analisis tim
    return half_up(Fraction(str(n)))


def as_int(value, name="nilai"):
    """Validasi bilangan bulat ketat: menolak bool, desimal, teks."""
    if isinstance(value, bool):
        raise ValueError(f"{name} harus bilangan bulat")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError(f"{name} harus bilangan bulat")


def blocked(action, ref, details, msg, status=409, **extra):
    """Tolak aksi dan tetap simpan jejak percobaan di audit (bukti T-10, T-18, T-22, T-24)."""
    g.setdefault("pending_audit", []).append((user(), action, ref, details))
    return err(msg, status, **extra)


def tx(fn):
    """Satu request tulis = satu transaksi BEGIN IMMEDIATE. Gagal -> rollback total."""
    @wraps(fn)
    def wrapped(*a, **kw):
        db = get_db()
        begin(db)
        try:
            resp = fn(*a, **kw)
        except ValueError as e:
            db.rollback()
            resp = err(str(e), 400)
        except Exception:
            db.rollback()
            raise
        status = resp[1] if isinstance(resp, tuple) else resp.status_code
        if status >= 400:
            db.rollback()
        else:
            db.commit()
        pending = g.pop("pending_audit", [])
        if pending:
            for p in pending:
                audit(*p)
            db.commit()
        return resp
    return wrapped


def body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def scalar(sql, args=()):
    r = get_db().execute(sql, args).fetchone()
    return (r[0] if r and r[0] is not None else 0)


# ---------------------------------------------------------------- stock helpers
def reserved_qty(sku, wh, exclude_doc=None):
    sql = "SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE sku=? AND warehouse_code=? AND active=1"
    args = [sku, wh]
    if exclude_doc:
        sql += " AND document_no<>?"
        args.append(exclude_doc)
    return scalar(sql, args)


def on_hand(sku, wh):
    return scalar("SELECT on_hand FROM stock WHERE sku=? AND warehouse_code=?", (sku, wh))


def available(sku, wh):
    return on_hand(sku, wh) - reserved_qty(sku, wh)


def add_stock(sku, wh, qty):
    get_db().execute("INSERT INTO stock(sku,warehouse_code,on_hand) VALUES(?,?,?) ON CONFLICT(sku,warehouse_code) "
                     "DO UPDATE SET on_hand=on_hand+excluded.on_hand", (sku, wh, qty))


def delivered_qty(order_id, sku, status="Done"):
    return scalar("SELECT COALESCE(SUM(dl.quantity),0) FROM delivery_lines dl JOIN deliveries d ON d.id=dl.delivery_id "
                  "WHERE d.order_id=? AND d.status=? AND dl.sku=?", (order_id, status, sku))


def invoiced_qty(order_id, sku, statuses=("Draft", "Posted")):
    q = ",".join("?" * len(statuses))
    return scalar(f"SELECT COALESCE(SUM(il.quantity),0) FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id "
                  f"WHERE i.order_id=? AND il.sku=? AND i.status IN ({q})", (order_id, sku, *statuses))


def returned_qty(order_id, sku, statuses=("Requested", "Done")):
    q = ",".join("?" * len(statuses))
    return scalar(f"SELECT COALESCE(SUM(r.quantity),0) FROM returns r JOIN invoices i ON i.id=r.invoice_id "
                  f"WHERE i.order_id=? AND r.sku=? AND r.status IN ({q})", (order_id, sku, *statuses))


# ---------------------------------------------------------------- credit exposure (SR-09, F-02, F-10)
def credit_info(code, extra=0):
    db = get_db()
    c = one("SELECT * FROM customers WHERE code=?", (code,))
    if not c:
        return None
    open_invoices = scalar("SELECT COALESCE(SUM(i.total-i.paid_amount-i.credited_amount),0) FROM invoices i "
                           "JOIN sales_orders s ON s.id=i.order_id WHERE s.customer_code=? AND i.status='Posted'", (code,))
    # payment terhadap opening AR tidak dimodelkan; opening AR tetap terbuka
    ar_open = c["opening_ar"] + open_invoices
    uninvoiced = 0
    sos = []
    for so in db.execute("SELECT * FROM sales_orders WHERE customer_code=? AND status='Confirmed'", (code,)):
        posted = scalar("SELECT COALESCE(SUM(total),0) FROM invoices WHERE order_id=? AND status='Posted'", (so["id"],))
        part = max(0, so["total"] - posted)  # bagian yang sudah ditagih tidak dihitung dua kali
        if part:
            sos.append({"number": so["number"], "id": so["id"], "uninvoiced": part})
        uninvoiced += part
    exposure = ar_open + uninvoiced
    return {"customer_code": code, "customer_name": c["name"], "credit_limit": c["credit_limit"],
            "payment_terms": c["payment_terms"], "opening_ar": c["opening_ar"], "open_invoice_ar": open_invoices,
            "ar_open": ar_open, "confirmed_uninvoiced": uninvoiced, "uninvoiced_orders": sos,
            "exposure": exposure, "headroom": c["credit_limit"] - exposure,
            "exposure_after": exposure + extra, "headroom_after": c["credit_limit"] - exposure - extra}


# ---------------------------------------------------------------- auth
@bp.post("/login")
def login():
    d = body()
    db = get_db()
    r = one("SELECT * FROM users WHERE username=? AND active=1", (d.get("username", ""),))
    if not r or not check_password_hash(r["password_hash"], d.get("password", "")):
        audit(d.get("username", "?") or "?", "Login failed", None, "Invalid credentials")
        db.commit()
        return err("Username atau password salah", 401)
    session.clear()
    session["user_id"] = r["id"]
    session["user"] = {"username": r["username"], "name": r["full_name"], "role": r["role"]}
    audit(r["username"], "Login", None, r["role"])
    db.commit()
    return ok({"user": session["user"]})


@bp.post("/logout")
def logout():
    if session.get("user"):
        audit(user(), "Logout")
        get_db().commit()
    session.clear()
    return ok()


@bp.get("/me")
@require("dashboard.view")
def me():
    apps = [a for a in APPS if allowed(a["perm"])]
    perms = ["*"] if role() == "Owner" else permissions_for(role())
    return ok({"user": session["user"], "permissions": perms, "apps": apps})


# ---------------------------------------------------------------- dashboard (SR-19, F-19)
def so_open(so):
    if so["status"] in ("Quotation", "Pending Approval"):
        return True
    if so["status"] != "Confirmed":
        return False
    for l in get_db().execute("SELECT * FROM sales_order_lines WHERE order_id=?", (so["id"],)):
        if invoiced_qty(so["id"], l["sku"], ("Posted",)) < l["quantity"]:
            return True
    return False


def po_open(po):
    if po["status"] in ("RFQ", "Pending Approval"):
        return True
    if po["status"] != "Confirmed":
        return False
    for l in get_db().execute("SELECT * FROM purchase_order_lines WHERE order_id=?", (po["id"],)):
        rec = scalar("SELECT COALESCE(SUM(rl.received_qty),0) FROM receipt_lines rl JOIN receipts r ON r.id=rl.receipt_id "
                     "WHERE r.purchase_order_id=? AND rl.sku=? AND r.status='Done'", (po["id"], l["sku"]))
        if rec < l["quantity"]:
            return True
    return False


def account_balance(code, exclude_opening=False):
    sql = "SELECT COALESCE(SUM(jl.debit-jl.credit),0) FROM journal_lines jl JOIN journal_entries j ON j.id=jl.journal_id " \
          "WHERE jl.account_code=? AND j.status='Posted'"
    if exclude_opening:
        sql += " AND j.source_type<>'Opening'"
    return scalar(sql, (code,))


@bp.get("/dashboard")
@require("dashboard.view")
def dashboard():
    db = get_db()
    onhand = scalar("SELECT COALESCE(SUM(on_hand),0) FROM stock")
    reserved = scalar("SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE active=1")
    stockvalue = scalar("SELECT COALESCE(SUM(s.on_hand*p.standard_cost),0) FROM stock s JOIN products p ON p.sku=s.sku")
    sos = db.execute("SELECT * FROM sales_orders").fetchall()
    pos = db.execute("SELECT * FROM purchase_orders").fetchall()
    data = {
        "company": "PT Segar Rasa Riau", "currency": "IDR", "data_cutoff": "Live (baseline seed + transaksi)",
        "on_hand": onhand, "reserved": reserved, "available": onhand - reserved, "stock_value": stockvalue,
        "stock_by_warehouse": rowdicts(db.execute(
            "SELECT s.warehouse_code, SUM(s.on_hand) units, SUM(s.on_hand*p.standard_cost) value FROM stock s "
            "JOIN products p ON p.sku=s.sku GROUP BY s.warehouse_code ORDER BY s.warehouse_code")),
        "open_sales_orders": sum(1 for s in sos if so_open(s)),
        "open_purchase_orders": sum(1 for p in pos if po_open(p)),
        "open_leads": scalar("SELECT COUNT(*) FROM leads WHERE stage NOT IN ('Won','Lost')"),
        "sales_orders": len(sos), "purchase_orders": len(pos),
        "pending_approvals": scalar("SELECT COUNT(*) FROM purchase_orders WHERE status='Pending Approval'")
                             + scalar("SELECT COUNT(*) FROM sales_orders WHERE status='Pending Approval'"),
        "receipts_waiting_qc": scalar("SELECT COUNT(*) FROM receipts WHERE status='Waiting'"),
        "recent_audit": rowdicts(db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8")),
        "finance_visible": allowed("reports.view") or allowed("accounting.view"),
    }
    if data["finance_visible"]:
        revenue = -(account_balance("4100", True) + account_balance("4110", True))
        data.update({
            "revenue_posted": revenue,
            "invoices_posted": scalar("SELECT COUNT(*) FROM invoices WHERE status='Posted'"),
            "invoices_posted_amount": scalar("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='Posted'"),
            "ar": account_balance("1100"), "ap": -account_balance("2100"), "bank": account_balance("1010"),
            "inventory_ledger": account_balance("1200"), "grni": -account_balance("1210"),
            "vat_output": -account_balance("2200"), "vat_input": account_balance("1300"),
            "uninvoiced_confirmed": sum(credit_info(c["code"])["confirmed_uninvoiced"]
                                        for c in db.execute("SELECT code FROM customers")),
        })
    return ok(data)


# ---------------------------------------------------------------- masters
@bp.get("/masters/<kind>")
def masters(kind):
    if not session.get("user_id"):
        return err("Authentication required", 401)
    perm = {"customers": "contacts.view", "vendors": "contacts.view", "products": "products.view",
            "warehouses": "inventory.view", "accounts": "accounting.view", "users": "*",
            "pricelist": "purchase.view"}.get(kind)
    if not perm:
        return err("Unknown master", 404)
    if kind == "vendors" and allowed("purchase.view"):
        perm = "purchase.view"
    if not allowed(perm):
        return deny(perm)
    db = get_db()
    if kind == "users":
        rows = db.execute("SELECT id,username,full_name,role,active FROM users")
    elif kind == "customers":
        rows = db.execute("SELECT * FROM customers ORDER BY code")
    elif kind == "products":
        rows = db.execute("SELECT * FROM products ORDER BY sku")
    elif kind == "pricelist":
        rows = db.execute("SELECT * FROM vendor_pricelist ORDER BY vendor_code, sku")
    else:
        rows = db.execute(f"SELECT * FROM {kind}")
    return ok(rowdicts(rows))


@bp.post("/masters/products")
@require("products.configure")
@tx
def create_product():
    """SR-17: tiap variant SKU unik. Duplikat ditolak dan master tidak berubah (T-06)."""
    d = body()
    sku = str(d.get("sku", "")).strip().upper()
    if not sku or not d.get("name") or not d.get("unit"):
        return err("SKU, nama, dan satuan wajib diisi")
    if one("SELECT 1 FROM products WHERE sku=?", (sku,)):
        return blocked("Blocked duplicate SKU", sku, f"Percobaan membuat SKU {sku} ({d.get('size')})",
                       f"SKU {sku} sudah ada. Setiap varian ukuran/rasa wajib memakai SKU unik (SR-17).")
    price = as_int(d.get("sales_price"), "Sales price")
    cost = as_int(d.get("standard_cost"), "Standard cost")
    if price < 0 or cost < 0:
        return err("Harga/cost tidak boleh negatif")
    vendor = d.get("default_vendor") or None
    get_db().execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,1)",
                     (sku, d["name"], d.get("size"), d.get("color"), d["unit"], price, cost, vendor))
    for wh in ("WH-PKU", "OUT-MRP"):
        get_db().execute("INSERT INTO stock VALUES(?,?,0)", (sku, wh))
    audit(user(), "Create Product", sku, f"{d['name']} {d.get('size') or ''}")
    return ok({"sku": sku}, 201)


@bp.get("/contacts/<code>/credit")
@require("contacts.view")
def contact_credit(code):
    info = credit_info(code)
    if not info:
        return err("Customer tidak ditemukan", 404)
    return ok(info)


@bp.get("/settings/tax")
@require("dashboard.view")
def tax_settings():
    return ok({"tax_code": setting("tax_code", "VAT11"), "label": "PPN barang kemasan non-mewah",
               "nominal_rate": setting("tax_nominal_rate", "12") + "%", "tax_base_factor": setting("tax_base_factor"),
               "effective_rate": "11%", "rounding": setting("tax_rounding"), "scope": "Sale + vendor bill simulasi",
               "example": {"net": 90000, "tax": tax_of(90000), "gross": 90000 + tax_of(90000)}})


# ---------------------------------------------------------------- stock
@bp.get("/stock")
@require("inventory.view")
def stock():
    rows = get_db().execute("""SELECT s.sku,p.name,p.size,p.unit,s.warehouse_code,w.name warehouse,s.on_hand,
      COALESCE((SELECT SUM(r.quantity) FROM reservations r WHERE r.sku=s.sku AND r.warehouse_code=s.warehouse_code AND r.active=1),0) reserved,
      s.on_hand-COALESCE((SELECT SUM(r.quantity) FROM reservations r WHERE r.sku=s.sku AND r.warehouse_code=s.warehouse_code AND r.active=1),0) available,
      p.standard_cost,s.on_hand*p.standard_cost stock_value
      FROM stock s JOIN products p ON p.sku=s.sku JOIN warehouses w ON w.code=s.warehouse_code ORDER BY s.sku,s.warehouse_code""")
    return ok(rowdicts(rows))


@bp.get("/stock/reservations")
@require("inventory.view")
def reservations():
    return ok(rowdicts(get_db().execute("SELECT * FROM reservations WHERE active=1 ORDER BY id")))


# ---------------------------------------------------------------- document lists
LIST_QUERIES = {
    "sales": ("sales.view", "SELECT so.id, so.number, so.customer_code, c.name customer, so.status, so.warehouse_code, "
              "so.discount_pct, so.subtotal, so.tax, so.total, so.created_by, so.created_at, so.confirmed_at "
              "FROM sales_orders so JOIN customers c ON c.code=so.customer_code ORDER BY so.id DESC"),
    "purchase": ("purchase.view", "SELECT po.id, po.number, po.vendor_code, v.name vendor, po.status, po.subtotal, po.tax, "
                 "po.total, po.created_by, po.approved_by, po.created_at FROM purchase_orders po "
                 "JOIN vendors v ON v.code=po.vendor_code ORDER BY po.id DESC"),
    "deliveries": ("delivery.view", "SELECT d.id, d.number, so.number sales_order, c.name customer, d.warehouse_code, "
                   "d.status, d.created_at, d.validated_at FROM deliveries d JOIN sales_orders so ON so.id=d.order_id "
                   "JOIN customers c ON c.code=so.customer_code ORDER BY d.id DESC"),
    "receipts": ("receipt.view", "SELECT r.id, r.number, po.number purchase_order, po.vendor_code vendor, r.warehouse_code, "
                 "r.status, r.qc_number, (SELECT SUM(received_qty) FROM receipt_lines WHERE receipt_id=r.id) received, "
                 "(SELECT SUM(accepted_qty) FROM receipt_lines WHERE receipt_id=r.id) accepted, "
                 "(SELECT SUM(rejected_qty) FROM receipt_lines WHERE receipt_id=r.id) rejected, r.created_at "
                 "FROM receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id ORDER BY r.id DESC"),
    "quality": ("quality.view", "SELECT r.id, COALESCE(r.qc_number,'(To Do)') qc_number, r.number receipt, po.number purchase_order, "
                "CASE WHEN r.status='Waiting' THEN 'To Do' WHEN r.status='Quality Checked' THEN 'Passed / Failed' ELSE 'Closed' END status, "
                "(SELECT SUM(received_qty) FROM receipt_lines WHERE receipt_id=r.id) inspected, "
                "(SELECT SUM(accepted_qty) FROM receipt_lines WHERE receipt_id=r.id) passed, "
                "(SELECT SUM(rejected_qty) FROM receipt_lines WHERE receipt_id=r.id) failed, r.qc_by inspector, r.qc_at "
                "FROM receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id ORDER BY r.id DESC"),
    "invoices": ("invoicing.view", "SELECT i.id, i.number, so.number sales_order, c.name customer, i.status, i.payment_status, "
                 "i.net_amount, i.tax_amount, i.total, i.paid_amount, i.credited_amount, "
                 "i.total-i.paid_amount-i.credited_amount residual, i.created_at, i.posted_at FROM invoices i "
                 "JOIN sales_orders so ON so.id=i.order_id JOIN customers c ON c.code=so.customer_code ORDER BY i.id DESC"),
    "bills": ("bills.view", "SELECT b.id, b.number, po.number purchase_order, po.vendor_code vendor, r.number receipt, b.status, "
              "b.match_status, b.payment_status, b.net_amount, b.tax_amount, b.total, b.paid_amount, "
              "b.total-b.paid_amount residual, b.created_at FROM vendor_bills b JOIN purchase_orders po ON "
              "po.id=b.purchase_order_id JOIN receipts r ON r.id=b.receipt_id ORDER BY b.id DESC"),
    "payments": ("payments.view", "SELECT p.id, p.number, p.document_type, CASE WHEN p.document_type='customer' THEN "
                 "(SELECT number FROM invoices WHERE id=p.document_id) ELSE (SELECT number FROM vendor_bills WHERE id=p.document_id) END document, "
                 "p.amount, p.journal_number, p.created_by, p.payment_date FROM payments p ORDER BY p.id DESC"),
    "transfers": ("transfer.view", "SELECT id, number, sku, quantity, from_warehouse, to_warehouse, status, created_by, "
                  "created_at, validated_at FROM transfers ORDER BY id DESC"),
    "returns": ("returns.view", "SELECT r.id, r.number, i.number invoice, r.sku, r.quantity, r.inspection, r.status, "
                "r.credit_note_number, r.credit_net, r.credit_tax, r.credit_total, r.cogs_reversal, r.created_at "
                "FROM returns r JOIN invoices i ON i.id=r.invoice_id ORDER BY r.id DESC"),
    "leads": ("crm.view", "SELECT l.id, l.number, l.customer_code, c.name customer, l.title, l.stage, l.expected_sku, "
              "l.expected_qty, (SELECT number FROM sales_orders WHERE id=l.quotation_id) quotation, l.created_at "
              "FROM leads l LEFT JOIN customers c ON c.code=l.customer_code ORDER BY l.id DESC"),
    "journals": ("accounting.view", "SELECT j.id, j.number, j.entry_date, j.reference, j.source_type, j.memo, j.status, "
                 "(SELECT SUM(debit) FROM journal_lines WHERE journal_id=j.id) debit, "
                 "(SELECT SUM(credit) FROM journal_lines WHERE journal_id=j.id) credit, j.created_by "
                 "FROM journal_entries j ORDER BY j.id DESC"),
    "moves": ("inventory.view", "SELECT id, reference, move_type, sku, from_location, to_location, quantity, unit_cost, "
              "value, created_by, created_at FROM stock_moves ORDER BY id DESC"),
    "audit": ("audit.view", "SELECT * FROM audit_logs ORDER BY id DESC"),
}


@bp.get("/documents/<kind>")
def documents(kind):
    if not session.get("user_id"):
        return err("Authentication required", 401)
    if kind not in LIST_QUERIES:
        return err("Unknown document type", 404)
    perm, sql = LIST_QUERIES[kind]
    if not allowed(perm):
        return deny(perm)
    return ok(rowdicts(get_db().execute(sql)))


@bp.get("/history/<path:ref>")
@require("dashboard.view")
def history(ref):
    return ok(rowdicts(get_db().execute("SELECT * FROM audit_logs WHERE document_ref=? ORDER BY id", (ref,))))


# ---------------------------------------------------------------- CRM (SR-18, F-04)
LEAD_STAGES = ["New", "Qualified", "Proposition", "Won", "Lost"]


@bp.post("/leads")
@require("crm.create")
@tx
def create_lead():
    d = body()
    if not d.get("title"):
        return err("Judul prospek wajib diisi")
    if d.get("customer_code") and not one("SELECT 1 FROM customers WHERE code=?", (d["customer_code"],)):
        return err("Customer tidak ditemukan")
    no = next_number("CRM")
    cur = get_db().execute("INSERT INTO leads(number,customer_code,title,stage,notes,expected_sku,expected_qty) "
                           "VALUES(?,?,?,?,?,?,?)", (no, d.get("customer_code"), d["title"], "New", d.get("notes"),
                                                     d.get("expected_sku"), as_int(d.get("expected_qty", 0), "Qty")))
    get_db().execute("INSERT INTO lead_stage_history(lead_id,from_stage,to_stage,actor) VALUES(?,?,?,?)",
                     (cur.lastrowid, None, "New", user()))
    audit(user(), "Create Lead", no, d["title"])
    return ok({"id": cur.lastrowid, "number": no}, 201)


@bp.post("/leads/<int:lid>/stage")
@require("crm.edit")
@tx
def lead_stage(lid):
    stage = body().get("stage")
    lead = one("SELECT * FROM leads WHERE id=?", (lid,))
    if not lead:
        return err("Lead tidak ditemukan", 404)
    if stage not in LEAD_STAGES:
        return err("Stage tidak valid")
    if stage == lead["stage"]:
        return ok({"stage": stage, "unchanged": True})
    get_db().execute("UPDATE leads SET stage=? WHERE id=?", (stage, lid))
    get_db().execute("INSERT INTO lead_stage_history(lead_id,from_stage,to_stage,actor) VALUES(?,?,?,?)",
                     (lid, lead["stage"], stage, user()))
    audit(user(), "Stage change", lead["number"], f"{lead['stage']} → {stage}; tidak ada revenue/jurnal")
    return ok({"stage": stage})


@bp.post("/leads/<int:lid>/quotation")
@require("sales.create")
@tx
def lead_to_quotation(lid):
    """Konversi Opportunity -> Quotation. Tidak membuat jurnal/revenue (SR-18, T-08)."""
    lead = one("SELECT * FROM leads WHERE id=?", (lid,))
    if not lead:
        return err("Lead tidak ditemukan", 404)
    if lead["quotation_id"]:
        q = one("SELECT number FROM sales_orders WHERE id=?", (lead["quotation_id"],))
        return ok({"id": lead["quotation_id"], "number": q["number"], "idempotent": True})
    if not lead["customer_code"] or not lead["expected_sku"]:
        return err("Lead belum memiliki customer dan expected SKU")
    d = body()
    lines = d.get("lines") or [{"sku": lead["expected_sku"], "quantity": lead["expected_qty"] or 1}]
    so = build_quotation(lead["customer_code"], lines, d.get("discount_pct", 0), d.get("warehouse_code", "WH-PKU"), lid)
    if isinstance(so, tuple):
        return so
    get_db().execute("UPDATE leads SET quotation_id=?, stage=CASE WHEN stage IN ('New','Qualified') THEN 'Proposition' "
                     "ELSE stage END WHERE id=?", (so["id"], lid))
    if lead["stage"] in ("New", "Qualified"):
        get_db().execute("INSERT INTO lead_stage_history(lead_id,from_stage,to_stage,actor) VALUES(?,?,?,?)",
                         (lid, lead["stage"], "Proposition", user()))
    audit(user(), "Convert to Quotation", lead["number"], f"→ {so['number']}")
    return ok(so, 201)


# ---------------------------------------------------------------- Sales (SR-01, SR-07, SR-08, SR-09, F-05, F-06, F-10)
def price_lines(lines, discount):
    db = get_db()
    max_disc = float(setting("max_discount_pct", "20"))
    if discount < 0 or discount > max_disc:
        raise ValueError(f"Diskon harus 0–{max_disc:g}%")
    prepared = []
    for line in lines:
        p = db.execute("SELECT * FROM products WHERE sku=? AND active=1", (line.get("sku"),)).fetchone()
        qty = as_int(line.get("quantity", 0), "Quantity")
        if not p or qty <= 0:
            raise ValueError("SKU/quantity tidak valid")
        price = as_int(line.get("unit_price", p["sales_price"]), "Unit price")
        if price <= 0:
            raise ValueError("Unit price harus > 0")
        gross = qty * price
        net = half_up(Fraction(gross) * (1 - Fraction(str(discount)) / 100))
        tax = tax_of(net)
        prepared.append({"sku": p["sku"], "qty": qty, "price": price, "list_price": p["sales_price"],
                         "disc": discount, "net": net, "tax": tax, "gross": net + tax})
    return prepared


def effective_discount(order_id=None, prepared=None):
    """Diskon efektif terhadap harga list (diskon % + potongan harga unit)."""
    if prepared is None:
        rows = get_db().execute("SELECT l.*, p.sales_price list_price FROM sales_order_lines l JOIN products p "
                                "ON p.sku=l.sku WHERE order_id=?", (order_id,)).fetchall()
        prepared = [{"qty": r["quantity"], "list_price": r["list_price"], "net": r["subtotal"]} for r in rows]
    listv = sum(x["qty"] * x["list_price"] for x in prepared)
    netv = sum(x["net"] for x in prepared)
    return round((1 - netv / listv) * 100, 2) if listv else 0


def build_quotation(customer, lines, discount, warehouse, lead_id=None, revision_of=None):
    db = get_db()
    if not lines:
        return err("Tambahkan minimal satu product line")
    if not one("SELECT 1 FROM customers WHERE code=? AND active=1", (customer,)):
        return err("Customer tidak ditemukan")
    if not one("SELECT 1 FROM warehouses WHERE code=?", (warehouse,)):
        return err("Gudang tidak ditemukan")
    discount = float(discount or 0)
    prepared = price_lines(lines, discount)
    subtotal = sum(x["net"] for x in prepared)
    tax = sum(x["tax"] for x in prepared)
    no = next_number("SO")
    cur = db.execute("INSERT INTO sales_orders(number,customer_code,status,discount_pct,subtotal,tax,total,warehouse_code,"
                     "created_by,lead_id,revision_of) VALUES(?,?,'Quotation',?,?,?,?,?,?,?,?)",
                     (no, customer, discount, subtotal, tax, subtotal + tax, warehouse, user(), lead_id, revision_of))
    oid = cur.lastrowid
    for x in prepared:
        db.execute("INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price,discount_pct,subtotal,tax,total) "
                   "VALUES(?,?,?,?,?,?,?,?)", (oid, x["sku"], x["qty"], x["price"], x["disc"], x["net"], x["tax"], x["gross"]))
    audit(user(), "Create Quotation", no, f"{customer} · net {idr(subtotal)} · PPN {idr(tax)} · total {idr(subtotal + tax)}")
    return {"id": oid, "number": no, "subtotal": subtotal, "tax": tax, "total": subtotal + tax,
            "effective_discount": effective_discount(prepared=prepared)}


@bp.post("/sales-orders")
@require("sales.create")
@tx
def create_so():
    d = body()
    so = build_quotation(d.get("customer_code"), d.get("lines", []), d.get("discount_pct", 0),
                         d.get("warehouse_code", "WH-PKU"))
    return so if isinstance(so, tuple) else ok(so, 201)


@bp.put("/sales-orders/<int:oid>")
@require("sales.edit")
@tx
def update_so(oid):
    """SR-08: setelah Confirmed harga & diskon terkunci. Perubahan hanya lewat revisi."""
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so:
        return err("SO tidak ditemukan", 404)
    if so["status"] not in ("Quotation", "Pending Approval"):
        return blocked("Blocked edit (locked)", so["number"], f"Percobaan ubah harga/diskon pada status {so['status']}",
                       f"{so['number']} berstatus {so['status']} sehingga harga dan diskon terkunci (SR-08). "
                       "Gunakan tombol Revisi untuk perubahan terkontrol.", locked=True)
    d = body()
    discount = float(d.get("discount_pct", so["discount_pct"]))
    lines = d.get("lines") or [{"sku": l["sku"], "quantity": l["quantity"], "unit_price": l["unit_price"]}
                               for l in db.execute("SELECT * FROM sales_order_lines WHERE order_id=?", (oid,))]
    prepared = price_lines(lines, discount)
    db.execute("DELETE FROM sales_order_lines WHERE order_id=?", (oid,))
    for x in prepared:
        db.execute("INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price,discount_pct,subtotal,tax,total) "
                   "VALUES(?,?,?,?,?,?,?,?)", (oid, x["sku"], x["qty"], x["price"], x["disc"], x["net"], x["tax"], x["gross"]))
    sub = sum(x["net"] for x in prepared)
    tax = sum(x["tax"] for x in prepared)
    db.execute("UPDATE sales_orders SET discount_pct=?, subtotal=?, tax=?, total=?, status='Quotation', "
               "discount_approved_by=NULL, discount_approved_at=NULL WHERE id=?", (discount, sub, tax, sub + tax, oid))
    audit(user(), "Edit Quotation", so["number"], f"Diskon {discount:g}% · total {idr(sub + tax)} (approval di-reset)")
    return ok({"id": oid, "subtotal": sub, "tax": tax, "total": sub + tax})


@bp.post("/sales-orders/<int:oid>/approve-discount")
@require("sales.confirm")
@tx
def approve_discount(oid):
    if role() != "Owner":
        return deny("owner.approve")
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so or so["status"] not in ("Quotation", "Pending Approval"):
        return err("Dokumen tidak menunggu approval", 409)
    get_db().execute("UPDATE sales_orders SET status='Quotation', discount_approved_by=?, discount_approved_at=CURRENT_TIMESTAMP "
                     "WHERE id=?", (user(), oid))
    audit(user(), "Approve discount", so["number"], f"Diskon efektif {effective_discount(oid)}% disetujui Owner")
    return ok({"status": "Quotation", "approved_by": user()})


@bp.post("/sales-orders/<int:oid>/confirm")
@require("sales.confirm")
@tx
def confirm_so(oid):
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so:
        return err("SO tidak ditemukan", 404)
    if so["status"] == "Confirmed":
        return ok({"status": "Confirmed", "number": so["number"], "idempotent": True})
    if so["status"] not in ("Quotation", "Pending Approval"):
        return err(f"SO berstatus {so['status']}", 409)
    eff = effective_discount(oid)
    limit = float(setting("discount_approval_pct", "10"))
    approved_by = so["discount_approved_by"]
    if eff > limit and not approved_by:
        if role() == "Owner":
            approved_by = user()
        else:
            db.execute("UPDATE sales_orders SET status='Pending Approval' WHERE id=?", (oid,))
            db.commit()  # status Pending Approval harus tersimpan meski respons 409
            begin(db)
            return blocked("Approval required", so["number"], f"Diskon efektif {eff}% > {limit:g}% — konfirmasi ditahan",
                           f"Diskon {eff}% melebihi {limit:g}%: konfirmasi ditahan dan butuh approval Owner (SR-08).",
                           status_after="Pending Approval")
    # SR-09 credit exposure
    ci = credit_info(so["customer_code"], so["total"])
    if ci["exposure_after"] > ci["credit_limit"]:
        return blocked("Credit hold", so["number"],
                       f"Exposure {idr(ci['exposure_after'])} > limit {idr(ci['credit_limit'])}",
                       f"Credit limit terlampaui: AR terbuka {idr(ci['ar_open'])} + SO Confirmed belum ditagih "
                       f"{idr(ci['confirmed_uninvoiced'])} + SO ini {idr(so['total'])} = {idr(ci['exposure_after'])} "
                       f"> limit {idr(ci['credit_limit'])}. SO tetap belum Confirmed (SR-09).", credit=ci)
    # SR-03/SR-04 Available = On Hand − Reserved, tidak boleh negatif
    need = {}
    for l in db.execute("SELECT * FROM sales_order_lines WHERE order_id=?", (oid,)):
        need[l["sku"]] = need.get(l["sku"], 0) + l["quantity"]
    for sku, qty in need.items():
        av = available(sku, so["warehouse_code"])
        if qty > av:
            return blocked("Blocked oversell", so["number"], f"{sku}: diminta {qty}, available {av} di {so['warehouse_code']}",
                           f"Available {sku} di {so['warehouse_code']} hanya {av} (On Hand {on_hand(sku, so['warehouse_code'])}"
                           f" − Reserved {reserved_qty(sku, so['warehouse_code'])}). Pesanan {qty} ditolak; "
                           "reservasi pesanan lain tidak boleh dipakai (SR-03/SR-04).")
    for sku, qty in need.items():
        db.execute("INSERT INTO reservations(document_no,sku,warehouse_code,quantity,active) VALUES(?,?,?,?,1)",
                   (so["number"], sku, so["warehouse_code"], qty))
    db.execute("UPDATE sales_orders SET status='Confirmed', confirmed_at=CURRENT_TIMESTAMP, discount_approved_by=? "
               "WHERE id=?", (approved_by, oid))
    audit(user(), "Confirmed + reserve", so["number"],
          f"Reserve {', '.join(f'{k}×{v}' for k, v in need.items())} @ {so['warehouse_code']}; "
          f"exposure {idr(ci['exposure_after'])}/{idr(ci['credit_limit'])}")
    return ok({"status": "Confirmed", "number": so["number"], "exposure": ci["exposure_after"],
               "headroom": ci["headroom_after"]})


@bp.post("/sales-orders/<int:oid>/revise")
@require("sales.edit")
@tx
def revise_so(oid):
    """Revisi terkontrol: SO lama di-Cancel (reservasi dilepas) dan Quotation revisi baru dibuat."""
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so or so["status"] != "Confirmed":
        return err("Hanya SO Confirmed yang dapat direvisi", 409)
    if scalar("SELECT COUNT(*) FROM deliveries WHERE order_id=? AND status='Done'", (oid,)) or \
            scalar("SELECT COUNT(*) FROM invoices WHERE order_id=?", (oid,)):
        return blocked("Blocked revision", so["number"], "Sudah ada delivery/invoice",
                       "SO sudah memiliki Delivery Done atau Invoice; revisi tidak diizinkan.")
    db.execute("UPDATE deliveries SET status='Cancelled' WHERE order_id=? AND status='Ready'", (oid,))
    db.execute("UPDATE reservations SET active=0 WHERE document_no=?", (so["number"],))
    db.execute("UPDATE sales_orders SET status='Cancelled', cancelled_at=CURRENT_TIMESTAMP WHERE id=?", (oid,))
    lines = [{"sku": l["sku"], "quantity": l["quantity"], "unit_price": l["unit_price"]}
             for l in db.execute("SELECT * FROM sales_order_lines WHERE order_id=?", (oid,))]
    new = build_quotation(so["customer_code"], lines, so["discount_pct"], so["warehouse_code"], so["lead_id"], oid)
    if isinstance(new, tuple):
        return new
    audit(user(), "Revision", so["number"], f"Dibatalkan & direvisi menjadi {new['number']}; reservasi dilepas")
    return ok({"cancelled": so["number"], **new}, 201)


@bp.post("/sales-orders/<int:oid>/cancel")
@require("sales.edit")
@tx
def cancel_so(oid):
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so:
        return err("SO tidak ditemukan", 404)
    if so["status"] == "Cancelled":
        return ok({"status": "Cancelled", "idempotent": True})
    if scalar("SELECT COUNT(*) FROM deliveries WHERE order_id=? AND status='Done'", (oid,)):
        return err("SO sudah memiliki delivery Done; tidak dapat dibatalkan", 409)
    db.execute("UPDATE deliveries SET status='Cancelled' WHERE order_id=? AND status='Ready'", (oid,))
    db.execute("UPDATE reservations SET active=0 WHERE document_no=?", (so["number"],))
    db.execute("UPDATE sales_orders SET status='Cancelled', cancelled_at=CURRENT_TIMESTAMP WHERE id=?", (oid,))
    audit(user(), "Cancel", so["number"], "Reservasi dilepas")
    return ok({"status": "Cancelled"})


# ---------------------------------------------------------------- Delivery (SR-02, SR-04, F-08, F-15)
def so_lines_status(so):
    out = []
    for l in get_db().execute("SELECT sol.*, p.name, p.size, p.unit, p.standard_cost FROM sales_order_lines sol "
                              "JOIN products p ON p.sku=sol.sku WHERE sol.order_id=?", (so["id"],)):
        delivered = delivered_qty(so["id"], l["sku"])
        ready = delivered_qty(so["id"], l["sku"], "Ready")
        inv = invoiced_qty(so["id"], l["sku"], ("Posted",))
        inv_draft = invoiced_qty(so["id"], l["sku"], ("Draft",))
        out.append({"sku": l["sku"], "product_name": f"{l['name']} {l['size'] or ''}".strip(), "unit": l["unit"],
                    "ordered_qty": l["quantity"], "delivered_qty": delivered, "pending_ready_qty": ready,
                    "remaining_qty": l["quantity"] - delivered, "backorder_qty": l["quantity"] - delivered,
                    "invoiced_qty": inv, "draft_invoiced_qty": inv_draft,
                    "invoiceable_qty": max(0, delivered - inv - inv_draft),
                    "returned_qty": returned_qty(so["id"], l["sku"], ("Done",)),
                    "unit_price": l["unit_price"], "discount_pct": l["discount_pct"], "subtotal": l["subtotal"],
                    "tax": l["tax"], "total": l["total"], "standard_cost": l["standard_cost"],
                    "on_hand": on_hand(l["sku"], so["warehouse_code"]),
                    "reserved": reserved_qty(l["sku"], so["warehouse_code"]),
                    "available": available(l["sku"], so["warehouse_code"])})
    return out


@bp.get("/sales-orders/for-delivery")
@require("delivery.view")
def sales_orders_for_delivery():
    db = get_db()
    result = []
    for so in db.execute("SELECT so.*, c.name customer_name FROM sales_orders so JOIN customers c ON c.code=so.customer_code "
                         "WHERE so.status='Confirmed' ORDER BY so.id DESC").fetchall():
        lines = so_lines_status(so)
        if any(l["remaining_qty"] - l["pending_ready_qty"] > 0 for l in lines):
            d = dict(so)
            d["lines"] = lines
            result.append(d)
    return ok(result)


@bp.get("/sales-orders/for-invoice")
@require("invoicing.view")
def sales_orders_for_invoice():
    db = get_db()
    result = []
    for so in db.execute("SELECT so.*, c.name customer_name FROM sales_orders so JOIN customers c ON c.code=so.customer_code "
                         "WHERE so.status='Confirmed' ORDER BY so.id DESC").fetchall():
        d = dict(so)
        d["lines"] = so_lines_status(so)
        d["invoiceable_qty"] = sum(l["invoiceable_qty"] for l in d["lines"])
        result.append(d)
    return ok(result)


@bp.post("/sales-orders/<int:oid>/delivery")
@require("delivery.create")
@tx
def create_delivery(oid):
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so:
        return err("Sales Order tidak ditemukan", 404)
    if so["status"] != "Confirmed":
        return err("Sales Order harus berstatus Confirmed untuk membuat pengiriman", 409)
    qtymap = body().get("quantities", {}) or {}
    prepared = []
    for l in so_lines_status(so):
        qty = as_int(qtymap.get(l["sku"], 0), f"Qty {l['sku']}")
        if qty < 0:
            return err(f"Kuantitas untuk {l['sku']} tidak boleh negatif")
        free = l["remaining_qty"] - l["pending_ready_qty"]
        if qty > free:
            return err(f"Kuantitas {qty} melebihi sisa yang dapat dikirim ({free}) untuk {l['sku']}", 409)
        if qty:
            prepared.append((l["sku"], qty))
    if not prepared:
        return err("Kuantitas pengiriman minimal 1 unit")
    no = next_number("DO")
    cur = db.execute("INSERT INTO deliveries(number,order_id,status,warehouse_code) VALUES(?,?,'Ready',?)",
                     (no, oid, so["warehouse_code"]))
    for sku, qty in prepared:
        db.execute("INSERT INTO delivery_lines(delivery_id,sku,quantity) VALUES(?,?,?)", (cur.lastrowid, sku, qty))
    audit(user(), "Create Delivery", no, f"{so['number']} · {sum(q for _, q in prepared)} unit")
    audit(user(), "Create Delivery", so["number"], no)
    return ok({"id": cur.lastrowid, "number": no, "status": "Ready"}, 201)


@bp.get("/deliveries/<int:did>")
@require("delivery.view")
def get_delivery_detail(did):
    return ok(doc_delivery(did))


@bp.post("/deliveries/<int:did>/validate")
@require("delivery.validate")
@tx
def validate_delivery(did):
    db = get_db()
    d = one("SELECT * FROM deliveries WHERE id=?", (did,))
    if not d:
        return err("Delivery tidak ditemukan", 404)
    if d["status"] == "Done":  # SR-15 / T-26 idempotent
        return ok({"status": "Done", "idempotent": True, "number": d["number"],
                   "message": "Delivery sudah divalidasi; tidak ada stock move/jurnal tambahan"})
    if d["status"] != "Ready":
        return err(f"Delivery berstatus {d['status']}", 409)
    if db.execute("UPDATE deliveries SET status='Done', validated_at=CURRENT_TIMESTAMP, validated_by=? WHERE id=? AND "
                  "status='Ready'", (user(), did)).rowcount != 1:
        return ok({"status": "Done", "idempotent": True})
    so = one("SELECT * FROM sales_orders WHERE id=?", (d["order_id"],))
    lines = db.execute("SELECT * FROM delivery_lines WHERE delivery_id=?", (did,)).fetchall()
    total_cogs = 0
    for l in lines:
        oh = on_hand(l["sku"], d["warehouse_code"])
        own = scalar("SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE document_no=? AND sku=? AND "
                     "warehouse_code=? AND active=1", (so["number"], l["sku"], d["warehouse_code"]))
        other = reserved_qty(l["sku"], d["warehouse_code"], so["number"])
        if l["quantity"] > oh:
            return err(f"On Hand {l['sku']} ({oh}) tidak cukup; stok tidak boleh negatif (SR-04)", 409)
        if oh - other < l["quantity"]:
            return err(f"Pengiriman {l['sku']} akan memakai Reserved milik pesanan lain (SR-04)", 409)
        if l["quantity"] > own:
            return err(f"Kuantitas {l['quantity']} melebihi reservasi pesanan ({own}) untuk {l['sku']}", 409)
        db.execute("UPDATE stock SET on_hand=on_hand-? WHERE sku=? AND warehouse_code=?",
                   (l["quantity"], l["sku"], d["warehouse_code"]))
        db.execute("UPDATE reservations SET quantity=quantity-? WHERE document_no=? AND sku=? AND warehouse_code=? AND active=1",
                   (l["quantity"], so["number"], l["sku"], d["warehouse_code"]))
        cost = stock_move(d["number"], "Delivery", l["sku"], d["warehouse_code"], "Customer", l["quantity"], user())
        total_cogs += cost * l["quantity"]
    db.execute("UPDATE reservations SET active=0 WHERE quantity<=0")
    jno = post_journal("Delivery", did, d["number"], [("5100", total_cogs, 0, f"COGS {d['number']}"),
                                                     ("1200", 0, total_cogs, f"Inventory out {d['number']}")],
                       user(), f"Delivery {so['number']} @ standard cost")
    audit(user(), "Validate Delivery", d["number"], f"Stock −{sum(l['quantity'] for l in lines)}; COGS {idr(total_cogs)}; {jno}")
    audit(user(), "Delivery validated", so["number"], f"{d['number']} · {jno}")
    return ok({"status": "Done", "number": d["number"], "total_cogs": total_cogs, "journal_number": jno})


# ---------------------------------------------------------------- Customer Invoice (SR-01, SR-02, SR-14, F-09)
@bp.post("/invoices/from-order/<int:oid>")
@require("invoicing.post")
@tx
def invoice_from_order(oid):
    db = get_db()
    so = one("SELECT * FROM sales_orders WHERE id=?", (oid,))
    if not so or so["status"] != "Confirmed":
        return err("SO belum Confirmed", 409)
    requested = body().get("quantities") or {}
    lines = []
    for l in so_lines_status(so):
        qty = l["invoiceable_qty"]
        if l["sku"] in requested:
            want = as_int(requested[l["sku"]], "Qty")
            if want > qty:
                return blocked("Blocked over-invoice", so["number"],
                               f"{l['sku']} diminta {want}, delivered belum ditagih {qty}",
                               f"Invoice {l['sku']} maksimal {qty} unit (delivered {l['delivered_qty']} − sudah ditagih "
                               f"{l['invoiced_qty'] + l['draft_invoiced_qty']}). Backorder {l['backorder_qty']} unit belum "
                               "boleh ditagih (SR-02).", invoiceable=qty)
            qty = want
        if qty > 0:
            gross = qty * l["unit_price"]
            net = half_up(Fraction(gross) * (1 - Fraction(str(l["discount_pct"])) / 100))
            lines.append((l["sku"], qty, l["unit_price"], net, tax_of(net), gross - net))
    if not lines:
        return blocked("Blocked invoice", so["number"], "Invoicable quantity 0",
                       "Invoicable quantity 0: belum ada Delivery Done yang belum ditagih (SR-01/SR-02).", invoiceable=0)
    net = sum(x[3] for x in lines)
    tax = sum(x[4] for x in lines)
    no = next_number("INV")
    cur = db.execute("INSERT INTO invoices(number,order_id,status,net_amount,tax_amount,total) VALUES(?,?,'Draft',?,?,?)",
                     (no, oid, net, tax, net + tax))
    for x in lines:
        db.execute("INSERT INTO invoice_lines(invoice_id,sku,quantity,unit_price,net_amount,tax_amount,discount_amount) "
                   "VALUES(?,?,?,?,?,?,?)", (cur.lastrowid, *x))
    audit(user(), "Create Customer Invoice", no, f"{so['number']} · net {idr(net)} · PPN {idr(tax)}")
    audit(user(), "Invoice drafted", so["number"], no)
    return ok({"id": cur.lastrowid, "number": no, "net": net, "tax": tax, "total": net + tax}, 201)


@bp.post("/invoices/<int:iid>/post")
@require("invoicing.post")
@tx
def post_invoice(iid):
    db = get_db()
    inv = one("SELECT * FROM invoices WHERE id=?", (iid,))
    if not inv:
        return err("Invoice tidak ditemukan", 404)
    if inv["status"] == "Posted":  # SR-15 / T-36
        return ok({"status": "Posted", "idempotent": True, "number": inv["number"],
                   "message": "Invoice sudah Posted; tidak ada jurnal/AR tambahan"})
    if inv["status"] != "Draft":
        return err("Status invoice tidak valid", 409)
    if db.execute("UPDATE invoices SET status='Posted', posted_at=CURRENT_TIMESTAMP, posted_by=? WHERE id=? AND status='Draft'",
                  (user(), iid)).rowcount != 1:
        return ok({"status": "Posted", "idempotent": True})
    so = one("SELECT * FROM sales_orders WHERE id=?", (inv["order_id"],))
    for l in db.execute("SELECT * FROM invoice_lines WHERE invoice_id=?", (iid,)):  # recheck cap SR-02
        if invoiced_qty(so["id"], l["sku"], ("Posted",)) > delivered_qty(so["id"], l["sku"]):
            return err(f"Invoice melebihi delivered quantity untuk {l['sku']}", 409)
    disc = scalar("SELECT COALESCE(SUM(discount_amount),0) FROM invoice_lines WHERE invoice_id=?", (iid,))
    jno = post_journal("Customer Invoice", iid, inv["number"], [
        ("1100", inv["total"], 0, f"AR {so['customer_code']}"),
        ("4110", disc, 0, "Sales discount"),
        ("4100", 0, inv["net_amount"] + disc, "Sales revenue"),
        ("2200", 0, inv["tax_amount"], "VAT output 11%")], user(), f"Invoice {so['number']}")
    audit(user(), "Post Customer Invoice", inv["number"], f"AR +{idr(inv['total'])}; Revenue {idr(inv['net_amount'])}; "
                                                         f"PPN {idr(inv['tax_amount'])}; {jno}")
    audit(user(), "Invoice posted", so["number"], inv["number"])
    return ok({"status": "Posted", "number": inv["number"], "journal_number": jno})


# ---------------------------------------------------------------- Purchase (SR-05, SR-20, F-11, F-22)
@bp.post("/purchase-orders")
@require("purchase.create")
@tx
def create_po():
    d = body()
    db = get_db()
    lines = d.get("lines", [])
    if not lines:
        return err("Tambahkan product line")
    if not one("SELECT 1 FROM vendors WHERE code=?", (d.get("vendor_code"),)):
        return err("Vendor tidak ditemukan")
    prepared = []
    for x in lines:
        p = one("SELECT * FROM products WHERE sku=?", (x.get("sku"),))
        if not p:
            return err("SKU tidak valid")
        pl = one("SELECT purchase_price FROM vendor_pricelist WHERE vendor_code=? AND sku=?", (d["vendor_code"], p["sku"]))
        qty = as_int(x.get("quantity", 0), "Quantity")
        cost = as_int(x.get("unit_cost") if x.get("unit_cost") not in (None, "") else
                      (pl["purchase_price"] if pl else p["standard_cost"]), "Unit cost")
        if qty <= 0 or cost < 0:
            return err("Quantity/cost tidak valid")
        n = qty * cost
        prepared.append((p["sku"], qty, cost, n, tax_of(n)))
    sub = sum(x[3] for x in prepared)
    tax = sum(x[4] for x in prepared)
    no = next_number("PO")
    cur = db.execute("INSERT INTO purchase_orders(number,vendor_code,status,subtotal,tax,total,created_by) "
                     "VALUES(?,?,'RFQ',?,?,?,?)", (no, d["vendor_code"], sub, tax, sub + tax, user()))
    for x in prepared:
        db.execute("INSERT INTO purchase_order_lines(order_id,sku,quantity,unit_cost,subtotal,tax) VALUES(?,?,?,?,?,?)",
                   (cur.lastrowid, *x))
    audit(user(), "Create RFQ", no, f"{d['vendor_code']} · subtotal {idr(sub)} · gross {idr(sub + tax)}; stock tidak berubah")
    return ok({"id": cur.lastrowid, "number": no, "subtotal": sub, "tax": tax, "total": sub + tax}, 201)


@bp.post("/purchase-orders/<int:oid>/confirm")
@require("purchase.confirm")
@tx
def confirm_po(oid):
    db = get_db()
    po = one("SELECT * FROM purchase_orders WHERE id=?", (oid,))
    if not po:
        return err("PO tidak ditemukan", 404)
    if po["status"] == "Confirmed":
        return ok({"status": "Confirmed", "idempotent": True})
    if po["status"] not in ("RFQ", "Pending Approval"):
        return err("PO bukan RFQ", 409)
    threshold = int(setting("large_po_threshold", "1000000"))
    if po["subtotal"] > threshold and role() != "Owner":
        if po["status"] == "Pending Approval":
            return blocked("Confirm attempt blocked", po["number"], "Masih menunggu approval Owner",
                           "PO masih Pending Approval; hanya Owner yang dapat menyetujui (SR-20).", status_after="Pending Approval")
        db.execute("UPDATE purchase_orders SET status='Pending Approval' WHERE id=?", (oid,))
        audit(user(), "Request PO Approval", po["number"],
              f"Subtotal {idr(po['subtotal'])} > {idr(threshold)}; percobaan Confirm oleh {role()} ditahan")
        return ok({"status": "Pending Approval", "message": f"Subtotal {idr(po['subtotal'])} di atas {idr(threshold)}: "
                                                           "menunggu approval Owner (SR-20)."}, 202)
    approved = user() if po["subtotal"] > threshold else None
    db.execute("UPDATE purchase_orders SET status='Confirmed', confirmed_at=CURRENT_TIMESTAMP, approved_by=COALESCE(?,approved_by), "
               "approved_at=CASE WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP ELSE approved_at END WHERE id=?",
               (approved, approved, oid))
    audit(user(), "Confirm Purchase Order", po["number"], "Approved by Owner" if approved else "Di bawah ambang approval")
    return ok({"status": "Confirmed"})


@bp.post("/purchase-orders/<int:oid>/approve")
@require("purchase.confirm")
@tx
def approve_po(oid):
    if role() != "Owner":
        return deny("owner.approve")
    db = get_db()
    po = one("SELECT * FROM purchase_orders WHERE id=?", (oid,))
    if not po or po["status"] != "Pending Approval":
        return err("PO tidak menunggu approval", 409)
    db.execute("UPDATE purchase_orders SET status='Confirmed', approved_by=?, approved_at=CURRENT_TIMESTAMP, "
               "confirmed_at=CURRENT_TIMESTAMP WHERE id=?", (user(), oid))
    audit(user(), "Approve Purchase Order", po["number"], f"Approved & Confirmed oleh {user()}")
    return ok({"status": "Confirmed", "approved_by": user()})


@bp.post("/purchase-orders/<int:oid>/cancel")
@require("purchase.create")
@tx
def cancel_po(oid):
    po = one("SELECT * FROM purchase_orders WHERE id=?", (oid,))
    if not po:
        return err("PO tidak ditemukan", 404)
    if po["status"] not in ("RFQ", "Pending Approval"):
        return err("Hanya RFQ/Pending Approval yang dapat dibatalkan", 409)
    get_db().execute("UPDATE purchase_orders SET status='Cancelled' WHERE id=?", (oid,))
    audit(user(), "Cancel", po["number"])
    return ok({"status": "Cancelled"})


# ---------------------------------------------------------------- Receipt + Quality (SR-06, SR-11, F-12, F-13)
def received_so_far(poid, sku, exclude_rid=None):
    sql = ("SELECT COALESCE(SUM(rl.received_qty),0) FROM receipt_lines rl JOIN receipts r ON r.id=rl.receipt_id "
           "WHERE r.purchase_order_id=? AND rl.sku=? AND r.status<>'Cancelled'")
    args = [poid, sku]
    if exclude_rid:
        sql += " AND r.id<>?"
        args.append(exclude_rid)
    return scalar(sql, args)


@bp.post("/purchase-orders/<int:oid>/receipt")
@require("receipt.create")
@tx
def create_receipt(oid):
    db = get_db()
    po = one("SELECT * FROM purchase_orders WHERE id=?", (oid,))
    if not po or po["status"] != "Confirmed":
        return err("PO harus Confirmed", 409)
    d = body()
    wh = d.get("warehouse_code", "WH-PKU")
    pol = {l["sku"]: l for l in db.execute("SELECT * FROM purchase_order_lines WHERE order_id=?", (oid,))}
    lines = d.get("lines") or [{"sku": s, "received_qty": l["quantity"] - received_so_far(oid, s)} for s, l in pol.items()]
    prepared = []
    for x in lines:
        sku = x.get("sku")
        if sku not in pol:
            return err(f"SKU {sku} tidak ada di PO")
        qty = as_int(x.get("received_qty", 0), "Received qty")
        if qty <= 0:
            continue
        if x.get("accepted_qty") not in (None, "") or x.get("rejected_qty") not in (None, ""):
            pass  # accepted/rejected hanya boleh diisi Quality Check (SR-11), diabaikan di sini
        already = received_so_far(oid, sku)
        if already + qty > pol[sku]["quantity"]:
            return blocked("Blocked over-receipt", po["number"],
                           f"{sku}: diterima {already}+{qty} > PO {pol[sku]['quantity']}",
                           f"Receipt {qty} unit {sku} melebihi sisa PO ({pol[sku]['quantity'] - already} dari "
                           f"{pol[sku]['quantity']}). Kelebihan tidak diproses — ajukan revisi PO (SR-06).")
        prepared.append((sku, qty))
    if not prepared:
        return err("Tidak ada kuantitas yang diterima")
    no = next_number("IN")
    cur = db.execute("INSERT INTO receipts(number,purchase_order_id,status,warehouse_code,created_by) VALUES(?,?,'Waiting',?,?)",
                     (no, oid, wh, user()))
    for sku, qty in prepared:
        db.execute("INSERT INTO receipt_lines(receipt_id,sku,received_qty,accepted_qty,rejected_qty) VALUES(?,?,?,0,0)",
                   (cur.lastrowid, sku, qty))
    audit(user(), "Create Receipt", no, f"{po['number']} · {', '.join(f'{s}×{q}' for s, q in prepared)} · menunggu QC")
    audit(user(), "Receipt created", po["number"], no)
    return ok({"id": cur.lastrowid, "number": no, "status": "Waiting"}, 201)


@bp.patch("/receipts/<int:rid>/lines")
@require("receipt.view")
@tx
def edit_receipt_lines(rid):
    """Percobaan mengubah accepted qty di luar Quality ditolak (SR-06/SR-11, T-22)."""
    db = get_db()
    r = one("SELECT * FROM receipts WHERE id=?", (rid,))
    if not r:
        return err("Receipt tidak ditemukan", 404)
    lines = body().get("lines", [])
    if any(x.get("accepted_qty") not in (None, "") or x.get("rejected_qty") not in (None, "") for x in lines):
        return blocked("Blocked accepted override", r["number"],
                       f"{role()} mencoba mengubah accepted/rejected setelah QC" if r["qc_number"] else
                       f"{role()} mencoba mengisi accepted tanpa QC",
                       "Accepted/rejected quantity hanya dapat diubah melalui Quality Check oleh role Quality "
                       "(SR-06/SR-11). Unit Fail tidak dapat dinyatakan Accepted.", qc_number=r["qc_number"])
    if r["status"] != "Waiting" or not allowed("receipt.create"):
        return err("Receipt tidak dapat diubah pada status ini", 409)
    for x in lines:
        line = one("SELECT * FROM receipt_lines WHERE receipt_id=? AND sku=?", (rid, x.get("sku")))
        if not line:
            return err("SKU tidak ada di receipt")
        qty = as_int(x.get("received_qty"), "Received qty")
        pol = one("SELECT quantity FROM purchase_order_lines WHERE order_id=? AND sku=?", (r["purchase_order_id"], x["sku"]))
        if qty <= 0 or received_so_far(r["purchase_order_id"], x["sku"], rid) + qty > pol["quantity"]:
            return blocked("Blocked over-receipt", r["number"], f"{x['sku']} → {qty}",
                           "Kuantitas melebihi PO; ajukan revisi PO (SR-06).")
        db.execute("UPDATE receipt_lines SET received_qty=? WHERE id=?", (qty, line["id"]))
    audit(user(), "Edit Receipt", r["number"], "Received qty diubah sebelum QC")
    return ok({"status": r["status"]})


@bp.post("/receipts/<int:rid>/quality-check")
@require("quality.check")
@tx
def quality_check(rid):
    db = get_db()
    r = one("SELECT * FROM receipts WHERE id=?", (rid,))
    if not r:
        return err("Receipt tidak ditemukan", 404)
    if r["status"] == "Done":
        return err("Receipt sudah Done; QC tidak dapat diubah", 409)
    lines = {l["sku"]: l for l in db.execute("SELECT * FROM receipt_lines WHERE receipt_id=?", (rid,))}
    data = {x.get("sku"): x for x in body().get("lines", [])}
    if set(data) != set(lines):
        return err("Isi hasil QC untuk setiap SKU pada receipt")
    changes = []
    for sku, l in lines.items():
        passed = as_int(data[sku].get("passed", 0), "Pass")
        failed = as_int(data[sku].get("failed", 0), "Fail")
        reason = (data[sku].get("reason") or "").strip()
        if passed < 0 or failed < 0 or passed + failed != l["received_qty"]:
            return err(f"{sku}: Pass + Fail harus sama dengan inspected {l['received_qty']}")
        if failed and not reason:
            return err(f"{sku}: alasan Fail wajib diisi")
        changes.append(f"{sku} Pass={passed} Fail={failed}" + (f" ({reason})" if failed else "")
                       + (f" [koreksi dari Pass={l['accepted_qty']} Fail={l['rejected_qty']}]" if r["qc_number"] else ""))
        db.execute("UPDATE receipt_lines SET accepted_qty=?, rejected_qty=?, quality_reason=? WHERE id=?",
                   (passed, failed, reason or None, l["id"]))
    qc = r["qc_number"] or next_number("QC")
    db.execute("UPDATE receipts SET status='Quality Checked', qc_number=?, qc_by=?, qc_at=CURRENT_TIMESTAMP WHERE id=?",
               (qc, user(), rid))
    action = "QC correction" if r["qc_number"] else "Quality Check"
    audit(user(), action, qc, f"{r['number']}: " + "; ".join(changes))
    audit(user(), action, r["number"], f"{qc}: " + "; ".join(changes))
    return ok({"qc_number": qc, "status": "Quality Checked"})


@bp.post("/receipts/<int:rid>/validate")
@require("receipt.validate")
@tx
def validate_receipt(rid):
    db = get_db()
    r = one("SELECT * FROM receipts WHERE id=?", (rid,))
    if not r:
        return err("Receipt tidak ditemukan", 404)
    if r["status"] == "Done":
        return ok({"status": "Done", "idempotent": True})
    if r["status"] != "Quality Checked":
        return err("Quality Check Pass/Fail wajib dilakukan sebelum Receipt divalidasi (SR-11)", 409)
    if db.execute("UPDATE receipts SET status='Done', validated_by=?, validated_at=CURRENT_TIMESTAMP WHERE id=? AND "
                  "status='Quality Checked'", (user(), rid)).rowcount != 1:
        return ok({"status": "Done", "idempotent": True})
    po = one("SELECT * FROM purchase_orders WHERE id=?", (r["purchase_order_id"],))
    value = 0
    for l in db.execute("SELECT * FROM receipt_lines WHERE receipt_id=?", (rid,)).fetchall():
        pol = one("SELECT quantity FROM purchase_order_lines WHERE order_id=? AND sku=?", (po["id"], l["sku"]))
        if received_so_far(po["id"], l["sku"]) > pol["quantity"]:
            return err("Total receipt melebihi PO (SR-06)", 409)
        if l["accepted_qty"]:
            add_stock(l["sku"], r["warehouse_code"], l["accepted_qty"])
            cost = stock_move(r["number"], "Receipt", l["sku"], f"Vendor {po['vendor_code']}", r["warehouse_code"],
                              l["accepted_qty"], user())
            value += cost * l["accepted_qty"]
        if l["rejected_qty"]:
            stock_move(r["number"], "Rejected (not stocked)", l["sku"], f"Vendor {po['vendor_code']}", "Rejected/Return to vendor",
                       0, user())
            audit(user(), "Rejected not stocked", r["number"], f"{l['sku']} {l['rejected_qty']} unit: {l['quality_reason']}")
    jno = post_journal("Receipt", rid, r["number"], [("1200", value, 0, "Stock in (accepted only)"),
                                                     ("1210", 0, value, "GRNI")], user(), f"Receipt {po['number']}") if value else None
    audit(user(), "Validate Receipt", r["number"], f"Accepted value {idr(value)} masuk stock; {jno or 'tanpa jurnal'}")
    audit(user(), "Receipt validated", po["number"], r["number"])
    return ok({"status": "Done", "accepted_value": value, "journal_number": jno})


# ---------------------------------------------------------------- Vendor Bill three-way match (SR-05, SR-06, F-14)
@bp.post("/vendor-bills/from-receipt/<int:rid>")
@require("bills.post")
@tx
def bill_from_receipt(rid):
    db = get_db()
    r = one("SELECT * FROM receipts WHERE id=?", (rid,))
    if not r or r["status"] != "Done":
        return err("Receipt belum tervalidasi", 409)
    existing = one("SELECT * FROM vendor_bills WHERE receipt_id=? AND status<>'Cancelled'", (rid,))
    if existing:
        return ok({"id": existing["id"], "number": existing["number"], "idempotent": True,
                   "message": "Receipt ini sudah memiliki Vendor Bill"})
    po = one("SELECT * FROM purchase_orders WHERE id=?", (r["purchase_order_id"],))
    claimed_map = body().get("claimed") or {}
    rows, notes = [], []
    for l in db.execute("SELECT rl.*, pol.unit_cost, pol.quantity ordered FROM receipt_lines rl JOIN purchase_order_lines pol "
                        "ON pol.order_id=? AND pol.sku=rl.sku WHERE rl.receipt_id=?", (po["id"], rid)):
        claimed = as_int(claimed_map.get(l["sku"], l["accepted_qty"]), "Claimed qty")
        billed = min(claimed, l["accepted_qty"])
        net = billed * l["unit_cost"]
        rows.append((l["sku"], l["ordered"], l["accepted_qty"], claimed, billed, l["unit_cost"], net, tax_of(net)))
        if claimed > l["accepted_qty"]:
            notes.append(f"{l['sku']}: vendor menagih {claimed}, accepted {l['accepted_qty']} → selisih "
                         f"{claimed - l['accepted_qty']} unit ditandai & tidak dibukukan")
    net = sum(x[6] for x in rows)
    tax = sum(x[7] for x in rows)
    no = next_number("BILL")
    status = "Mismatch" if notes else "Matched"
    cur = db.execute("INSERT INTO vendor_bills(number,purchase_order_id,receipt_id,status,net_amount,tax_amount,total,"
                     "match_status,match_note,created_at) VALUES(?,?,?,'Draft',?,?,?,?,?,CURRENT_TIMESTAMP)",
                     (no, po["id"], rid, net, tax, net + tax, status, "; ".join(notes) or None))
    for x in rows:
        db.execute("INSERT INTO vendor_bill_lines(bill_id,sku,ordered_qty,accepted_qty,claimed_qty,billed_qty,unit_cost,"
                   "net_amount,tax_amount) VALUES(?,?,?,?,?,?,?,?,?)", (cur.lastrowid, *x))
    audit(user(), "Create Vendor Bill", no, f"Three-way match {po['number']}/{r['number']}: {status}"
          + (f" — {'; '.join(notes)}" if notes else ""))
    audit(user(), "Bill drafted", po["number"], no)
    return ok({"id": cur.lastrowid, "number": no, "net": net, "tax": tax, "total": net + tax,
               "match_status": status, "match_note": "; ".join(notes)}, 201)


@bp.post("/vendor-bills/<int:bid>/resolve")
@require("bills.post")
@tx
def resolve_bill(bid):
    b = one("SELECT * FROM vendor_bills WHERE id=?", (bid,))
    if not b or b["status"] != "Draft" or b["match_status"] != "Mismatch":
        return err("Bill tidak dalam status Mismatch", 409)
    get_db().execute("UPDATE vendor_bills SET match_status='Matched (accepted qty)' WHERE id=?", (bid,))
    audit(user(), "Resolve mismatch", b["number"], f"Ditagih sebatas accepted qty; selisih dikembalikan ke vendor: {b['match_note']}")
    return ok({"match_status": "Matched (accepted qty)"})


@bp.post("/vendor-bills/<int:bid>/post")
@require("bills.post")
@tx
def post_bill(bid):
    db = get_db()
    b = one("SELECT * FROM vendor_bills WHERE id=?", (bid,))
    if not b:
        return err("Bill tidak ditemukan", 404)
    if b["status"] == "Posted":
        return ok({"status": "Posted", "idempotent": True})
    if b["match_status"] == "Mismatch":
        return blocked("Blocked bill posting", b["number"], b["match_note"],
                       f"Posting ditahan: {b['match_note']}. Selesaikan selisih (tagih sebatas accepted) dulu (SR-05/SR-06).")
    if db.execute("UPDATE vendor_bills SET status='Posted', posted_at=CURRENT_TIMESTAMP, posted_by=? WHERE id=? AND "
                  "status='Draft'", (user(), bid)).rowcount != 1:
        return ok({"status": "Posted", "idempotent": True})
    std = scalar("SELECT COALESCE(SUM(bl.billed_qty*p.standard_cost),0) FROM vendor_bill_lines bl JOIN products p "
                 "ON p.sku=bl.sku WHERE bl.bill_id=?", (bid,))
    po = one("SELECT * FROM purchase_orders WHERE id=?", (b["purchase_order_id"],))
    jno = post_journal("Vendor Bill", bid, b["number"], [
        ("1210", std, 0, "Clear GRNI"),
        ("1200", b["net_amount"] - std if b["net_amount"] > std else 0, std - b["net_amount"] if std > b["net_amount"] else 0,
         "Price difference"),
        ("1300", b["tax_amount"], 0, "VAT input 11%"),
        ("2100", 0, b["total"], f"AP {po['vendor_code']}")], user(), f"Bill {po['number']}")
    audit(user(), "Post Vendor Bill", b["number"], f"AP +{idr(b['total'])}; GRNI cleared {idr(std)}; {jno}")
    return ok({"status": "Posted", "journal_number": jno})


# ---------------------------------------------------------------- Payment (SR-10, F-16)
def pay_status(total, paid, credited=0):
    residual = total - paid - credited
    return "Paid" if residual <= 0 else ("Partial" if paid or credited else "Not Paid")


@bp.post("/payments")
@require("payments.create")
@tx
def payment():
    d = body()
    db = get_db()
    typ = d.get("document_type")
    table = "invoices" if typ == "customer" else "vendor_bills" if typ == "vendor" else None
    if not table:
        return err("Jenis dokumen pembayaran tidak valid")
    try:
        amount = as_int(d.get("amount"), "Jumlah pembayaran")
        docid = as_int(d.get("document_id"), "Dokumen")
    except ValueError as e:
        return blocked("Payment rejected", None, f"Input {d.get('amount')!r}", str(e), 400)
    doc = one(f"SELECT * FROM {table} WHERE id=?", (docid,))
    if not doc or doc["status"] != "Posted":
        return err("Dokumen harus Posted", 409)
    credited = doc["credited_amount"] if typ == "customer" else 0
    residual = doc["total"] - doc["paid_amount"] - credited
    if amount <= 0 or amount > residual:
        return blocked("Payment rejected", doc["number"], f"Amount {idr(amount)} vs residual {idr(residual)}",
                       f"Pembayaran harus positif dan ≤ residual {idr(residual)} (SR-10). Ditolak: {idr(amount)}.",
                       residual=residual)
    no = next_number("PAY")
    paid = doc["paid_amount"] + amount
    status = pay_status(doc["total"], paid, credited)
    db.execute(f"UPDATE {table} SET paid_amount=?, payment_status=? WHERE id=?", (paid, status, docid))
    cur = db.execute("INSERT INTO payments(number,document_type,document_id,amount,created_by) VALUES(?,?,?,?,?)",
                     (no, typ, docid, amount, user()))
    lines = [("1010", amount, 0, "Bank in"), ("1100", 0, amount, "Clear AR")] if typ == "customer" else \
        [("2100", amount, 0, "Clear AP"), ("1010", 0, amount, "Bank out")]
    jno = post_journal("Payment", cur.lastrowid, no, lines, user(), f"Payment {doc['number']}")
    db.execute("UPDATE payments SET journal_number=? WHERE id=?", (jno, cur.lastrowid))
    audit(user(), "Register Payment", no, f"{doc['number']} {idr(amount)}; residual {idr(residual - amount)}; {status}")
    audit(user(), "Payment registered", doc["number"], f"{no} {idr(amount)} → {status}")
    return ok({"id": cur.lastrowid, "number": no, "residual": residual - amount, "status": status,
               "journal_number": jno}, 201)


# ---------------------------------------------------------------- Internal Transfer (SR-12, F-17)
@bp.post("/transfers")
@require("transfer.create")
@tx
def transfer():
    d = body()
    sku, src, dst = d.get("sku"), d.get("from_warehouse"), d.get("to_warehouse")
    qty = as_int(d.get("quantity", 0), "Quantity")
    if not one("SELECT 1 FROM products WHERE sku=?", (sku,)):
        return err("SKU tidak ditemukan")
    if src == dst or qty <= 0 or not one("SELECT 1 FROM warehouses WHERE code=?", (src,)) or \
            not one("SELECT 1 FROM warehouses WHERE code=?", (dst,)):
        return err("Transfer tidak valid (lokasi harus berbeda, qty > 0)")
    av = available(sku, src)
    if qty > av:
        return err(f"Available {sku} di {src} hanya {av} (reservasi pesanan dilindungi)", 409)
    no = next_number("INT")
    cur = get_db().execute("INSERT INTO transfers(number,from_warehouse,to_warehouse,sku,quantity,status,created_by) "
                           "VALUES(?,?,?,?,?,'Ready',?)", (no, src, dst, sku, qty, user()))
    audit(user(), "Create Transfer", no, f"{src} → {dst}, {sku} × {qty}")
    resp = {"id": cur.lastrowid, "number": no, "status": "Ready"}
    return ok(resp, 201)


@bp.post("/transfers/<int:tid>/validate")
@require("transfer.validate")
@tx
def validate_transfer(tid):
    db = get_db()
    t = one("SELECT * FROM transfers WHERE id=?", (tid,))
    if not t:
        return err("Transfer tidak ditemukan", 404)
    if t["status"] == "Done":  # T-30
        return ok({"status": "Done", "idempotent": True, "message": "Transfer sudah Done; stock move kedua tidak dibuat"})
    if db.execute("UPDATE transfers SET status='Done', validated_by=?, validated_at=CURRENT_TIMESTAMP WHERE id=? AND "
                  "status='Ready'", (user(), tid)).rowcount != 1:
        return ok({"status": "Done", "idempotent": True})
    av = available(t["sku"], t["from_warehouse"])
    if t["quantity"] > av:
        return err(f"Available di {t['from_warehouse']} hanya {av}", 409)
    db.execute("UPDATE stock SET on_hand=on_hand-? WHERE sku=? AND warehouse_code=?", (t["quantity"], t["sku"], t["from_warehouse"]))
    add_stock(t["sku"], t["to_warehouse"], t["quantity"])
    stock_move(t["number"], "Internal Transfer", t["sku"], t["from_warehouse"], t["to_warehouse"], t["quantity"], user())
    audit(user(), "Validate Transfer", t["number"], f"{t['from_warehouse']} −{t['quantity']} / {t['to_warehouse']} +{t['quantity']}; "
                                                   "nilai persediaan total tidak berubah (tanpa jurnal)")
    return ok({"status": "Done", "number": t["number"]})


# ---------------------------------------------------------------- Return & Credit Note (SR-13, SR-14, F-18)
def return_cap(inv, sku, statuses=("Requested", "Done"), exclude_id=None):
    line_qty = scalar("SELECT COALESCE(SUM(quantity),0) FROM invoice_lines WHERE invoice_id=? AND sku=?", (inv["id"], sku))
    sql = "SELECT COALESCE(SUM(quantity),0) FROM returns WHERE invoice_id=? AND sku=? AND status IN ({})".format(
        ",".join("?" * len(statuses)))
    args = [inv["id"], sku, *statuses]
    if exclude_id:
        sql += " AND id<>?"
        args.append(exclude_id)
    ret_inv = scalar(sql, args)
    net_delivered = delivered_qty(inv["order_id"], sku) - returned_qty(inv["order_id"], sku, statuses) + \
        (scalar("SELECT quantity FROM returns WHERE id=?", (exclude_id,)) if exclude_id else 0)
    return max(0, min(line_qty - ret_inv, net_delivered))


@bp.post("/returns")
@require("returns.create")
@tx
def create_return():
    d = body()
    db = get_db()
    iid = as_int(d.get("invoice_id", 0), "Invoice")
    sku = d.get("sku")
    qty = as_int(d.get("quantity", 0), "Quantity")
    inspection = d.get("inspection", "Saleable")
    if inspection not in ("Saleable", "Damaged"):
        return err("Inspection harus Saleable atau Damaged")
    inv = one("SELECT * FROM invoices WHERE id=? AND status='Posted'", (iid,))
    line = one("SELECT * FROM invoice_lines WHERE invoice_id=? AND sku=?", (iid, sku)) if inv else None
    if not inv or not line or qty <= 0:
        return err("Invoice Posted / SKU / qty tidak valid")
    cap = return_cap(inv, sku)
    if qty > cap:
        return blocked("Blocked return", inv["number"], f"{sku}: diminta {qty}, sisa dapat diretur {cap}",
                       f"Return maksimal {cap} unit (net delivered − previously returned) untuk {sku} (SR-13).", cap=cap)
    credit_net = half_up(Fraction(line["net_amount"] * qty, line["quantity"]))
    credit_tax = tax_of(credit_net)
    so = one("SELECT * FROM sales_orders WHERE id=?", (inv["order_id"],))
    cost = scalar("SELECT standard_cost FROM products WHERE sku=?", (sku,))
    no = next_number("RET")
    cur = db.execute("INSERT INTO returns(number,invoice_id,sku,quantity,status,credit_total,credit_net,credit_tax,"
                     "cogs_reversal,inspection,reason,warehouse_code,created_by) VALUES(?,?,?,?,'Requested',?,?,?,?,?,?,?,?)",
                     (no, iid, sku, qty, credit_net + credit_tax, credit_net, credit_tax, qty * cost, inspection,
                      d.get("reason"), so["warehouse_code"], user()))
    audit(user(), "Return requested", no, f"{inv['number']} {sku} × {qty} ({inspection}); credit {idr(credit_net + credit_tax)}")
    audit(user(), "Return requested", inv["number"], no)
    return ok({"id": cur.lastrowid, "number": no, "status": "Requested", "credit_net": credit_net,
               "credit_tax": credit_tax, "credit_total": credit_net + credit_tax, "cogs_reversal": qty * cost}, 201)


@bp.post("/returns/<int:retid>/validate")
@require("returns.validate")
@tx
def validate_return(retid):
    db = get_db()
    r = one("SELECT * FROM returns WHERE id=?", (retid,))
    if not r:
        return err("Return tidak ditemukan", 404)
    if r["status"] == "Done":  # T-32 credit note ganda
        return ok({"status": "Done", "idempotent": True, "credit_note_number": r["credit_note_number"],
                   "message": "Credit Note sudah dibuat; tidak ada kredit kedua"})
    if r["status"] != "Requested":
        return err("Status return tidak valid", 409)
    inv = one("SELECT * FROM invoices WHERE id=?", (r["invoice_id"],))
    if r["quantity"] > return_cap(inv, r["sku"], ("Done",)):
        return err("Return melebihi net delivered − previously returned (SR-13)", 409)
    if db.execute("UPDATE returns SET status='Done', validated_by=?, validated_at=CURRENT_TIMESTAMP WHERE id=? AND "
                  "status='Requested'", (user(), retid)).rowcount != 1:
        return ok({"status": "Done", "idempotent": True})
    so = one("SELECT * FROM sales_orders WHERE id=?", (inv["order_id"],))
    cn = next_number("RINV")
    if r["inspection"] == "Saleable":
        add_stock(r["sku"], r["warehouse_code"], r["quantity"])
        stock_move(r["number"], "Customer Return", r["sku"], "Customer", r["warehouse_code"], r["quantity"], user())
        cogs_lines = [("1200", r["cogs_reversal"], 0, "Stock back (saleable)"), ("5100", 0, r["cogs_reversal"], "COGS reversal")]
    else:
        stock_move(r["number"], "Customer Return (damaged)", r["sku"], "Customer", "Scrap", r["quantity"], user())
        cogs_lines = [("6100", r["cogs_reversal"], 0, "Quality loss (damaged return)"), ("5100", 0, r["cogs_reversal"], "COGS reversal")]
    j1 = post_journal("Return COGS", retid, r["number"], cogs_lines, user(), f"Return {inv['number']}")
    j2 = post_journal("Credit Note", retid, cn, [("4100", r["credit_net"], 0, "Reverse revenue"),
                                                 ("2200", r["credit_tax"], 0, "Reverse VAT output"),
                                                 ("1100", 0, r["credit_total"], f"AR adjustment {so['customer_code']}")],
                      user(), f"Credit note {inv['number']}")
    credited = inv["credited_amount"] + r["credit_total"]
    db.execute("UPDATE invoices SET credited_amount=?, payment_status=? WHERE id=?",
               (credited, pay_status(inv["total"], inv["paid_amount"], credited), inv["id"]))
    db.execute("UPDATE returns SET credit_note_number=? WHERE id=?", (cn, retid))
    audit(user(), "Validate Return + Credit Note", r["number"], f"{cn}: net {idr(r['credit_net'])} + PPN {idr(r['credit_tax'])}"
          f" = {idr(r['credit_total'])}; COGS reversal {idr(r['cogs_reversal'])}; {j1}, {j2}")
    audit(user(), "Credit Note", inv["number"], f"{cn} {idr(r['credit_total'])}")
    audit(user(), "Credit Note posted", cn, r["number"])
    return ok({"status": "Done", "credit_note_number": cn, "journals": [j1, j2]})


# ---------------------------------------------------------------- Accounting (SR-15, SR-16, F-15)
@bp.get("/accounting/trial-balance")
@require("accounting.view")
def trial_balance():
    rows = rowdicts(get_db().execute(
        "SELECT a.code account_code, a.name account_name, COALESCE(SUM(jl.debit),0) debit, COALESCE(SUM(jl.credit),0) credit, "
        "COALESCE(SUM(jl.debit),0)-COALESCE(SUM(jl.credit),0) balance FROM accounts a LEFT JOIN journal_lines jl "
        "ON jl.account_code=a.code GROUP BY a.code ORDER BY a.code"))
    td = sum(x["debit"] for x in rows)
    tc = sum(x["credit"] for x in rows)
    return ok({"lines": rows, "total_debit": td, "total_credit": tc, "difference": td - tc})


@bp.get("/accounting/ledger/<code>")
@require("accounting.view")
def ledger(code):
    rows = rowdicts(get_db().execute(
        "SELECT j.id journal_id, j.number, j.entry_date, j.reference, j.source_type, jl.label, jl.debit, jl.credit "
        "FROM journal_lines jl JOIN journal_entries j ON j.id=jl.journal_id WHERE jl.account_code=? ORDER BY j.id", (code,)))
    bal = 0
    for r in rows:
        bal += r["debit"] - r["credit"]
        r["running_balance"] = bal
    return ok({"account": code, "lines": rows, "balance": bal})


@bp.get("/journals/<int:jid>")
@require("accounting.view")
def journal_detail(jid):
    return ok(doc_journal(jid))


@bp.get("/reports")
@require("reports.view")
def reports():
    db = get_db()
    return ok({"sales_by_status": rowdicts(db.execute("SELECT status,COUNT(*) count,SUM(total) amount FROM sales_orders GROUP BY status")),
               "purchase_by_status": rowdicts(db.execute("SELECT status,COUNT(*) count,SUM(total) amount FROM purchase_orders GROUP BY status")),
               "stock_by_warehouse": rowdicts(db.execute("SELECT warehouse_code,SUM(on_hand) units FROM stock GROUP BY warehouse_code"))})


# ---------------------------------------------------------------- generic document form (Form view + smart buttons + log)
def rel(kind, row, label=None):
    if not row:
        return None
    return {"kind": kind, "id": row["id"], "number": row["number"], "label": label or row["number"],
            "status": row["status"] if "status" in row.keys() else None}


def journals_for(source_type, source_id):
    return [rel("journal", j, f"Journal {j['number']}") for j in get_db().execute(
        "SELECT * FROM journal_entries WHERE source_type=? AND source_id=?", (source_type, source_id))]


def doc_sale(oid):
    db = get_db()
    so = one("SELECT so.*, c.name customer_name FROM sales_orders so JOIN customers c ON c.code=so.customer_code WHERE so.id=?", (oid,))
    if not so:
        return None
    lines = so_lines_status(so)
    related = []
    if so["lead_id"]:
        related.append(rel("lead", one("SELECT id,number,stage status FROM leads WHERE id=?", (so["lead_id"],)), "Opportunity"))
    if so["revision_of"]:
        related.append(rel("sale", one("SELECT id,number,status FROM sales_orders WHERE id=?", (so["revision_of"],)), "Revisi dari"))
    for r in db.execute("SELECT id,number,status FROM sales_orders WHERE revision_of=?", (oid,)):
        related.append(rel("sale", r, f"Revisi → {r['number']}"))
    for r in db.execute("SELECT id,number,status FROM deliveries WHERE order_id=?", (oid,)):
        related.append(rel("delivery", r, f"Delivery {r['number']}"))
        related += journals_for("Delivery", r["id"])
    for r in db.execute("SELECT id,number,status FROM invoices WHERE order_id=?", (oid,)):
        related.append(rel("invoice", r, f"Invoice {r['number']}"))
        for p in db.execute("SELECT id,number,'Posted' status FROM payments WHERE document_type='customer' AND document_id=?", (r["id"],)):
            related.append(rel("payment", p, f"Payment {p['number']}"))
        for x in db.execute("SELECT id,number,status FROM returns WHERE invoice_id=?", (r["id"],)):
            related.append(rel("return", x, f"Return {x['number']}"))
    res = [dict(x) for x in db.execute("SELECT * FROM reservations WHERE document_no=? ORDER BY id", (so["number"],))]
    ci = credit_info(so["customer_code"])
    eff = effective_discount(oid)
    status = so["status"]
    steps = ["Quotation", "Pending Approval", "Confirmed", "Locked"] if status != "Cancelled" else ["Quotation", "Cancelled"]
    return {"kind": "sale", "id": oid, "number": so["number"], "status": status,
            "title": "Quotation" if status in ("Quotation", "Pending Approval") else "Sales Order",
            "steps": steps, "current": "Locked" if status == "Confirmed" and any(l["delivered_qty"] for l in lines) else status,
            "fields": [["Customer", f"{so['customer_code']} — {so['customer_name']}"], ["Document", so["number"]],
                       ["Warehouse", so["warehouse_code"]], ["Discount", f"{so['discount_pct']:g}% (efektif {eff}%)"],
                       ["Discount approval", so["discount_approved_by"] or ("Diperlukan Owner" if eff > 10 else "Tidak perlu")],
                       ["Created by", so["created_by"]], ["Created at", so["created_at"]], ["Confirmed at", so["confirmed_at"]],
                       ["Credit exposure", ci["exposure"]], ["Credit limit", ci["credit_limit"]],
                       ["Price lock", "Terkunci (SR-08)" if status not in ("Quotation", "Pending Approval") else "Dapat diedit"]],
            "lines": {"columns": ["sku", "product_name", "ordered_qty", "unit_price", "discount_pct", "subtotal", "tax", "total",
                                  "delivered_qty", "backorder_qty", "invoiced_qty", "returned_qty"], "rows": lines},
            "totals": [["Untaxed (Net)", so["subtotal"]], ["PPN 11%", so["tax"]], ["Grand Total", so["total"]]],
            "related": [r for r in related if r], "reservations": res, "raw": dict(so), "customer_code": so["customer_code"]}


def doc_delivery(did):
    db = get_db()
    d = one("SELECT d.*, so.number sales_order, so.id so_id, so.customer_code, c.name customer_name FROM deliveries d "
            "JOIN sales_orders so ON so.id=d.order_id JOIN customers c ON c.code=so.customer_code WHERE d.id=?", (did,))
    if not d:
        return None
    lines = []
    for l in db.execute("SELECT dl.*, p.name, p.size, p.unit, p.standard_cost, sol.quantity ordered FROM delivery_lines dl "
                        "JOIN products p ON p.sku=dl.sku JOIN sales_order_lines sol ON sol.order_id=? AND sol.sku=dl.sku "
                        "WHERE dl.delivery_id=?", (d["order_id"], did)):
        tot = delivered_qty(d["order_id"], l["sku"])
        lines.append({"sku": l["sku"], "product_name": f"{l['name']} {l['size'] or ''}".strip(), "unit": l["unit"],
                      "ordered_qty": l["ordered"], "delivery_qty": l["quantity"], "delivered_total": tot,
                      "backorder_qty": max(0, l["ordered"] - tot), "standard_cost": l["standard_cost"],
                      "cogs_amount": l["quantity"] * l["standard_cost"]})
    j = one("SELECT * FROM journal_entries WHERE source_type='Delivery' AND source_id=?", (did,))
    cogs = sum(x["cogs_amount"] for x in lines)
    backorder = sum(x["backorder_qty"] for x in lines)
    return {"kind": "delivery", "id": did, "number": d["number"], "status": d["status"], "title": "Delivery Order",
            "steps": ["Ready", "Partial", "Done"],
            "current": d["status"] if d["status"] != "Done" else ("Partial" if backorder else "Done"),
            "fields": [["Order", d["sales_order"]], ["Customer", f"{d['customer_code']} — {d['customer_name']}"],
                       ["Warehouse", d["warehouse_code"]], ["Created at", d["created_at"]],
                       ["Validated at", d["validated_at"]], ["Validated by", d["validated_by"]],
                       ["Backorder", f"{backorder} unit"], ["COGS policy", "Standard cost"]],
            "lines": {"columns": ["sku", "product_name", "ordered_qty", "delivery_qty", "delivered_total", "backorder_qty",
                                  "standard_cost", "cogs_amount"], "rows": lines},
            "totals": [["Total unit", sum(x["delivery_qty"] for x in lines)], ["COGS delivered", cogs]],
            "related": [rel("sale", {"id": d["so_id"], "number": d["sales_order"], "status": None}, f"SO {d['sales_order']}")]
                       + ([rel("journal", j, f"Journal {j['number']}")] if j else []),
            # kompatibilitas UI lama
            "sales_order": d["sales_order"], "customer_code": d["customer_code"], "customer_name": d["customer_name"],
            "warehouse_code": d["warehouse_code"], "created_at": d["created_at"], "validated_at": d["validated_at"],
            "total_cogs": cogs, "total_quantity": sum(x["delivery_qty"] for x in lines),
            "journal_entry": dict(j) if j else None}


def doc_invoice(iid):
    db = get_db()
    i = one("SELECT i.*, so.number sales_order, so.id so_id, so.customer_code, c.name customer_name FROM invoices i "
            "JOIN sales_orders so ON so.id=i.order_id JOIN customers c ON c.code=so.customer_code WHERE i.id=?", (iid,))
    if not i:
        return None
    lines = rowdicts(db.execute("SELECT il.sku, p.name product_name, il.quantity, il.unit_price, il.discount_amount, "
                                "il.net_amount, il.tax_amount, il.net_amount+il.tax_amount total FROM invoice_lines il "
                                "JOIN products p ON p.sku=il.sku WHERE il.invoice_id=?", (iid,)))
    related = [rel("sale", {"id": i["so_id"], "number": i["sales_order"], "status": None}, f"SO {i['sales_order']}")]
    for r in db.execute("SELECT id,number,status FROM deliveries WHERE order_id=? AND status='Done'", (i["so_id"],)):
        related.append(rel("delivery", r, f"Source delivery {r['number']}"))
    related += journals_for("Customer Invoice", iid)
    for p in db.execute("SELECT id,number,'Posted' status FROM payments WHERE document_type='customer' AND document_id=?", (iid,)):
        related.append(rel("payment", p, f"Payment {p['number']}"))
    for x in db.execute("SELECT id,number,status FROM returns WHERE invoice_id=?", (iid,)):
        related.append(rel("return", x, f"Return {x['number']}"))
    residual = i["total"] - i["paid_amount"] - i["credited_amount"]
    cur = "Paid" if i["status"] == "Posted" and i["payment_status"] == "Paid" else i["status"]
    return {"kind": "invoice", "id": iid, "number": i["number"], "status": i["status"], "title": "Customer Invoice",
            "steps": ["Draft", "Posted", "Paid"], "current": cur,
            "fields": [["Customer", f"{i['customer_code']} — {i['customer_name']}"], ["Sales Order", i["sales_order"]],
                       ["Source delivery", ", ".join(f"{l['quantity']} × {l['sku']}" for l in lines)],
                       ["Tax", "PPN efektif 11% (12% × 11/12), half-up per line"], ["Invoice number", i["number"]],
                       ["Payment status", i["payment_status"]], ["Posted at", i["posted_at"]], ["Posted by", i["posted_by"]]],
            "lines": {"columns": ["sku", "product_name", "quantity", "unit_price", "discount_amount", "net_amount", "tax_amount",
                                  "total"], "rows": lines},
            "totals": [["Untaxed Amount", i["net_amount"]], ["Tax", i["tax_amount"]], ["Total", i["total"]],
                       ["Paid", i["paid_amount"]], ["Credited", i["credited_amount"]], ["Amount Due", residual]],
            "related": related, "residual": residual, "raw": dict(i)}


def doc_purchase(oid):
    db = get_db()
    po = one("SELECT po.*, v.name vendor_name FROM purchase_orders po JOIN vendors v ON v.code=po.vendor_code WHERE po.id=?", (oid,))
    if not po:
        return None
    lines = []
    for l in db.execute("SELECT pol.*, p.name FROM purchase_order_lines pol JOIN products p ON p.sku=pol.sku WHERE order_id=?", (oid,)):
        rec = scalar("SELECT COALESCE(SUM(rl.received_qty),0) FROM receipt_lines rl JOIN receipts r ON r.id=rl.receipt_id "
                     "WHERE r.purchase_order_id=? AND rl.sku=? AND r.status='Done'", (oid, l["sku"]))
        acc = scalar("SELECT COALESCE(SUM(rl.accepted_qty),0) FROM receipt_lines rl JOIN receipts r ON r.id=rl.receipt_id "
                     "WHERE r.purchase_order_id=? AND rl.sku=? AND r.status='Done'", (oid, l["sku"]))
        billed = scalar("SELECT COALESCE(SUM(bl.billed_qty),0) FROM vendor_bill_lines bl JOIN vendor_bills b ON b.id=bl.bill_id "
                        "WHERE b.purchase_order_id=? AND bl.sku=? AND b.status='Posted'", (oid, l["sku"]))
        lines.append({"sku": l["sku"], "product_name": l["name"], "quantity": l["quantity"], "unit_cost": l["unit_cost"],
                      "subtotal": l["subtotal"], "tax": l["tax"], "received_qty": rec, "accepted_qty": acc, "billed_qty": billed})
    related = []
    for r in db.execute("SELECT id,number,status FROM receipts WHERE purchase_order_id=?", (oid,)):
        related.append(rel("receipt", r, f"Receipt {r['number']}"))
    for b in db.execute("SELECT id,number,status FROM vendor_bills WHERE purchase_order_id=?", (oid,)):
        related.append(rel("bill", b, f"Bill {b['number']}"))
    status = po["status"]
    return {"kind": "purchase", "id": oid, "number": po["number"], "status": status,
            "title": "RFQ" if status in ("RFQ", "Pending Approval") else "Purchase Order",
            "steps": ["RFQ", "Pending Approval", "Purchase Order"],
            "current": "Purchase Order" if status == "Confirmed" else status,
            "fields": [["Vendor", f"{po['vendor_code']} — {po['vendor_name']}"], ["Currency", "IDR"],
                       ["Approval threshold", f"Subtotal > {idr(setting('large_po_threshold'))}"],
                       ["Approved by", po["approved_by"]], ["Approved at", po["approved_at"]],
                       ["Created by", po["created_by"]], ["Confirmed at", po["confirmed_at"]]],
            "lines": {"columns": ["sku", "product_name", "quantity", "unit_cost", "subtotal", "tax", "received_qty",
                                  "accepted_qty", "billed_qty"], "rows": lines},
            "totals": [["Untaxed Amount", po["subtotal"]], ["PPN simulasi", po["tax"]], ["Gross Amount", po["total"]]],
            "related": related, "raw": dict(po)}


def doc_receipt(rid):
    db = get_db()
    r = one("SELECT r.*, po.number purchase_order, po.id po_id, po.vendor_code FROM receipts r JOIN purchase_orders po "
            "ON po.id=r.purchase_order_id WHERE r.id=?", (rid,))
    if not r:
        return None
    lines = rowdicts(db.execute(
        "SELECT rl.sku, p.name product_name, pol.quantity ordered_qty, rl.received_qty inspected, rl.accepted_qty accepted, "
        "rl.rejected_qty rejected, rl.quality_reason fail_reason, pol.unit_cost, rl.accepted_qty*p.standard_cost accepted_value, "
        "rl.rejected_qty*p.standard_cost rejected_value FROM receipt_lines rl JOIN products p ON p.sku=rl.sku "
        "JOIN purchase_order_lines pol ON pol.order_id=? AND pol.sku=rl.sku WHERE rl.receipt_id=?", (r["po_id"], rid)))
    related = [rel("purchase", {"id": r["po_id"], "number": r["purchase_order"], "status": None}, f"PO {r['purchase_order']}")]
    related += journals_for("Receipt", rid)
    for b in db.execute("SELECT id,number,status FROM vendor_bills WHERE receipt_id=?", (rid,)):
        related.append(rel("bill", b, f"Bill {b['number']}"))
    hist_refs = [r["number"]] + ([r["qc_number"]] if r["qc_number"] else [])
    return {"kind": "receipt", "id": rid, "number": r["number"], "status": r["status"], "title": "Receipt & Accepted Qty",
            "steps": ["Waiting", "Quality Hold", "Done"],
            "current": {"Waiting": "Waiting", "Quality Checked": "Quality Hold", "Done": "Done"}.get(r["status"], r["status"]),
            "fields": [["Purchase Order", r["purchase_order"]], ["Vendor", r["vendor_code"]], ["Warehouse", r["warehouse_code"]],
                       ["Quality Check", r["qc_number"] or "Belum (wajib sebelum validasi)"], ["Inspector", r["qc_by"]],
                       ["QC at", r["qc_at"]], ["Validated by", r["validated_by"]], ["Validated at", r["validated_at"]]],
            "lines": {"columns": ["sku", "product_name", "ordered_qty", "inspected", "accepted", "rejected", "fail_reason",
                                  "accepted_value", "rejected_value"], "rows": lines},
            "totals": [["Inspected", sum(l["inspected"] for l in lines)], ["Accepted", sum(l["accepted"] for l in lines)],
                       ["Rejected", sum(l["rejected"] for l in lines)],
                       ["Accepted value (masuk stock)", sum(l["accepted_value"] for l in lines)],
                       ["Rejected value potensial (tidak masuk stock)", sum(l["rejected_value"] for l in lines)]],
            "related": related, "history_refs": hist_refs, "raw": dict(r)}


def doc_bill(bid):
    db = get_db()
    b = one("SELECT b.*, po.number purchase_order, po.vendor_code, r.number receipt FROM vendor_bills b JOIN purchase_orders po "
            "ON po.id=b.purchase_order_id JOIN receipts r ON r.id=b.receipt_id WHERE b.id=?", (bid,))
    if not b:
        return None
    lines = rowdicts(db.execute("SELECT sku, ordered_qty, accepted_qty, claimed_qty, billed_qty, claimed_qty-billed_qty "
                                "disputed_qty, unit_cost, net_amount, tax_amount FROM vendor_bill_lines WHERE bill_id=?", (bid,)))
    related = [rel("purchase", {"id": b["purchase_order_id"], "number": b["purchase_order"], "status": None}, f"PO {b['purchase_order']}"),
               rel("receipt", {"id": b["receipt_id"], "number": b["receipt"], "status": None}, f"Receipt {b['receipt']}")]
    related += journals_for("Vendor Bill", bid)
    for p in db.execute("SELECT id,number,'Posted' status FROM payments WHERE document_type='vendor' AND document_id=?", (bid,)):
        related.append(rel("payment", p, f"Payment {p['number']}"))
    acc = sum(l["accepted_qty"] for l in lines)
    ordered = sum(l["ordered_qty"] for l in lines)
    return {"kind": "bill", "id": bid, "number": b["number"], "status": b["status"], "title": "Vendor Bill & Three-way Match",
            "steps": ["Draft", "Posted", "Paid"], "current": "Paid" if b["payment_status"] == "Paid" else b["status"],
            "fields": [["Vendor", b["vendor_code"]], ["PO Reference", b["purchase_order"]], ["Receipt", b["receipt"]],
                       ["Receipt Accepted", f"{acc} / {ordered}"], ["Tax", "PPN efektif 11%"],
                       ["Three-way match", b["match_status"]], ["Selisih", b["match_note"] or "—"],
                       ["Payment status", b["payment_status"]]],
            "lines": {"columns": ["sku", "ordered_qty", "accepted_qty", "claimed_qty", "billed_qty", "disputed_qty", "unit_cost",
                                  "net_amount", "tax_amount"], "rows": lines},
            "totals": [["Untaxed", b["net_amount"]], ["Tax", b["tax_amount"]], ["Total", b["total"]],
                       ["Paid", b["paid_amount"]], ["Residual", b["total"] - b["paid_amount"]]],
            "related": related, "residual": b["total"] - b["paid_amount"], "raw": dict(b)}


def doc_payment(pid):
    p = one("SELECT * FROM payments WHERE id=?", (pid,))
    if not p:
        return None
    kind = "invoice" if p["document_type"] == "customer" else "bill"
    doc = one(f"SELECT id,number,status FROM {'invoices' if kind == 'invoice' else 'vendor_bills'} WHERE id=?", (p["document_id"],))
    return {"kind": "payment", "id": pid, "number": p["number"], "status": "Posted", "title": "Payment",
            "steps": ["Draft", "Posted"], "current": "Posted",
            "fields": [["Type", "Customer payment (Bank)" if kind == "invoice" else "Vendor payment (Bank)"],
                       ["Document", doc["number"]], ["Amount", p["amount"]], ["Journal", p["journal_number"]],
                       ["Created by", p["created_by"]], ["Date", p["payment_date"]]],
            "lines": None, "totals": [["Amount", p["amount"]]],
            "related": [rel(kind, doc)] + journals_for("Payment", pid), "raw": dict(p)}


def doc_transfer(tid):
    t = one("SELECT * FROM transfers WHERE id=?", (tid,))
    if not t:
        return None
    moves = rowdicts(get_db().execute("SELECT * FROM stock_moves WHERE reference=?", (t["number"],)))
    return {"kind": "transfer", "id": tid, "number": t["number"], "status": t["status"], "title": "Internal Stock Transfer",
            "steps": ["Draft", "Ready", "Done"], "current": t["status"],
            "fields": [["SKU", t["sku"]], ["From", t["from_warehouse"]], ["To", t["to_warehouse"]],
                       ["Quantity", t["quantity"]], ["Created by", t["created_by"]], ["Validated by", t["validated_by"]],
                       ["Validated at", t["validated_at"]],
                       [f"{t['from_warehouse']} on hand", on_hand(t["sku"], t["from_warehouse"])],
                       [f"{t['to_warehouse']} on hand", on_hand(t["sku"], t["to_warehouse"])],
                       ["Company total", scalar("SELECT SUM(on_hand) FROM stock WHERE sku=?", (t["sku"],))]],
            "lines": {"columns": ["move_type", "sku", "from_location", "to_location", "quantity", "value", "created_at"],
                      "rows": moves},
            "totals": [["Stock moves", len(moves)]], "related": [], "raw": dict(t)}


def doc_return(retid):
    r = one("SELECT r.*, i.number invoice, i.order_id FROM returns r JOIN invoices i ON i.id=r.invoice_id WHERE r.id=?", (retid,))
    if not r:
        return None
    so = one("SELECT id,number,status FROM sales_orders WHERE id=?", (r["order_id"],))
    related = [rel("invoice", {"id": r["invoice_id"], "number": r["invoice"], "status": None}, f"Invoice {r['invoice']}"),
               rel("sale", so, f"SO {so['number']}")]
    related += journals_for("Return COGS", retid) + journals_for("Credit Note", retid)
    for d in get_db().execute("SELECT id,number,status FROM deliveries WHERE order_id=? AND status='Done'", (r["order_id"],)):
        related.append(rel("delivery", d, f"Original delivery {d['number']}"))
    return {"kind": "return", "id": retid, "number": r["number"], "status": r["status"], "title": "Return & Credit Note",
            "steps": ["Requested", "Validated", "Credited"], "current": "Credited" if r["status"] == "Done" else r["status"],
            "fields": [["Original invoice", r["invoice"]], ["SKU", r["sku"]], ["Return quantity", r["quantity"]],
                       ["Inspection", r["inspection"]], ["Reason", r["reason"]], ["Credit Note", r["credit_note_number"]],
                       ["Warehouse", r["warehouse_code"]], ["Validated by", r["validated_by"]]],
            "lines": None,
            "totals": [["Untaxed Credit", r["credit_net"]], ["Tax Credit", r["credit_tax"]],
                       ["Refund / AR Adjustment", r["credit_total"]], ["COGS reversal", r["cogs_reversal"]]],
            "related": related, "history_refs": [r["number"]] + ([r["credit_note_number"]] if r["credit_note_number"] else []),
            "raw": dict(r)}


SOURCE_KIND = {"Delivery": ("delivery", "deliveries"), "Customer Invoice": ("invoice", "invoices"),
               "Receipt": ("receipt", "receipts"), "Vendor Bill": ("bill", "vendor_bills"),
               "Payment": ("payment", "payments"), "Return COGS": ("return", "returns"), "Credit Note": ("return", "returns")}


def doc_journal(jid):
    j = one("SELECT * FROM journal_entries WHERE id=?", (jid,))
    if not j:
        return None
    lines = rowdicts(get_db().execute("SELECT jl.account_code, a.name account_name, jl.label, jl.debit, jl.credit FROM journal_lines jl "
                                      "LEFT JOIN accounts a ON a.code=jl.account_code WHERE journal_id=?", (jid,)))
    td = sum(l["debit"] for l in lines)
    tc = sum(l["credit"] for l in lines)
    related = []
    if j["source_type"] in SOURCE_KIND:
        kind, table = SOURCE_KIND[j["source_type"]]
        src = one(f"SELECT id, number, {'status' if table != 'payments' else chr(39) + 'Posted' + chr(39)} status FROM {table} WHERE id=?",
                  (j["source_id"],))
        related.append(rel(kind, src, f"Source {src['number']}" if src else None))
    return {"kind": "journal", "id": jid, "number": j["number"], "status": j["status"], "title": "Journal Entry",
            "steps": ["Draft", "Posted"], "current": j["status"],
            "fields": [["Reference", j["reference"]], ["Date", j["entry_date"]], ["Move type", j["source_type"]],
                       ["Posting policy", "Standard cost"], ["Memo", j["memo"]], ["Created by", j["created_by"]]],
            "lines": {"columns": ["account_code", "account_name", "label", "debit", "credit"], "rows": lines},
            "totals": [["Debit", td], ["Credit", tc], ["Balance Difference", td - tc]],
            "related": [r for r in related if r], "history_refs": [j["reference"], j["number"]]}


def doc_lead(lid):
    l = one("SELECT l.*, c.name customer_name FROM leads l LEFT JOIN customers c ON c.code=l.customer_code WHERE l.id=?", (lid,))
    if not l:
        return None
    hist = rowdicts(get_db().execute("SELECT from_stage, to_stage, actor, created_at FROM lead_stage_history WHERE lead_id=? "
                                     "ORDER BY id", (lid,)))
    related = []
    if l["quotation_id"]:
        related.append(rel("sale", one("SELECT id,number,status FROM sales_orders WHERE id=?", (l["quotation_id"],)), "Quotation"))
    return {"kind": "lead", "id": lid, "number": l["number"], "status": l["stage"], "title": "Opportunity",
            "steps": ["New", "Qualified", "Proposition", "Won"], "current": l["stage"],
            "fields": [["Opportunity", l["number"]], ["Customer", f"{l['customer_code']} — {l['customer_name']}"],
                       ["Title", l["title"]], ["Expected SKU", l["expected_sku"]], ["Expected qty", l["expected_qty"]],
                       ["Stage", l["stage"]], ["Revenue", "Rp0 (revenue hanya dari invoice posted — SR-18)"]],
            "lines": {"columns": ["from_stage", "to_stage", "actor", "created_at"], "rows": hist},
            "totals": [], "related": related, "raw": dict(l)}


DOC_BUILDERS = {"sale": ("sales.view", doc_sale), "delivery": ("delivery.view", doc_delivery),
                "invoice": ("invoicing.view", doc_invoice), "purchase": ("purchase.view", doc_purchase),
                "receipt": ("receipt.view", doc_receipt), "bill": ("bills.view", doc_bill),
                "payment": ("payments.view", doc_payment), "transfer": ("transfer.view", doc_transfer),
                "return": ("returns.view", doc_return), "journal": ("accounting.view", doc_journal),
                "lead": ("crm.view", doc_lead)}


@bp.get("/doc/<kind>/<int:did>")
def document_form(kind, did):
    if not session.get("user_id"):
        return err("Authentication required", 401)
    if kind not in DOC_BUILDERS:
        return err("Unknown document", 404)
    perm, fn = DOC_BUILDERS[kind]
    if not allowed(perm):
        return deny(perm)
    doc = fn(did)
    if not doc:
        return err("Dokumen tidak ditemukan", 404)
    refs = doc.get("history_refs") or [doc["number"]]
    q = ",".join("?" * len(refs))
    doc["history"] = rowdicts(get_db().execute(f"SELECT * FROM audit_logs WHERE document_ref IN ({q}) ORDER BY id", refs))
    # smart link yang tidak boleh diakses role ini ditandai (UI menampilkan terkunci)
    for r in doc.get("related", []):
        r["allowed"] = allowed(DOC_BUILDERS[r["kind"]][0])
    return ok(doc)


# ---------------------------------------------------------------- seed reset
@bp.post("/seed/reset")
@require("*")
def reset_seed():
    db = get_db()
    db.execute("PRAGMA foreign_keys = OFF")
    for t in RESET_TABLES:
        db.execute(f"DELETE FROM {t}")
    db.execute("DELETE FROM sqlite_sequence")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    seed_db()
    actor = session.get("user", {}).get("username", "owner")
    audit(actor, "Seed reset", None, "Baseline dikembalikan")
    db.commit()
    return ok({"message": "Baseline reset berhasil"})


# ================================================================ analisis tambahan tim (dipertahankan)
@bp.get("/products/analysis")
@require("products.view")
def product_analysis():
    db = get_db()

    # Minimum stok bersifat opsional untuk percobaan
    raw_minimum = request.args.get("minimum")
    minimum = None

    if raw_minimum is not None:
        try:
            minimum = int(raw_minimum)
            if minimum < 0:
                raise ValueError()
        except ValueError:
            return jsonify({
                "error": "Minimum stok harus angka 0 atau lebih"
            }), 400

    # Ambil data produk dan stok dari database
    rows = db.execute("""
        SELECT
            p.sku,
            p.name,
            p.size,
            p.unit,
            p.sales_price,
            p.standard_cost,
            s.warehouse_code,
            s.on_hand,
            rp.min_available,
            COALESCE((
                SELECT SUM(r.quantity)
                FROM reservations r
                WHERE r.sku = s.sku
                  AND r.warehouse_code = s.warehouse_code
                  AND r.active = 1
            ), 0) AS reserved
        FROM products p
        JOIN stock s ON s.sku = p.sku
        LEFT JOIN reorder_points rp
        ON rp.sku = s.sku
        AND rp.warehouse_code = s.warehouse_code
        WHERE p.active = 1
        ORDER BY p.sku, s.warehouse_code
    """).fetchall()

    results = []

    for row in rows:
        product = dict(row)

        # Hitung stok yang tersedia
        product["available"] = (
            product["on_hand"] - product["reserved"]
        )

        # Hitung nilai persediaan
        product["stock_value"] = (
            product["on_hand"] * product["standard_cost"]
        )

        # Hitung potensi margin kotor per unit
        product["margin_per_unit"] = (
            product["sales_price"] - product["standard_cost"]
        )

        # Tentukan status stok
        if product["min_available"] is None:
            product["stock_status"] = "Belum diatur"
        elif product["available"] <= product["min_available"]:
            product["stock_status"] = "Stok menipis"
        else:
            product["stock_status"] = "Normal"

        results.append(product)

    return ok(results)


@bp.post("/products/reorder-point")
@require("products.configure")
def save_reorder_point():
    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({
            "error": "Format data tidak valid"
        }), 400

    sku = str(data.get("sku") or "").strip()
    warehouse = str(data.get("warehouse_code") or "").strip()
    minimum = data.get("min_available")

    # Validasi input
    if not sku or not warehouse:
        return jsonify({
            "error": "SKU dan gudang wajib diisi"
        }), 400

    if type(minimum) is not int or minimum < 0:
        return jsonify({
            "error": "Minimum stok harus bilangan bulat >= 0"
        }), 400

    db = get_db()

    # Pastikan produk dan gudang tersedia
    stock = db.execute("""
        SELECT 1 FROM stock
        WHERE sku = ? AND warehouse_code = ?
    """, (sku, warehouse)).fetchone()

    if not stock:
        return jsonify({
            "error": "Produk atau gudang tidak ditemukan"
        }), 404

    # Simpan atau perbarui minimum stok
    db.execute("""
        INSERT INTO reorder_points
            (sku, warehouse_code, min_available)
        VALUES (?, ?, ?)
        ON CONFLICT(sku, warehouse_code)
        DO UPDATE SET
            min_available = excluded.min_available
    """, (sku, warehouse, minimum))

    # Simpan riwayat perubahan
    audit(
        user(),
        "Set Minimum Stock",
        f"{sku}/{warehouse}",
        f"Minimum available: {minimum}"
    )

    db.commit()

    return ok({
        "sku": sku,
        "warehouse_code": warehouse,
        "min_available": minimum
    })


@bp.get("/products/performance")
@require("products.view")
def products_performance():
    db = get_db()

    # Periode analisis: default 30 hari
    try:
        days = int(request.args.get("days", "30"))
    except (TypeError, ValueError):
        return jsonify({
            "error": "Periode harus berupa angka"
        }), 400

    if days < 1 or days > 365:
        return jsonify({
            "error": "Periode harus 1 sampai 365 hari"
        }), 400

    # 1. Ambil seluruh produk aktif
    products = db.execute("""
        SELECT
            sku,
            name,
            size,
            unit,
            sales_price,
            standard_cost
        FROM products
        WHERE active = 1
        ORDER BY sku
    """).fetchall()

    # 2. Ambil stok dan minimum stok per gudang
    stock_rows = db.execute("""
        SELECT
            s.sku,
            s.warehouse_code,
            s.on_hand,
            rp.min_available,
            COALESCE((
                SELECT SUM(r.quantity)
                FROM reservations r
                WHERE r.sku = s.sku
                  AND r.warehouse_code = s.warehouse_code
                  AND r.active = 1
            ), 0) AS reserved
        FROM stock s
        LEFT JOIN reorder_points rp
            ON rp.sku = s.sku
           AND rp.warehouse_code = s.warehouse_code
    """).fetchall()

    stock_by_sku = {}

    for row in stock_rows:
        stock_by_sku.setdefault(
            row["sku"], []
        ).append(dict(row))

    # 3. Hitung produk terkirim pada periode
    # Hanya Delivery berstatus Done
    delivery_rows = db.execute("""
        SELECT
            dl.sku,
            SUM(dl.quantity) AS total_sold,
            COUNT(DISTINCT d.id) AS delivery_count
        FROM delivery_lines dl
        JOIN deliveries d
            ON d.id = dl.delivery_id
        WHERE d.status = 'Done'
          AND d.validated_at IS NOT NULL
          AND datetime(d.validated_at) >= datetime('now', ?)
        GROUP BY dl.sku
    """, (f"-{days} days",)).fetchall()

    sales_by_sku = {
        row["sku"]: dict(row)
        for row in delivery_rows
    }

    total_delivered = sum(
        row["total_sold"] for row in delivery_rows
    )

    # 4. Hitung pendapatan dari Invoice Posted
    # SO Confirmed belum dianggap revenue
    invoice_rows = db.execute("""
        SELECT
            il.sku,
            SUM(il.quantity) AS invoiced_qty,
            SUM(il.net_amount) AS net_sales
        FROM invoice_lines il
        JOIN invoices i
            ON i.id = il.invoice_id
        WHERE i.status = 'Posted'
        GROUP BY il.sku
    """).fetchall()

    invoice_by_sku = {
        row["sku"]: dict(row)
        for row in invoice_rows
    }

    # 5. Retur dicatat terpisah untuk evaluasi
    return_rows = db.execute("""
        SELECT
            sku,
            SUM(quantity) AS returned_qty
        FROM returns
        WHERE status = 'Done'
        GROUP BY sku
    """).fetchall()

    returns_by_sku = {
        row["sku"]: row["returned_qty"]
        for row in return_rows
    }

    results = []

    for p in products:
        sku = p["sku"]
        locations = stock_by_sku.get(sku, [])

        # Jumlah stok seluruh lokasi
        on_hand = sum(
            r["on_hand"] for r in locations
        )

        reserved = sum(
            r["reserved"] for r in locations
        )

        available = on_hand - reserved

        # Penjualan berdasarkan Delivery Done
        sales = sales_by_sku.get(sku, {})

        total_sold = sales.get("total_sold", 0)
        delivery_count = sales.get("delivery_count", 0)

        # Nilai inventory
        stock_value = (
            on_hand * p["standard_cost"]
        )

        # Margin potensial per unit
        margin_per_unit = (
            p["sales_price"] - p["standard_cost"]
        )

        # Keuntungan berdasarkan Posted Invoice
        invoice = invoice_by_sku.get(sku)

        if invoice is None:
            net_sales = None
            estimated_cogs = None
            gross_profit = None
            profit_margin = None
        else:
            net_sales = invoice["net_sales"]

            estimated_cogs = (
                invoice["invoiced_qty"]
                * p["standard_cost"]
            )

            gross_profit = (
                net_sales - estimated_cogs
            )

            profit_margin = (
                round(gross_profit / net_sales * 100, 2)
                if net_sales > 0 else None
            )

        # Cek lokasi dengan stok menipis
        low_stock_locations = []
        configured_locations = 0

        for r in locations:
            minimum = r["min_available"]

            if minimum is None:
                continue

            configured_locations += 1

            location_available = (
                r["on_hand"] - r["reserved"]
            )

            if location_available <= minimum:
                low_stock_locations.append(
                    r["warehouse_code"]
                )

        # Klasifikasi pergerakan produk
        if total_delivered == 0:
            movement_status = "Data belum cukup"
        elif total_sold == 0:
            movement_status = "0 terkirim - perlu evaluasi"
        else:
            movement_status = "Ada pengiriman"

        # Rekomendasi untuk Finance
        if configured_locations == 0:
            recommendation = (
                "Minimum stok belum diatur"
            )

        elif low_stock_locations and total_sold > 0:
            recommendation = (
                "Pertimbangkan restock"
            )

        elif low_stock_locations:
            recommendation = (
                "Stok menipis - evaluasi kebutuhan"
            )

        else:
            recommendation = (
                "Stok cukup pada lokasi terkonfigurasi"
            )

        results.append({
            "sku": sku,
            "product": (
                f'{p["name"]} {p["size"] or ""}'
            ).strip(),

            "on_hand": on_hand,
            "reserved": reserved,
            "available": available,
            "stock_value": stock_value,

            "total_sold_period": total_sold,
            "delivery_count": delivery_count,
            "movement_status": movement_status,

            "margin_per_unit": margin_per_unit,

            "posted_net_sales": net_sales,
            "estimated_cogs": estimated_cogs,
            "estimated_gross_profit": gross_profit,
            "estimated_profit_margin_pct": profit_margin,

            "returned_qty": returns_by_sku.get(sku, 0),

            "low_stock_locations": (
                ", ".join(low_stock_locations)
                if low_stock_locations else "—"
            ),

            "restock_recommendation": recommendation
        })

    # 6. Ranking produk paling banyak terkirim
    best_sellers = sorted(
        [
            x for x in results
            if x["total_sold_period"] > 0
        ],
        key=lambda x: (
            -x["total_sold_period"], x["sku"]
        )
    )[:5]

    # 7. Produk dengan pengiriman paling rendah
    # Tidak otomatis berarti produk tidak laku
    if total_delivered > 0:
        low_movement = sorted(
            results,
            key=lambda x: (
                x["total_sold_period"], x["sku"]
            )
        )[:5]
    else:
        low_movement = []

    # 8. Produk yang perlu ditinjau untuk restock
    restock_candidates = [
        x for x in results
        if x["restock_recommendation"]
        == "Pertimbangkan restock"
    ]

    return ok({
        "period_days": days,
        "has_delivery_history": total_delivered > 0,
        "total_delivered_units": total_delivered,
        "best_sellers": best_sellers,
        "low_movement_products": low_movement,
        "restock_candidates": restock_candidates,
        "products": results,
        "note": (
            "Produk terlaris dihitung dari Delivery Done. "
            "Keuntungan adalah estimasi berdasarkan "
            "Invoice Posted dan standard cost saat ini. "
            "Retur ditampilkan terpisah (Credit Note "
            "sudah membalik revenue di ledger)."
        )
    })


@bp.get("/contacts/profitability")
@require("contacts.view")
def contacts_profitability():
    db = get_db()

    # Ambil semua customer aktif
    customers = db.execute("""
        SELECT code, name, opening_ar, credit_limit
        FROM customers
        WHERE active = 1
        ORDER BY code
    """).fetchall()

    # Penjualan hanya dari Invoice Posted
    # Dipisah dari retur supaya tidak double counting
    invoice_rows = db.execute("""
        SELECT
            so.customer_code AS customer_code,
            SUM(il.net_amount) AS invoiced_net_sales,
            SUM(
                il.quantity * p.standard_cost
            ) AS estimated_cogs
        FROM invoices i
        JOIN sales_orders so
            ON so.id = i.order_id
        JOIN invoice_lines il
            ON il.invoice_id = i.id
        JOIN products p
            ON p.sku = il.sku
        WHERE i.status = 'Posted'
        GROUP BY so.customer_code
    """).fetchall()

    invoice_by_customer = {
        r["customer_code"]: dict(r)
        for r in invoice_rows
    }

    # Piutang dari Invoice Posted yang belum lunas
    ar_rows = db.execute("""
        SELECT
            so.customer_code AS customer_code,
            COUNT(*) AS posted_invoice_count,
            SUM(
                i.total - i.paid_amount - i.credited_amount
            ) AS unpaid_invoice_ar
        FROM invoices i
        JOIN sales_orders so
            ON so.id = i.order_id
        WHERE i.status = 'Posted'
        GROUP BY so.customer_code
    """).fetchall()

    ar_by_customer = {
        r["customer_code"]: dict(r)
        for r in ar_rows
    }

    # Retur yang sudah Done
    return_rows = db.execute("""
        SELECT
            so.customer_code AS customer_code,
            r.quantity,
            r.credit_total,
            p.standard_cost
        FROM returns r
        JOIN invoices i
            ON i.id = r.invoice_id
        JOIN sales_orders so
            ON so.id = i.order_id
        JOIN products p
            ON p.sku = r.sku
        WHERE r.status = 'Done'
          AND i.status = 'Posted'
    """).fetchall()

    tax_setting = db.execute("""
        SELECT value
        FROM settings
        WHERE key = 'tax_effective_rate'
    """).fetchone()

    tax_rate = (
        float(tax_setting["value"])
        if tax_setting else 0.11
    )

    returns_by_customer = {}

    for r in return_rows:
        ret = returns_by_customer.setdefault(
            r["customer_code"],
            {
                "returned_units": 0,
                "returned_net_est": 0,
                "returned_cost_est": 0,
                "credit_note_gross": 0
            }
        )

        ret["returned_units"] += r["quantity"]

        # credit_total sudah termasuk pajak
        ret["returned_net_est"] += money(
            r["credit_total"] / (1 + tax_rate)
        )

        ret["returned_cost_est"] += (
            r["quantity"] * r["standard_cost"]
        )

        ret["credit_note_gross"] += r["credit_total"]

    results = []

    for c in customers:
        code = c["code"]

        invoice = invoice_by_customer.get(code)
        ar = ar_by_customer.get(code, {})
        ret = returns_by_customer.get(code, {})

        opening_ar = c["opening_ar"]

        unpaid_invoice_ar = ar.get(
            "unpaid_invoice_ar", 0
        )

        outstanding_ar = (
            opening_ar + unpaid_invoice_ar
        )

        # Jangan mengarang keuntungan jika
        # belum ada Invoice Posted
        if invoice is None:
            net_sales = None
            estimated_cogs = None
            gross_profit = None
            profit_margin_pct = None

            profit_status = (
                "Belum ada Invoice Posted"
            )

        else:
            # Penjualan bersih setelah retur
            net_sales = (
                invoice["invoiced_net_sales"]
                - ret.get("returned_net_est", 0)
            )

            # Estimasi COGS setelah retur
            estimated_cogs = (
                invoice["estimated_cogs"]
                - ret.get("returned_cost_est", 0)
            )

            gross_profit = (
                net_sales - estimated_cogs
            )

            profit_margin_pct = (
                round(
                    gross_profit / net_sales * 100,
                    2
                )
                if net_sales > 0 else None
            )

            if gross_profit > 0:
                profit_status = "Untung (estimasi)"

            elif gross_profit < 0:
                profit_status = "Rugi (estimasi)"

            else:
                profit_status = "Impas (estimasi)"

        results.append({
            "customer_code": code,
            "customer_name": c["name"],

            "posted_invoice_count": ar.get(
                "posted_invoice_count", 0
            ),

            "net_sales": net_sales,
            "estimated_cogs": estimated_cogs,
            "estimated_gross_profit": gross_profit,
            "estimated_margin_pct": profit_margin_pct,
            "profit_status": profit_status,

            "opening_ar": opening_ar,
            "unpaid_invoice_ar": unpaid_invoice_ar,
            "outstanding_ar": outstanding_ar,
            "credit_limit": c["credit_limit"],

            "returned_units": ret.get(
                "returned_units", 0
            ),

            "credit_note_gross": ret.get(
                "credit_note_gross", 0
            )
        })

    # Ranking customer paling menguntungkan
    profitable = sorted(
        [
            r for r in results
            if r["estimated_gross_profit"] is not None
            and r["estimated_gross_profit"] > 0
        ],
        key=lambda r: (
            -r["estimated_gross_profit"],
            r["customer_code"]
        )
    )

    # Customer dengan estimasi laba negatif
    losses = sorted(
        [
            r for r in results
            if r["estimated_gross_profit"] is not None
            and r["estimated_gross_profit"] < 0
        ],
        key=lambda r: (
            r["estimated_gross_profit"],
            r["customer_code"]
        )
    )

    customers_with_data = sum(
        r["posted_invoice_count"] > 0
        for r in results
    )

    return ok({
        "summary": {
            "customer_count": len(results),

            "customers_with_posted_invoice":
                customers_with_data,

            "customers_without_posted_invoice":
                len(results) - customers_with_data,

            "total_outstanding_ar": sum(
                r["outstanding_ar"]
                for r in results
            ),

            "total_estimated_gross_profit": (
                sum(
                    r["estimated_gross_profit"] or 0
                    for r in results
                )
                if customers_with_data else None
            )
        },

        "most_profitable": profitable[:5],
        "loss_making": losses,
        "customers": results,

        "note": (
            "Profit merupakan ESTIMASI kumulatif "
            "dari Invoice Posted dikurangi Return Done. "
            "COGS menggunakan standard cost saat ini, "
            "bukan biaya historis. "
            "Piutang = opening AR + sisa Invoice Posted. "
            "Credit Note mengurangi AR invoice terkait. "
            "Opening AR tidak dihitung sebagai laba."
        )
    })
