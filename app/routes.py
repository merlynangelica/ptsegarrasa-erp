from flask import Blueprint, request, jsonify, session
from werkzeug.security import check_password_hash
from .db import get_db, next_number, audit
from .auth import require, allowed
import sqlite3, math

bp = Blueprint("api", __name__, url_prefix="/api")
def ok(data=None, status=200): return jsonify(data or {"ok":True}), status
def money(n): return int(math.floor(float(n)+0.5))
def tax_of(net): return money(net * 0.11)
def rowdicts(rows): return [dict(r) for r in rows]
def user(): return session.get("user",{}).get("username","system")

@bp.post("/login")
def login():
    d=request.get_json() or {}
    r=get_db().execute("SELECT * FROM users WHERE username=? AND active=1",(d.get("username",""),)).fetchone()
    if not r or not check_password_hash(r["password_hash"],d.get("password","")):
        return jsonify({"error":"Username atau password salah"}),401
    session["user_id"]=r["id"]; session["user"]={"username":r["username"],"name":r["full_name"],"role":r["role"]}
    audit(r["username"],"Login",None,r["role"])
    return ok({"user":session["user"]})
@bp.post("/logout")
def logout():
    if session.get("user"): audit(user(),"Logout")
    session.clear(); return ok()

@bp.get("/dashboard")
@require("dashboard.view")
def dashboard():
    db=get_db()
    onhand=db.execute("SELECT COALESCE(SUM(on_hand),0) n FROM stock").fetchone()["n"]
    reserved=db.execute("SELECT COALESCE(SUM(quantity),0) n FROM reservations WHERE active=1").fetchone()["n"]
    stockvalue=db.execute("SELECT COALESCE(SUM(s.on_hand*p.standard_cost),0) n FROM stock s JOIN products p ON p.sku=s.sku").fetchone()["n"]
    ar=db.execute("SELECT COALESCE(SUM(opening_ar),0) n FROM customers").fetchone()["n"] + db.execute("SELECT COALESCE(SUM(total-paid_amount),0) n FROM invoices WHERE status='Posted'").fetchone()["n"]
    ap=db.execute("SELECT COALESCE(SUM(opening_ap),0) n FROM vendors").fetchone()["n"] + db.execute("SELECT COALESCE(SUM(total-paid_amount),0) n FROM vendor_bills WHERE status='Posted'").fetchone()["n"]
    return ok({"company":"PT Segar Rasa Riau","on_hand":onhand,"reserved":reserved,"available":onhand-reserved,
               "stock_value":stockvalue,"ar":ar,"ap":ap,
               "sales_orders":db.execute("SELECT COUNT(*) n FROM sales_orders").fetchone()["n"],
               "purchase_orders":db.execute("SELECT COUNT(*) n FROM purchase_orders").fetchone()["n"],
               "invoices":db.execute("SELECT COUNT(*) n FROM invoices").fetchone()["n"],
               "recent_audit":rowdicts(db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8"))})

@bp.get("/masters/<kind>")
@require("contacts.view")
def masters(kind):
    tables={"customers":"customers","vendors":"vendors","products":"products","warehouses":"warehouses","users":"users"}
    if kind not in tables: return jsonify({"error":"Unknown master"}),404
    if kind=="users":
        rows=get_db().execute("SELECT id,username,full_name,role,active FROM users").fetchall()
    else: rows=get_db().execute(f"SELECT * FROM {tables[kind]}").fetchall()
    return ok(rowdicts(rows))

@bp.get("/stock")
@require("inventory.view")
def stock():
    rows=get_db().execute("""SELECT s.sku,p.name,p.size,s.warehouse_code,w.name warehouse,
      s.on_hand,COALESCE((SELECT SUM(r.quantity) FROM reservations r WHERE r.sku=s.sku AND r.warehouse_code=s.warehouse_code AND r.active=1),0) reserved,
      s.on_hand-COALESCE((SELECT SUM(r.quantity) FROM reservations r WHERE r.sku=s.sku AND r.warehouse_code=s.warehouse_code AND r.active=1),0) available,
      p.standard_cost,s.on_hand*p.standard_cost stock_value
      FROM stock s JOIN products p ON p.sku=s.sku JOIN warehouses w ON w.code=s.warehouse_code ORDER BY s.sku,s.warehouse_code""").fetchall()
    return ok(rowdicts(rows))

@bp.get("/documents/<kind>")
def documents(kind):
    if not session.get("user_id"):
        return jsonify({"error": "Authentication required"}), 401
    perm_map = {
        "sales": "sales.view",
        "purchase": "purchase.view",
        "deliveries": "delivery.view",
        "receipts": "receipt.view",
        "invoices": "invoicing.view",
        "bills": "bills.view",
        "transfers": "transfer.view",
        "returns": "returns.view",
        "leads": "crm.view",
        "journals": "accounting.view",
        "audit": "reports.view"
    }
    perm = perm_map.get(kind, "sales.view")
    if not allowed(perm):
        return jsonify({"error": "Akses ditolak untuk role ini", "required_permission": perm}), 403
    db = get_db()
    q = {
        "sales": "SELECT so.*, c.name customer FROM sales_orders so JOIN customers c ON c.code=so.customer_code ORDER BY so.id DESC",
        "purchase": "SELECT po.*, v.name vendor FROM purchase_orders po JOIN vendors v ON v.code=po.vendor_code ORDER BY po.id DESC",
        "deliveries": "SELECT d.id, d.number, so.number sales_order, c.name customer, d.warehouse_code, d.status, d.created_at, d.validated_at FROM deliveries d JOIN sales_orders so ON so.id=d.order_id JOIN customers c ON c.code=so.customer_code ORDER BY d.id DESC",
        "receipts": "SELECT r.*, po.number purchase_order FROM receipts r JOIN purchase_orders po ON po.id=r.purchase_order_id ORDER BY r.id DESC",
        "invoices": "SELECT i.*, so.number sales_order, c.name customer FROM invoices i JOIN sales_orders so ON so.id=i.order_id JOIN customers c ON c.code=so.customer_code ORDER BY i.id DESC",
        "bills": "SELECT b.*, po.number purchase_order FROM vendor_bills b JOIN purchase_orders po ON po.id=b.purchase_order_id ORDER BY b.id DESC",
        "transfers": "SELECT * FROM transfers ORDER BY id DESC",
        "returns": "SELECT * FROM returns ORDER BY id DESC",
        "leads": "SELECT * FROM leads ORDER BY id DESC",
        "journals": "SELECT * FROM journal_entries ORDER BY id DESC",
        "audit": "SELECT * FROM audit_logs ORDER BY id DESC"
    }
    if kind not in q:
        return jsonify({"error": "Unknown document type"}), 404
    return ok(rowdicts(db.execute(q[kind]).fetchall()))

@bp.post("/sales-orders")
@require("sales.create")
def create_so():
    d=request.get_json() or {}; db=get_db()
    lines=d.get("lines",[])
    if not lines:return jsonify({"error":"Tambahkan minimal satu product line"}),400
    if not db.execute("SELECT 1 FROM customers WHERE code=?",(d.get("customer_code"),)).fetchone():return jsonify({"error":"Customer tidak ditemukan"}),400
    discount=float(d.get("discount_pct",0))
    if discount<0 or discount>100:return jsonify({"error":"Discount tidak valid"}),400
    subtotal=0; prepared=[]
    for line in lines:
        p=db.execute("SELECT * FROM products WHERE sku=?",(line.get("sku"),)).fetchone()
        qty=int(line.get("quantity",0))
        if not p or qty<=0:return jsonify({"error":"SKU/quantity tidak valid"}),400
        net=money(qty*int(p["sales_price"])*(1-discount/100)); tax=tax_of(net)
        prepared.append((p["sku"],qty,p["sales_price"],discount,net,tax,net+tax));subtotal+=net
    tax=sum(x[5] for x in prepared); total=subtotal+tax
    no=next_number("SO")
    cur=db.execute("INSERT INTO sales_orders(number,customer_code,status,discount_pct,subtotal,tax,total,warehouse_code,created_by) VALUES(?,?,'Draft',?,?,?,?,?,?)",
        (no,d["customer_code"],discount,subtotal,tax,total,d.get("warehouse_code","WH-PKU"),user()))
    oid=cur.lastrowid
    for sku,qty,price,disc,net,tx,gross in prepared:
        db.execute("INSERT INTO sales_order_lines(order_id,sku,quantity,unit_price,discount_pct,subtotal,tax,total) VALUES(?,?,?,?,?,?,?,?)",(oid,sku,qty,price,disc,net,tx,gross))
    audit(user(),"Create Sales Order",no,f"Total {total}")
    db.commit();return ok({"id":oid,"number":no,"total":total},201)

@bp.post("/sales-orders/<int:oid>/confirm")
@require("sales.confirm")
def confirm_so(oid):
    db=get_db(); so=db.execute("SELECT * FROM sales_orders WHERE id=?",(oid,)).fetchone()
    if not so:return jsonify({"error":"SO tidak ditemukan"}),404
    if so["status"]!="Draft":return jsonify({"error":"SO bukan Draft"}),409
    if so["discount_pct"]>10 and session["user"]["role"]!="Owner":
        return jsonify({"error":"Diskon di atas 10% memerlukan approval Owner"}),409
    ar=db.execute("SELECT COALESCE(SUM(opening_ar),0) n FROM customers WHERE code=?",(so["customer_code"],)).fetchone()["n"]
    ar += db.execute("""
        SELECT COALESCE(SUM(i.total - i.paid_amount), 0) AS n
        FROM invoices i
        JOIN sales_orders s ON s.id = i.order_id
        WHERE s.customer_code = ?
          AND i.status = 'Posted'
    """, (so["customer_code"],)).fetchone()["n"]
    uninvoiced=db.execute("""SELECT COALESCE(SUM(so.total-COALESCE(inv.invoiced,0)),0) n FROM sales_orders so
      LEFT JOIN (SELECT order_id,SUM(total) invoiced FROM invoices WHERE status='Posted' GROUP BY order_id) inv ON inv.order_id=so.id
      WHERE so.customer_code=? AND so.status='Confirmed'""",(so["customer_code"],)).fetchone()["n"]
    customer=db.execute("SELECT credit_limit FROM customers WHERE code=?",(so["customer_code"],)).fetchone()
    exposure=ar+uninvoiced+so["total"]
    if exposure>customer["credit_limit"]:return jsonify({"error":f"Credit limit terlampaui. Exposure {exposure:,}; limit {customer['credit_limit']:,}"}),409
    lines=db.execute("SELECT * FROM sales_order_lines WHERE order_id=?",(oid,)).fetchall()
    for line in lines:
        r=db.execute("SELECT COALESCE(SUM(quantity),0) n FROM reservations WHERE sku=? AND warehouse_code=? AND active=1",(line["sku"],so["warehouse_code"])).fetchone()["n"]
        s=db.execute("SELECT on_hand FROM stock WHERE sku=? AND warehouse_code=?",(line["sku"],so["warehouse_code"])).fetchone()
        available=(s["on_hand"] if s else 0)-r
        if line["quantity"]>available:return jsonify({"error":f"Available stock {available} tidak cukup untuk {line['sku']}"}),409
    for line in lines:
        db.execute("INSERT INTO reservations(document_no,sku,warehouse_code,quantity,active) VALUES(?,?,?,?,1)",(so["number"],line["sku"],so["warehouse_code"],line["quantity"]))
    db.execute("UPDATE sales_orders SET status='Confirmed',confirmed_at=CURRENT_TIMESTAMP WHERE id=?",(oid,))
    audit(user(),"Confirm + reserve",so["number"],"Stock reservation created");db.commit()
    return ok({"status":"Confirmed","number":so["number"]})

@bp.get("/sales-orders/for-delivery")
@require("delivery.view")
def sales_orders_for_delivery():
    db = get_db()
    sos = db.execute("""
        SELECT so.id, so.number, so.customer_code, c.name as customer_name,
               so.warehouse_code, so.subtotal, so.total, so.confirmed_at
        FROM sales_orders so
        JOIN customers c ON c.code = so.customer_code
        WHERE so.status = 'Confirmed'
        ORDER BY so.id DESC
    """).fetchall()

    result = []
    for so in sos:
        lines = db.execute("""
            SELECT sol.id, sol.sku, p.name as product_name, p.size, p.unit,
                   sol.quantity as ordered_qty, sol.unit_price, p.standard_cost
            FROM sales_order_lines sol
            JOIN products p ON p.sku = sol.sku
            WHERE sol.order_id = ?
        """, (so["id"],)).fetchall()

        line_data = []
        has_undelivered = False
        for l in lines:
            delivered = db.execute("""
                SELECT COALESCE(SUM(dl.quantity), 0) as n
                FROM delivery_lines dl
                JOIN deliveries d ON d.id = dl.delivery_id
                WHERE d.order_id = ? AND d.status = 'Done' AND dl.sku = ?
            """, (so["id"], l["sku"])).fetchone()["n"]

            pending_ready = db.execute("""
                SELECT COALESCE(SUM(dl.quantity), 0) as n
                FROM delivery_lines dl
                JOIN deliveries d ON d.id = dl.delivery_id
                WHERE d.order_id = ? AND d.status = 'Ready' AND dl.sku = ?
            """, (so["id"], l["sku"])).fetchone()["n"]

            remaining = l["ordered_qty"] - delivered
            if remaining > 0:
                has_undelivered = True

            stock_row = db.execute("""
                SELECT s.on_hand,
                       COALESCE((SELECT SUM(r.quantity) FROM reservations r WHERE r.sku=s.sku AND r.warehouse_code=s.warehouse_code AND r.active=1), 0) as reserved
                FROM stock s
                WHERE s.sku = ? AND s.warehouse_code = ?
            """, (l["sku"], so["warehouse_code"])).fetchone()

            on_hand = stock_row["on_hand"] if stock_row else 0
            reserved = stock_row["reserved"] if stock_row else 0
            available = on_hand - reserved

            line_data.append({
                "sku": l["sku"],
                "product_name": f"{l['product_name']} {l['size'] or ''}".strip(),
                "unit": l["unit"],
                "ordered_qty": l["ordered_qty"],
                "delivered_qty": delivered,
                "pending_ready_qty": pending_ready,
                "remaining_qty": remaining,
                "on_hand": on_hand,
                "reserved": reserved,
                "available": available
            })

        if has_undelivered:
            so_dict = dict(so)
            so_dict["lines"] = line_data
            result.append(so_dict)

    return ok(result)

@bp.get("/deliveries/<int:did>")
@require("delivery.view")
def get_delivery_detail(did):
    db = get_db()
    d = db.execute("""
        SELECT d.*, so.number as sales_order, so.customer_code, c.name as customer_name
        FROM deliveries d
        JOIN sales_orders so ON so.id = d.order_id
        JOIN customers c ON c.code = so.customer_code
        WHERE d.id = ?
    """, (did,)).fetchone()
    if not d:
        return jsonify({"error": "Delivery tidak ditemukan"}), 404

    lines = db.execute("""
        SELECT dl.id, dl.sku, p.name as product_name, p.size, p.unit, dl.quantity,
               p.standard_cost, (dl.quantity * p.standard_cost) as cogs_amount,
               sol.quantity as ordered_qty
        FROM delivery_lines dl
        JOIN products p ON p.sku = dl.sku
        JOIN sales_order_lines sol ON sol.order_id = ? AND sol.sku = dl.sku
        WHERE dl.delivery_id = ?
    """, (d["order_id"], did)).fetchall()

    detailed_lines = []
    total_cogs = 0
    total_qty = 0
    for l in lines:
        delivered_total = db.execute("""
            SELECT COALESCE(SUM(dl2.quantity), 0) as n
            FROM delivery_lines dl2
            JOIN deliveries d2 ON d2.id = dl2.delivery_id
            WHERE d2.order_id = ? AND d2.status = 'Done' AND dl2.sku = ?
        """, (d["order_id"], l["sku"])).fetchone()["n"]

        backorder = max(0, l["ordered_qty"] - delivered_total)
        total_cogs += l["cogs_amount"]
        total_qty += l["quantity"]
        detailed_lines.append({
            "sku": l["sku"],
            "product_name": f"{l['product_name']} {l['size'] or ''}".strip(),
            "unit": l["unit"],
            "delivery_qty": l["quantity"],
            "ordered_qty": l["ordered_qty"],
            "delivered_total": delivered_total,
            "backorder_qty": backorder,
            "standard_cost": l["standard_cost"],
            "cogs_amount": l["cogs_amount"]
        })

    journal = db.execute("""
        SELECT number, status, created_at
        FROM journal_entries
        WHERE source_type = 'Delivery' AND source_id = ?
    """, (did,)).fetchone()

    res = dict(d)
    res["lines"] = detailed_lines
    res["total_cogs"] = total_cogs
    res["total_quantity"] = total_qty
    res["journal_entry"] = dict(journal) if journal else None
    return ok(res)

@bp.post("/sales-orders/<int:oid>/delivery")
@require("delivery.view")
def create_delivery(oid):
    d = request.get_json() or {}
    db = get_db()
    so = db.execute("SELECT * FROM sales_orders WHERE id=?", (oid,)).fetchone()
    if not so:
        return jsonify({"error": "Sales Order tidak ditemukan"}), 404
    if so["status"] != "Confirmed":
        return jsonify({"error": "Sales Order harus berstatus Confirmed untuk membuat pengiriman"}), 409

    qtymap = d.get("quantities", {})
    lines = db.execute("SELECT * FROM sales_order_lines WHERE order_id=?", (oid,)).fetchall()

    prepared_lines = []
    total_qty = 0
    for line in lines:
        qty = int(qtymap.get(line["sku"], 0))
        if qty < 0:
            return jsonify({"error": f"Kuantitas untuk {line['sku']} tidak boleh negatif"}), 400

        delivered = db.execute("""
            SELECT COALESCE(SUM(dl.quantity), 0) as n
            FROM delivery_lines dl
            JOIN deliveries d ON d.id = dl.delivery_id
            WHERE d.order_id = ? AND d.status = 'Done' AND dl.sku = ?
        """, (oid, line["sku"])).fetchone()["n"]

        pending_ready = db.execute("""
            SELECT COALESCE(SUM(dl.quantity), 0) as n
            FROM delivery_lines dl
            JOIN deliveries d ON d.id = dl.delivery_id
            WHERE d.order_id = ? AND d.status = 'Ready' AND dl.sku = ?
        """, (oid, line["sku"])).fetchone()["n"]

        remaining = line["quantity"] - delivered
        if qty > remaining:
            return jsonify({"error": f"Kuantitas {qty} melebihi sisa pesanan ({remaining}) untuk {line['sku']}"}), 409
        if qty > (remaining - pending_ready):
            return jsonify({"error": f"Sudah ada Delivery Ready ({pending_ready} unit). Kuantitas pengiriman melebihi sisa yang dapat dialokasikan ({remaining - pending_ready}) untuk {line['sku']}"}), 409

        if qty > 0:
            prepared_lines.append((line["sku"], qty))
            total_qty += qty

    if total_qty <= 0:
        return jsonify({"error": "Kuantitas pengiriman minimal 1 botol/unit"}), 400

    no = next_number("DO")
    cur = db.execute("INSERT INTO deliveries(number, order_id, status, warehouse_code) VALUES(?, ?, 'Ready', ?)",
                     (no, oid, so["warehouse_code"]))
    did = cur.lastrowid
    for sku, qty in prepared_lines:
        db.execute("INSERT INTO delivery_lines(delivery_id, sku, quantity) VALUES(?, ?, ?)", (did, sku, qty))

    audit(user(), "Create Delivery", no, f"{so['number']} · {total_qty} units")
    db.commit()
    return ok({"id": did, "number": no, "status": "Ready", "total_items": total_qty}, 201)

@bp.post("/deliveries/<int:did>/validate")
@require("delivery.validate")
def validate_delivery(did):
    db = get_db()
    d = db.execute("SELECT * FROM deliveries WHERE id=?", (did,)).fetchone()
    if not d:
        return jsonify({"error": "Delivery tidak ditemukan"}), 404
    # Idempotent: Validating an already Done delivery must not alter stock or create duplicate journals (SR-15, T-26)
    if d["status"] == "Done":
        return ok({"status": "Done", "idempotent": True, "message": "Delivery sudah divalidasi sebelumnya"})
    if d["status"] != "Ready":
        return jsonify({"error": f"Delivery berstatus {d['status']}, tidak dapat divalidasi"}), 409

    lines = db.execute("SELECT * FROM delivery_lines WHERE delivery_id=?", (did,)).fetchall()
    if not lines:
        return jsonify({"error": "Delivery kosong"}), 400

    so = db.execute("SELECT * FROM sales_orders WHERE id=?", (d["order_id"],)).fetchone()
    if not so:
        return jsonify({"error": "Sales Order terkait tidak ditemukan"}), 404

    # 1. Validation checks (SR-04)
    for l in lines:
        s = db.execute("SELECT on_hand FROM stock WHERE sku=? AND warehouse_code=?", (l["sku"], d["warehouse_code"])).fetchone()
        if not s or l["quantity"] > s["on_hand"]:
            return jsonify({"error": f"Stok On Hand ({s['on_hand'] if s else 0}) tidak cukup untuk {l['sku']}"}), 409

        # Check this SO's own active reservation
        own = db.execute("""
            SELECT COALESCE(SUM(quantity), 0) as n
            FROM reservations
            WHERE document_no = ? AND sku = ? AND warehouse_code = ? AND active = 1
        """, (so["number"], l["sku"], d["warehouse_code"])).fetchone()["n"]
        if l["quantity"] > own:
            return jsonify({"error": f"Kuantitas pengiriman ({l['quantity']}) melebihi reservasi pesanan ({own}) untuk {l['sku']}"}), 409

        # Check that other orders' reservations are protected (SR-04)
        total_reserved = db.execute("""
            SELECT COALESCE(SUM(quantity), 0) as n
            FROM reservations
            WHERE sku = ? AND warehouse_code = ? AND active = 1
        """, (l["sku"], d["warehouse_code"])).fetchone()["n"]
        other_reserved = total_reserved - own
        if s["on_hand"] - other_reserved < l["quantity"]:
            return jsonify({"error": f"Pengiriman akan mengambil stok ter-reservasi milik pesanan lain untuk {l['sku']}"}), 409

    # 2. Execution: reduce stock, decrease reservation, compute COGS
    total_cogs = 0
    for l in lines:
        db.execute("UPDATE stock SET on_hand = on_hand - ? WHERE sku = ? AND warehouse_code = ?", (l["quantity"], l["sku"], d["warehouse_code"]))
        db.execute("""
            UPDATE reservations
            SET quantity = quantity - ?
            WHERE document_no = ? AND sku = ? AND warehouse_code = ? AND active = 1
        """, (l["quantity"], so["number"], l["sku"], d["warehouse_code"]))
        db.execute("UPDATE reservations SET active = 0 WHERE quantity <= 0")

        cost = db.execute("SELECT standard_cost FROM products WHERE sku=?", (l["sku"],)).fetchone()["standard_cost"]
        total_cogs += cost * l["quantity"]

    # Balanced journal entry: Dr 5100 COGS, Cr 1200 Inventory (F-15)
    jno = next_number("MISC")
    db.execute("INSERT INTO journal_entries(number, source_type, source_id, status) VALUES(?, 'Delivery', ?, 'Posted')", (jno, did))
    jid = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
    db.executemany("INSERT INTO journal_lines(journal_id, account_code, debit, credit) VALUES(?, ?, ?, ?)", [
        (jid, "5100", total_cogs, 0),
        (jid, "1200", 0, total_cogs)
    ])

    db.execute("UPDATE deliveries SET status = 'Done', validated_at = CURRENT_TIMESTAMP WHERE id = ?", (did,))
    audit(user(), "Validate Delivery", d["number"], f"Stock decrease + COGS journal Rp{total_cogs:,}")
    db.commit()
    return ok({"status": "Done", "number": d["number"], "total_cogs": total_cogs, "journal_number": jno})

@bp.post("/invoices/from-order/<int:oid>")
@require("invoicing.post")
def invoice_from_order(oid):
    db=get_db();so=db.execute("SELECT * FROM sales_orders WHERE id=?",(oid,)).fetchone()
    if not so or so["status"]!="Confirmed":return jsonify({"error":"SO belum Confirmed"}),409
    lines=db.execute("SELECT * FROM sales_order_lines WHERE order_id=?",(oid,)).fetchall()
    invoice_lines=[];net=0;tax=0
    for l in lines:
        delivered=db.execute("SELECT COALESCE(SUM(dl.quantity),0) n FROM delivery_lines dl JOIN deliveries d ON d.id=dl.delivery_id WHERE d.order_id=? AND d.status='Done' AND dl.sku=?",(oid,l["sku"])).fetchone()["n"]
        already=db.execute("SELECT COALESCE(SUM(il.quantity),0) n FROM invoice_lines il JOIN invoices i ON i.id=il.invoice_id WHERE i.order_id=? AND i.status IN ('Draft', 'Posted') AND il.sku=?",(oid,l["sku"])).fetchone()["n"]
        qty=delivered-already
        if qty>0:
            n=money(qty*l["unit_price"]*(1-l["discount_pct"]/100));t=tax_of(n)
            invoice_lines.append((l["sku"],qty,l["unit_price"],n,t));net+=n;tax+=t
    if not invoice_lines:return jsonify({"error":"Belum ada delivered quantity yang dapat ditagih"}),409
    no=next_number("INV");cur=db.execute("INSERT INTO invoices(number,order_id,status,net_amount,tax_amount,total) VALUES(?,?,'Draft',?,?,?)",(no,oid,net,tax,net+tax))
    iid=cur.lastrowid
    for x in invoice_lines:db.execute("INSERT INTO invoice_lines(invoice_id,sku,quantity,unit_price,net_amount,tax_amount) VALUES(?,?,?,?,?,?)",(iid,*x))
    audit(user(),"Create Customer Invoice",no,so["number"]);db.commit();return ok({"id":iid,"number":no,"net":net,"tax":tax,"total":net+tax},201)

@bp.post("/invoices/<int:iid>/post")
@require("invoicing.post")
def post_invoice(iid):
    db=get_db();inv=db.execute("SELECT * FROM invoices WHERE id=?",(iid,)).fetchone()
    if not inv:return jsonify({"error":"Invoice tidak ditemukan"}),404
    if inv["status"]=="Posted":return ok({"status":"Posted","idempotent":True})
    if inv["status"]!="Draft":return jsonify({"error":"Invalid status"}),409
    db.execute("UPDATE invoices SET status='Posted' WHERE id=?",(iid,))
    no=next_number("MISC");db.execute("INSERT INTO journal_entries(number,source_type,source_id) VALUES(?,'Customer Invoice',?)",(no,iid))
    jid=db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    db.executemany("INSERT INTO journal_lines(journal_id,account_code,debit,credit) VALUES(?,?,?,?)",[(jid,"1100",inv["total"],0),(jid,"4100",0,inv["net_amount"]),(jid,"2200",0,inv["tax_amount"])])
    audit(user(),"Post Customer Invoice",inv["number"],"AR + Revenue + VAT Output");db.commit();return ok({"status":"Posted"})

@bp.post("/purchase-orders")
@require("purchase.create")
def create_po():
    d=request.get_json() or {};db=get_db();lines=d.get("lines",[])
    if not lines:return jsonify({"error":"Tambahkan product line"}),400
    if not db.execute("SELECT 1 FROM vendors WHERE code=?",(d.get("vendor_code"),)).fetchone():return jsonify({"error":"Vendor tidak ditemukan"}),400
    prepared=[];subtotal=0
    for x in lines:
        p=db.execute("SELECT * FROM products WHERE sku=?",(x.get("sku"),)).fetchone();qty=int(x.get("quantity",0));cost=int(x.get("unit_cost",p["standard_cost"] if p else 0))
        if not p or qty<=0 or cost<0:return jsonify({"error":"SKU/quantity/cost tidak valid"}),400
        n=qty*cost;subtotal+=n;prepared.append((p["sku"],qty,cost,n))
    tax=tax_of(subtotal);no=next_number("PO")
    cur=db.execute("INSERT INTO purchase_orders(number,vendor_code,status,subtotal,tax,total,created_by) VALUES(?,?,'RFQ',?,?,?,?)",(no,d["vendor_code"],subtotal,tax,subtotal+tax,user()))
    oid=cur.lastrowid
    for x in prepared:db.execute("INSERT INTO purchase_order_lines(order_id,sku,quantity,unit_cost,subtotal) VALUES(?,?,?,?,?)",(oid,*x))
    audit(user(),"Create RFQ",no);db.commit();return ok({"id":oid,"number":no,"subtotal":subtotal,"total":subtotal+tax},201)

@bp.post("/purchase-orders/<int:oid>/confirm")
@require("purchase.confirm")
def confirm_po(oid):
    db=get_db();po=db.execute("SELECT * FROM purchase_orders WHERE id=?",(oid,)).fetchone()
    if not po:return jsonify({"error":"PO tidak ditemukan"}),404
    if po["status"] not in ("RFQ","Pending Approval"):return jsonify({"error":"PO bukan RFQ"}),409
    threshold=int(db.execute("SELECT value FROM settings WHERE key='large_po_threshold'").fetchone()["value"])
    if po["subtotal"]>threshold and session["user"]["role"]!="Owner":
        db.execute("UPDATE purchase_orders SET status='Pending Approval' WHERE id=?",(oid,))
        audit(user(),"Request PO Approval",po["number"]);db.commit()
        return ok({"status":"Pending Approval","message":"Menunggu Owner"})
    db.execute("UPDATE purchase_orders SET status='Confirmed' WHERE id=?",(oid,))
    audit(user(),"Confirm Purchase Order",po["number"]);db.commit();return ok({"status":"Confirmed"})

@bp.post("/purchase-orders/<int:oid>/approve")
@require("purchase.confirm")
def approve_po(oid):
    if session["user"]["role"]!="Owner":return jsonify({"error":"Hanya Owner"}),403
    db=get_db();po=db.execute("SELECT * FROM purchase_orders WHERE id=?",(oid,)).fetchone()
    if not po or po["status"]!="Pending Approval":return jsonify({"error":"PO tidak menunggu approval"}),409
    db.execute("UPDATE purchase_orders SET status='Confirmed' WHERE id=?",(oid,))
    audit(user(),"Approve Purchase Order",po["number"]);db.commit();return ok({"status":"Confirmed"})

@bp.post("/purchase-orders/<int:oid>/receipt")
@require("receipt.view")
def create_receipt(oid):
    d=request.get_json() or {};db=get_db();po=db.execute("SELECT * FROM purchase_orders WHERE id=?",(oid,)).fetchone()
    if not po or po["status"]!="Confirmed":return jsonify({"error":"PO harus Confirmed"}),409
    no=next_number("WHIN");cur=db.execute("INSERT INTO receipts(number,purchase_order_id,warehouse_code) VALUES(?,?,?)",(no,oid,d.get("warehouse_code","WH-PKU")))
    rid=cur.lastrowid
    for x in d.get("lines",[]):
        pol=db.execute("SELECT * FROM purchase_order_lines WHERE order_id=? AND sku=?",(oid,x.get("sku"),)).fetchone()
        if not pol:return jsonify({"error":"SKU tidak ada di PO"}),400
        qty=int(x.get("received_qty",0));accepted=int(x.get("accepted_qty",0));rejected=int(x.get("rejected_qty",0))
        already=db.execute("SELECT COALESCE(SUM(received_qty),0) n FROM receipt_lines rl JOIN receipts r ON r.id=rl.receipt_id WHERE r.purchase_order_id=? AND rl.sku=? AND r.status='Done'",(oid,x["sku"])).fetchone()["n"]
        if qty<=0 or accepted<0 or rejected<0 or accepted+rejected!=qty or already+qty>pol["quantity"]:
            return jsonify({"error":"Qty receipt/accepted/rejected tidak valid atau melebihi PO"}),409
        db.execute("INSERT INTO receipt_lines(receipt_id,sku,received_qty,accepted_qty,rejected_qty,quality_reason) VALUES(?,?,?,?,?,?)",(rid,x["sku"],qty,accepted,rejected,x.get("quality_reason")))
    audit(user(),"Create Receipt",no,po["number"]);db.commit();return ok({"id":rid,"number":no},201)

@bp.post("/receipts/<int:rid>/validate")
@require("quality.check")
def validate_receipt(rid):
    db=get_db();r=db.execute("SELECT * FROM receipts WHERE id=?",(rid,)).fetchone()
    if not r or r["status"]!="Draft":return jsonify({"error":"Receipt bukan Draft"}),409
    lines=db.execute("SELECT rl.*,pol.unit_cost FROM receipt_lines rl JOIN purchase_orders po ON po.id=? JOIN purchase_order_lines pol ON pol.order_id=po.id AND pol.sku=rl.sku WHERE rl.receipt_id=?",(r["purchase_order_id"],rid)).fetchall()
    if not lines:return jsonify({"error":"Receipt tidak memiliki baris"}),400
    for l in lines:
        db.execute("INSERT INTO stock(sku,warehouse_code,on_hand) VALUES(?,?,?) ON CONFLICT(sku,warehouse_code) DO UPDATE SET on_hand=on_hand+excluded.on_hand",(l["sku"],r["warehouse_code"],l["accepted_qty"]))
        if l["rejected_qty"]:
            audit(user(),"Quality Fail",r["number"],f"{l['sku']} rejected {l['rejected_qty']}: {l['quality_reason'] or 'No reason'}")
    db.execute("UPDATE receipts SET status='Done' WHERE id=?",(rid,))
    audit(user(),"Validate Receipt",r["number"],"Only accepted quantity added to sellable stock");db.commit();return ok({"status":"Done"})

@bp.post("/vendor-bills/from-receipt/<int:rid>")
@require("bills.post")
def bill_from_receipt(rid):
    db=get_db();r=db.execute("SELECT * FROM receipts WHERE id=?",(rid,)).fetchone()
    if not r or r["status"]!="Done":return jsonify({"error":"Receipt belum tervalidasi"}),409
    po=db.execute("SELECT * FROM purchase_orders WHERE id=?",(r["purchase_order_id"],)).fetchone()
    lines=db.execute("SELECT rl.*,pol.unit_cost FROM receipt_lines rl JOIN purchase_order_lines pol ON pol.order_id=? AND pol.sku=rl.sku WHERE rl.receipt_id=?",(po["id"],rid)).fetchall()
    net=sum(l["accepted_qty"]*l["unit_cost"] for l in lines);tax=tax_of(net);no=next_number("BILL")
    cur=db.execute("INSERT INTO vendor_bills(number,purchase_order_id,receipt_id,status,net_amount,tax_amount,total) VALUES(?,?,?,'Draft',?,?,?)",(no,po["id"],rid,net,tax,net+tax))
    audit(user(),"Create Vendor Bill",no,"Three-way match PO-Receipt-Accepted quantity");db.commit()
    return ok({"id":cur.lastrowid,"number":no,"net":net,"tax":tax,"total":net+tax},201)

@bp.post("/vendor-bills/<int:bid>/post")
@require("bills.post")
def post_bill(bid):
    db=get_db();b=db.execute("SELECT * FROM vendor_bills WHERE id=?",(bid,)).fetchone()
    if not b:return jsonify({"error":"Bill tidak ditemukan"}),404
    if b["status"]=="Posted":return ok({"status":"Posted","idempotent":True})
    db.execute("UPDATE vendor_bills SET status='Posted' WHERE id=?",(bid,))
    no=next_number("MISC");db.execute("INSERT INTO journal_entries(number,source_type,source_id) VALUES(?,'Vendor Bill',?)",(no,bid))
    jid=db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    db.executemany("INSERT INTO journal_lines(journal_id,account_code,debit,credit) VALUES(?,?,?,?)",[(jid,"1200",b["net_amount"],0),(jid,"1300",b["tax_amount"],0),(jid,"2100",0,b["total"])])
    audit(user(),"Post Vendor Bill",b["number"]);db.commit();return ok({"status":"Posted"})

@bp.post("/payments")
@require("payments.create")
def payment():
    d=request.get_json() or {};db=get_db();typ=d.get("document_type");docid=int(d.get("document_id",0));amount=int(d.get("amount",0))
    table="invoices" if typ=="customer" else "vendor_bills" if typ=="vendor" else None
    if not table or amount<=0:return jsonify({"error":"Payment tidak valid"}),400
    doc=db.execute(f"SELECT * FROM {table} WHERE id=? AND status='Posted'",(docid,)).fetchone()
    if not doc:return jsonify({"error":"Dokumen harus Posted"}),409
    residual=doc["total"]-doc["paid_amount"]
    if amount>residual:return jsonify({"error":f"Payment melebihi residual {residual}"}),409
    no=next_number("PAY");db.execute("INSERT INTO payments(number,document_type,document_id,amount,created_by) VALUES(?,?,?,?,?)",(no,typ,docid,amount,user()))
    db.execute(f"UPDATE {table} SET paid_amount=paid_amount+? WHERE id=?",(amount,docid))
    # Cash/bank and receivable/payable clearing
    jno=next_number("MISC");db.execute("INSERT INTO journal_entries(number,source_type,source_id) VALUES(?,'Payment',?)",(jno,docid))
    jid=db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    if typ=="customer": entries=[(jid,"1010",amount,0),(jid,"1100",0,amount)]
    else: entries=[(jid,"2100",amount,0),(jid,"1010",0,amount)]
    db.executemany("INSERT INTO journal_lines(journal_id,account_code,debit,credit) VALUES(?,?,?,?)",entries)
    audit(user(),"Register Payment",no,f"{typ} {amount}");db.commit();return ok({"number":no,"residual":residual-amount,"status":"Paid" if residual==amount else "Partial"},201)

@bp.post("/transfers")
@require("transfer.create")
def transfer():
    d=request.get_json() or {};db=get_db();sku=d.get("sku");qty=int(d.get("quantity",0));src=d.get("from_warehouse");dst=d.get("to_warehouse")
    if src==dst or qty<=0:return jsonify({"error":"Transfer tidak valid"}),400
    s=db.execute("SELECT on_hand FROM stock WHERE sku=? AND warehouse_code=?",(sku,src)).fetchone()
    if not s or s["on_hand"]<qty:return jsonify({"error":"On Hand lokasi asal tidak cukup"}),409
    no=next_number("INT");cur=db.execute("INSERT INTO transfers(number,from_warehouse,to_warehouse,sku,quantity,status,created_by) VALUES(?,?,?,?,?,'Done',?)",(no,src,dst,sku,qty,user()))
    db.execute("UPDATE stock SET on_hand=on_hand-? WHERE sku=? AND warehouse_code=?",(qty,sku,src))
    db.execute("INSERT INTO stock(sku,warehouse_code,on_hand) VALUES(?,?,?) ON CONFLICT(sku,warehouse_code) DO UPDATE SET on_hand=on_hand+excluded.on_hand",(sku,dst,qty))
    audit(user(),"Internal Transfer",no,f"{src} → {dst}, {sku} x {qty}");db.commit();return ok({"number":no,"status":"Done"},201)

@bp.post("/returns")
@require("returns.validate")
def create_return():
    d=request.get_json() or {};db=get_db();iid=int(d.get("invoice_id",0));sku=d.get("sku");qty=int(d.get("quantity",0))
    inv=db.execute("SELECT * FROM invoices WHERE id=? AND status='Posted'",(iid,)).fetchone()
    if not inv or qty<=0:return jsonify({"error":"Invoice/qty tidak valid"}),400
    invoiced=db.execute("SELECT COALESCE(SUM(quantity),0) n FROM invoice_lines WHERE invoice_id=? AND sku=?",(iid,sku)).fetchone()["n"]
    returned=db.execute("SELECT COALESCE(SUM(quantity),0) n FROM returns WHERE invoice_id=? AND sku=? AND status='Done'",(iid,sku)).fetchone()["n"]
    if qty>invoiced-returned:return jsonify({"error":"Return melebihi net invoiced quantity"}),409
    line=db.execute("SELECT * FROM invoice_lines WHERE invoice_id=? AND sku=?",(iid,sku)).fetchone()
    net=qty*line["unit_price"];credit=net+tax_of(net);no=next_number("RET")
    cur=db.execute("INSERT INTO returns(number,invoice_id,sku,quantity,status,credit_total) VALUES(?,?,?,?,'Done',?)",(no,iid,sku,qty,credit))
    cost=db.execute("SELECT standard_cost FROM products WHERE sku=?",(sku,)).fetchone()["standard_cost"]
    # Simplified sellable return: put returned quantity back into source WH
    soid=db.execute("SELECT order_id FROM invoices WHERE id=?",(iid,)).fetchone()["order_id"]
    wh=db.execute("SELECT warehouse_code FROM sales_orders WHERE id=?",(soid,)).fetchone()["warehouse_code"]
    db.execute("UPDATE stock SET on_hand=on_hand+? WHERE sku=? AND warehouse_code=?",(qty,sku,wh))
    audit(user(),"Customer Return + Credit Note",no,f"Credit {credit}; COGS reversal {qty*cost}");db.commit()
    return ok({"number":no,"credit_total":credit,"cogs_reversal":qty*cost},201)

@bp.get("/accounting/trial-balance")
@require("accounting.view")
def trial_balance():
    rows=get_db().execute("""SELECT account_code,SUM(debit) debit,SUM(credit) credit
      FROM journal_lines GROUP BY account_code ORDER BY account_code""").fetchall()
    out=rowdicts(rows)
    return ok({"lines":out,"total_debit":sum(x["debit"] for x in out),"total_credit":sum(x["credit"] for x in out)})

@bp.get("/reports")
@require("reports.view")
def reports():
    db=get_db()
    return ok({"sales_by_status":rowdicts(db.execute("SELECT status,COUNT(*) count,SUM(total) amount FROM sales_orders GROUP BY status").fetchall()),
               "purchase_by_status":rowdicts(db.execute("SELECT status,COUNT(*) count,SUM(total) amount FROM purchase_orders GROUP BY status").fetchall()),
               "stock_by_warehouse":rowdicts(db.execute("SELECT warehouse_code,SUM(on_hand) units FROM stock GROUP BY warehouse_code").fetchall())})

@bp.post("/seed/reset")
@require("*")
def reset_seed():
    # Owner-only destructive reset; rebuilds the baseline fixture.
    if session.get("user",{}).get("role")!="Owner":return jsonify({"error":"Owner only"}),403
    db=get_db()
    for t in ["journal_lines","journal_entries","returns","transfers","payments","vendor_bills","invoice_lines","invoices","receipt_lines","receipts","purchase_order_lines","purchase_orders","delivery_lines","deliveries","sales_order_lines","sales_orders","reservations","leads","audit_logs","reorder_points","stock","products","vendors","customers","warehouses","sequences","settings","users"]:
        db.execute(f"DELETE FROM {t}")
    db.commit()
    from .db import seed_db
    seed_db()
    return ok({"message":"Baseline reset berhasil"})


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
            "Retur ditampilkan terpisah dan belum "
            "dikurangkan dari profit karena posting "
            "Credit Note belum lengkap."
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
                i.total - i.paid_amount
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
            "Retur belum otomatis mengurangi AR "
            "dalam starter ERP ini. "
            "Opening AR tidak dihitung sebagai laba."
        )
    })
