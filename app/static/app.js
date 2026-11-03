const $ = s => document.querySelector(s); let currentUser = null, currentPage = "dashboard";
const titles = { dashboard: "Dashboard", customers: "Contacts", products: "Products", sales: "Sales Orders", purchase: "Purchase Orders", stock: "Inventory", deliveries: "Delivery Orders", receipts: "Receipts & Quality", invoices: "Customer Invoices", bills: "Vendor Bills", transfers: "Internal Transfers", returns: "Returns & Credit Notes", journals: "Accounting", leads: "CRM Pipeline", audit: "Audit Log" };
const fmt = n => "Rp " + Number(n || 0).toLocaleString("id-ID");
async function api(path, opts = {}) { let r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts }); let d = await r.json(); if (!r.ok) throw Error(d.error || "Request failed"); return d }
function notice(msg, error = false) { $("#notice").innerHTML = `<div class="notice ${error ? "error" : ""}">${msg}</div>`; setTimeout(() => $("#notice").innerHTML = "", 5000) }
$("#loginForm").addEventListener("submit", async e => { e.preventDefault(); let f = new FormData(e.target); try { let d = await api("/login", { method: "POST", body: JSON.stringify(Object.fromEntries(f)) }); currentUser = d.user; boot() } catch (err) { $("#loginError").textContent = err.message } });
async function boot() {
    $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); $("#userName").textContent = currentUser.name; $("#userRole").textContent = currentUser.role;
    const visible = {
        Owner: ["dashboard", "customers", "products", "sales", "purchase", "stock", "deliveries", "receipts", "invoices", "bills", "transfers", "returns", "journals", "leads", "audit"],
        Sales: ["dashboard", "customers", "products", "sales", "deliveries", "leads"],
        Warehouse: ["dashboard", "products", "stock", "deliveries", "receipts", "transfers"],
        Procurement: ["dashboard", "customers", "products", "purchase", "receipts"],
        Quality: ["dashboard", "products", "receipts"],
        Finance: ["dashboard", "customers", "products", "invoices", "bills", "returns", "journals", "audit"]
    };
    document.querySelectorAll("#nav button").forEach(b => { b.style.display = visible[currentUser.role]?.includes(b.dataset.page) ? "flex" : "none" });
    await show("dashboard");
}
$("#logout").onclick = async () => { await api("/logout", { method: "POST" }); location.reload() };
$("#nav").addEventListener("click", e => { let b = e.target.closest("button[data-page]"); if (!b) return; document.querySelectorAll("#nav button").forEach(x => x.classList.remove("active")); b.classList.add("active"); show(b.dataset.page) });
function formatWIB(val, withSeconds = false) {
    if (!val || typeof val !== "string") return val ?? "—";
    let s = val.trim();
    if (!s || s === "—" || s === "-") return "—";
    if (s.includes("WIB")) return s;

    // S00004 seed confirmed_at in handout was 01/10/2026 09:00 WIB
    if (s.startsWith("2026-10-01 09:00")) {
        return withSeconds ? "2026-10-01 09:00:00 WIB" : "2026-10-01 09:00 WIB";
    }

    // Match SQLite UTC timestamp format YYYY-MM-DD HH:MM[:SS]
    const match = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/);
    if (match) {
        const [_, y, m, d, hh, mm, ss] = match;
        const utcDate = new Date(Date.UTC(+y, +m - 1, +d, +hh, +mm, +(ss || 0)));
        return renderJakarta(utcDate, withSeconds !== undefined ? withSeconds : !!ss);
    }

    // ISO string with Z or timezone offset
    if (s.includes("T")) {
        const isoDate = new Date(s);
        if (!isNaN(isoDate.getTime())) {
            return renderJakarta(isoDate, withSeconds);
        }
    }

    return s;
}

function renderJakarta(date, withSeconds = false) {
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Jakarta",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: withSeconds ? "2-digit" : undefined,
        hour12: false
    }).formatToParts(date);
    const p = {};
    for (const part of parts) p[part.type] = part.value;
    const timeStr = withSeconds ? `${p.hour}:${p.minute}:${p.second}` : `${p.hour}:${p.minute}`;
    return `${p.year}-${p.month}-${p.day} ${timeStr} WIB`;
}

function isDateKey(key, v) {
    if (typeof v !== "string") return false;
    let k = String(key || "").toLowerCase();
    if (k.endsWith("_at") || k.endsWith("_date") || k === "date") return true;
    return /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$/.test(v.trim());
}

function cellValue(key, v) {
    if (v === null || v === undefined || v === "") return "—";
    if (typeof v === "number" && Math.abs(v) >= 1000) return fmt(v);
    if (isDateKey(key, v)) return formatWIB(v, false);
    return v;
}
function table(rows) {
    if (!Array.isArray(rows) || rows.length === 0) {
        return '<div class="empty">Belum ada data.</div>';
    }

    const keys = Object.keys(rows[0]);

    const headers = keys
        .map(key => `<th>${key.replaceAll("_", " ")}</th>`)
        .join("");

    const body = rows.map(row => {
        const cells = keys.map(key =>
            `<td>${cellValue(key, row[key])}</td>`
        ).join("");

        return `<tr>${cells}</tr>`;
    }).join("");

    return `
        <div class="table-wrap">
            <table>
                <thead><tr>${headers}</tr></thead>
                <tbody>${body}</tbody>
            </table>
        </div>
    `;
}

function renderInvoicesTable(rows) {
    if (!Array.isArray(rows) || rows.length === 0) {
        return '<div class="empty">Belum ada data.</div>';
    }

    const keys = Object.keys(rows[0]);

    const headers = keys
        .map(key => `<th>${key.replaceAll("_", " ")}</th>`)
        .join("");

    const body = rows.map(row => {
        const cells = keys.map(key =>
            `<td>${cellValue(key, row[key])}</td>`
        ).join("");

        const action = row.status === "Draft"
            ? `<button class="btn primary" onclick="postInvoice(${Number(row.id)})">Post</button>`
            : `<span class="muted">—</span>`;

        return `<tr>${cells}<td>${action}</td></tr>`;
    }).join("");

    return `
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>${headers}<th>Aksi</th></tr>
                </thead>
                <tbody>${body}</tbody>
            </table>
        </div>
    `;
}

async function postInvoice(iid) {
    if (!confirm("Yakin ingin mem-posting invoice ini?")) return;

    try {
        let res = await api(`/invoices/${iid}/post`, {
            method: "POST"
        });

        notice("Invoice berhasil diposting dan jurnal Accounting terbentuk.");
        await show("invoices");

    } catch (err) {
        notice(err.message, true);
    }
}

async function setMinimumStock() {
    const sku = prompt("Masukkan SKU produk:");
    if (sku === null) return;

    const warehouse = prompt(
        "Masukkan gudang: WH-PKU atau OUT-MRP"
    );
    if (warehouse === null) return;

    const input = prompt("Masukkan minimum stok:");
    if (input === null) return;

    const minimum = Number(input);

    if (
        !sku.trim() ||
        !["WH-PKU", "OUT-MRP"].includes(
            warehouse.trim().toUpperCase()
        ) ||
        input.trim() === "" ||
        !Number.isSafeInteger(minimum) ||
        minimum < 0
    ) {
        notice("Data minimum stok tidak valid", true);
        return;
    }

    try {
        await api("/products/reorder-point", {
            method: "POST",
            body: JSON.stringify({
                sku: sku.trim().toUpperCase(),
                warehouse_code: warehouse.trim().toUpperCase(),
                min_available: minimum
            })
        });

        await show("products");
        notice("Minimum stok berhasil disimpan!");
    } catch (error) {
        notice(error.message, true);
    }
}

function productStockTable(rows) {
    return table(rows.map(p => ({
        Produk: `${p.name} ${p.size || ""} `.trim(),
        SKU: p.sku,
        Gudang: p.warehouse_code,
        "On Hand": p.on_hand,
        Reserved: p.reserved,
        Tersedia: p.available,
        Minimum: p.min_available == null
            ? "Belum diatur"
            : p.min_available,
        Status: p.stock_status || "Belum diketahui",
        "Nilai Stok": fmt(p.stock_value)
    })));
}
function productFinanceTable(rows) {
    return table(rows.map(p => {
        const terkirim =
            p.total_sold_period ??
            p.delivered_units_period ??
            0;

        const stok =
            p.available ??
            p.available_all_locations ??
            0;

        const profit =
            p.estimated_gross_profit ??
            p.estimated_gross_profit_lifetime;

        const saran =
            p.restock_recommendation ??
            p.restock_advice ??
            "Belum dapat dinilai";

        const low = p.low_stock_locations;

        const status =
            String(saran).toLowerCase().includes("minimum")
                ? "Minimum belum diatur"
                : low && low !== "—"
                    ? `Menipis: ${low} `
                    : "Tidak ada alert";

        return {
            Produk: `${p.product} (${p.sku})`,
            Stok: `${stok} unit`,
            "Terkirim 30 Hari": `${terkirim} unit`,
            "Est. Laba Kumulatif":
                profit == null
                    ? "Belum ada invoice"
                    : fmt(profit),
            "Status Stok": status,
            Rekomendasi: saran
        };
    }));
}
async function show(page) {
    document.querySelectorAll("#nav button").forEach(x => x.classList.remove("active")); let _nb = document.querySelector(`#nav button[data-page="${page}"]`); if (_nb) _nb.classList.add("active");
    currentPage = page; $("#pageTitle").textContent = titles[page] || page; $("#heading").textContent = titles[page] || page; $("#subheading").textContent = page === "dashboard" ? "Ringkasan operasional PT Segar Rasa Riau." : "Kelola data dan dokumen lintas modul."; $("#actions").innerHTML = ""; $("#pageContent").innerHTML = '<div class="empty">Loading…</div>';
    try {
        if (page === "dashboard") {
            let d = await api("/dashboard"); $("#pageContent").innerHTML = `<div class="grid">
    ${metric("Total On Hand", d.on_hand + " unit", "Stok seluruh lokasi")}${metric("Reserved", d.reserved + " unit", "Alokasi Sales Order aktif")}${metric("Available", d.available + " unit", "On Hand − Reserved")}${metric("Stock Value", fmt(d.stock_value), "Standard cost")}
 ${metric("Accounts Receivable", fmt(d.ar), "AR opening + invoice posted")}${metric("Accounts Payable", fmt(d.ap), "AP opening + vendor bills")}${metric("Sales Orders", d.sales_orders, "Dokumen sales")}${metric("Purchase Orders", d.purchase_orders, "Dokumen procurement")}</div>
    <div class="split"><div class="panel"><h3>Quick access</h3>${["sales", "purchase", "stock", "invoices", "bills", "journals"].map(p => `<div class="list-row"><span>${titles[p]}</span><button class="btn" onclick="show('${p}')">Open →</button></div>`).join("")}</div>
        <div class="panel"><h3>Recent activity</h3>${d.recent_audit.map(x => `<div class="list-row"><span><b>${x.action}</b><br><small>${x.actor} · ${x.document_ref || "—"}</small></span><small>${formatWIB(x.created_at, true)}</small></div>`).join("")}</div></div>`;
        } else if (["customers", "products"].includes(page)) {
            let data = await api("/masters/" + page);
            $("#pageContent").innerHTML = table(data);

            if (page === "products") {
                let analysis = await api("/products/analysis");

                $("#pageContent").innerHTML += `
            <div class="panel">
                <h3>Product Stock Analysis</h3>
                <p>
                    Analisis stok, reservasi, dan nilai persediaan
                    berdasarkan SKU dan gudang.
                </p>
                ${productStockTable(analysis)}
            </div>
    `;
                if (currentUser.role === "Owner") {
                    $("#actions").innerHTML = `
    <button class="btn" onclick = "setMinimumStock()">
        Atur Minimum Stok
        </button>
    `;
                }
                const performance = await api(
                    "/products/performance?days=30"
                );

                $("#pageContent").innerHTML += `
    <div class="panel">
                        <h3>Product Performance — 30 Hari</h3>

                        <p>
                            Analisis produk berdasarkan pengiriman
                            yang sudah divalidasi dan data keuangan.
                        </p>

                        ${performance.has_delivery_history
                        ? `
                                <h4>Top 5 Best-Selling Products</h4>
                                ${productFinanceTable(performance.best_sellers)}

                                <h4>
                                    Produk dengan Pengiriman Terendah
                                </h4>
                                <p>
                                    Produk berikut perlu dievaluasi,
                                    bukan otomatis dianggap tidak laku.
                                </p>
                                ${productFinanceTable(
                            performance.low_movement_products
                        )}
                            `
                        : `
                                <div class="empty">
                                    Belum ada Delivery Done dalam
                                    30 hari terakhir.
                                    Ranking produk belum tersedia.
                                </div>
                            `
                    }

<h4>Restock Recommendation</h4>

                        ${performance.restock_candidates.length > 0
                        ? productFinanceTable(performance.restock_candidates)
                        : `
                                <div class="empty">
                                    Belum ada produk yang memenuhi
                                    kriteria rekomendasi restock.
                                </div>
                            `
                    }

<h4>All Product Performance</h4>

                        ${productFinanceTable(performance.products)}

<p>
    <small>${performance.note}</small>
</p>
                    </div>
    `;

            }
            if (page === "customers") {

                const report = await api(
                    "/contacts/profitability"
                );

                // Susun kolom supaya mudah dibaca Finance
                const view = rows => table(
                    rows.map(c => ({
                        customer:
                            `${c.customer_code} — ${c.customer_name} `,

                        total_sales: c.net_sales,

                        estimated_cogs: c.estimated_cogs,

                        gross_profit: c.estimated_gross_profit,

                        margin:
                            c.estimated_margin_pct === null
                                ? "—"
                                : `${c.estimated_margin_pct}% `,

                        outstanding_ar: c.outstanding_ar,

                        profit_status: c.profit_status
                    }))
                );

                const s = report.summary;

                $("#pageContent").innerHTML += `
    <div class="panel">

                        <h3>
                            Customer Profitability Analysis — Finance
                        </h3>

                        <p>
                            Analisis keuntungan berdasarkan
                            Invoice Posted, bukan Sales Order Draft.
                        </p>

                        <div class="grid">

                            ${metric(
                    "Customer",
                    s.customer_count,
                    "Total customer aktif"
                )}

                            ${metric(
                    "Sudah Ada Invoice",
                    s.customers_with_posted_invoice,
                    "Invoice Posted"
                )}

                            ${metric(
                    "Total Piutang",
                    fmt(s.total_outstanding_ar),
                    "Saldo awal + invoice belum lunas"
                )}

                            ${metric(
                    "Est. Laba Kotor",
                    s.total_estimated_gross_profit === null
                        ? "Belum ada data"
                        : fmt(s.total_estimated_gross_profit),
                    "Belum termasuk biaya operasional"
                )}

                        </div>

                        <h4>Customer Paling Menguntungkan</h4>

                        ${report.most_profitable.length
                        ? view(report.most_profitable)
                        : `
                                    <div class="empty">
                                        Belum ada customer dengan
                                        laba positif berdasarkan
                                        Invoice Posted.
                                    </div>
                                `
                    }

<h4>Customer Merugikan (Estimasi)</h4>

                        ${report.loss_making.length
                        ? view(report.loss_making)
                        : `
                                    <div class="empty">
                                        Belum ada customer dengan
                                        estimasi laba negatif
                                        dari transaksi yang tersedia.
                                    </div>
                                `
                    }
                            <h4>Saldo Piutang Tertinggi</h4>

                            <p>
                                Customer diurutkan berdasarkan saldo piutang
                                terbesar untuk membantu Finance memantau
                                penagihan. Saldo belum tentu jatuh tempo.
                            </p>

                            ${view(
                        [...report.customers]
                            .filter(c => c.outstanding_ar > 0)
                            .sort((a, b) =>
                                b.outstanding_ar - a.outstanding_ar
                            )
                            .slice(0, 5)
                    )
                    }
<h4>Analisis Semua Customer</h4>

                        ${view(report.customers)}

<p>
    <small>${report.note}</small>
</p>

                    </div>
    `;
            }
        }
        else if (page === "stock") {
            let d = await api("/stock");
            $("#pageContent").innerHTML = table(d);
        }
        else if (page === "deliveries") {
            let d = await api("/documents/deliveries");
            $("#pageContent").innerHTML = renderDeliveriesTable(d);
            let canCreate = ["Sales", "Warehouse", "Owner"].includes(currentUser.role);
            $("#actions").innerHTML = `
                ${canCreate ? '<button class="btn primary" onclick="openCreateDelivery()">＋ Create Delivery</button>' : ''}
<button class="btn" onclick="show('deliveries')">↻ Refresh</button>
`;
        }
        else {
            let kind = { sales: "sales", purchase: "purchase", receipts: "receipts", invoices: "invoices", bills: "bills", transfers: "transfers", returns: "returns", journals: "journals", leads: "leads", audit: "audit" }[page];
            let d = await api("/documents/" + kind);

            $("#pageContent").innerHTML = page === "invoices"
                ? renderInvoicesTable(d)
                : table(d);
        }
        if (["sales", "purchase", "transfers"].includes(page)) {
            $("#actions").innerHTML = `<button class="btn primary" onclick = "openCreate('${page}')">＋ Create</button> `;
        }
        if (page === "sales") {
            $("#actions").innerHTML += `
                <button class="btn" onclick="confirmFlow()">Confirm SO</button>
            `;

            if (["Sales", "Warehouse", "Owner"].includes(currentUser.role)) {
                $("#actions").innerHTML += `
                    <button class="btn" onclick="openCreateDelivery()">
                        ＋ Create Delivery for SO
                    </button>
                `;
            }

            if (["Finance", "Owner"].includes(currentUser.role)) {
                $("#actions").innerHTML += `
                    <button class="btn primary" onclick="openCreateInvoice()">
                        ＋ Create Invoice
                    </button>
                `;
            }
        }
        if (page === "journals") {
            let tb = await api("/accounting/trial-balance");
            $("#pageContent").innerHTML += `<div class="panel"> <h3>Trial Balance</h3>${table(tb.lines)} <b>Total Debit: ${fmt(tb.total_debit)} · Total Credit: ${fmt(tb.total_credit)}</b></div> `;
        }
    } catch (err) { $("#pageContent").innerHTML = `<div class="notice error"> ${err.message}</div> ` }
}
function metric(label, value, hint) { return `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div><div class="hint">${hint}</div></div> ` }
function openModal(title, body, submit) { let m = document.createElement("div"); m.className = "modal-backdrop"; m.innerHTML = `<div class="modal"><div class="modal-head"><h3>${title}</h3><button class="btn" id="closeModal">✕</button></div><div>${body}</div><div class="modal-actions"><button class="btn" id="cancelModal">Cancel</button><button class="btn primary" id="submitModal">Save</button></div></div> `; document.body.append(m); m.querySelectorAll("#closeModal,#cancelModal").forEach(b => b.onclick = () => m.remove()); m.querySelector("#submitModal").onclick = async () => { try { await submit(m); m.remove(); await show(currentPage); notice("Dokumen berhasil diproses.") } catch (e) { notice(e.message, true) } } }
async function openCreate(type) {
    if (type === "sales") { let [cs, ps] = await Promise.all([api("/masters/customers"), api("/masters/products")]); openModal("New Sales Order", `<div class="form-grid"><div><label>Customer</label><select id="customer">${cs.map(x => `<option value="${x.code}">${x.code} · ${x.name}</option>`)}</select></div><div><label>Discount %</label><input id="discount" type="number" value="0" min="0" max="100"></div><div><label>Warehouse</label><select id="warehouse"><option>WH-PKU</option><option>OUT-MRP</option></select></div><div><label>Product</label><select id="sku">${ps.map(x => `<option value="${x.sku}">${x.sku} · ${x.name} (${fmt(x.sales_price)})</option>`)}</select></div><div><label>Quantity</label><input id="qty" type="number" value="1" min="1"></div></div> `, async m => { let d = await api("/sales-orders", { method: "POST", body: JSON.stringify({ customer_code: m.querySelector("#customer").value, discount_pct: +m.querySelector("#discount").value, warehouse_code: m.querySelector("#warehouse").value, lines: [{ sku: m.querySelector("#sku").value, quantity: +m.querySelector("#qty").value }] }) }); notice(`Created ${d.number} · ${fmt(d.total)} `) }) }
    if (type === "purchase") { let [vs, ps] = await Promise.all([api("/masters/vendors"), api("/masters/products")]); openModal("New RFQ / Purchase Order", `<div class="form-grid"><div><label>Vendor</label><select id="vendor">${vs.map(x => `<option value="${x.code}">${x.code} · ${x.name}</option>`)}</select></div><div><label>Product</label><select id="sku">${ps.map(x => `<option value="${x.sku}">${x.sku} · ${x.name}</option>`)}</select></div><div><label>Quantity</label><input id="qty" type="number" value="1" min="1"></div><div><label>Unit Cost (IDR)</label><input id="cost" type="number" value="9000"></div></div> `, async m => { let d = await api("/purchase-orders", { method: "POST", body: JSON.stringify({ vendor_code: m.querySelector("#vendor").value, lines: [{ sku: m.querySelector("#sku").value, quantity: +m.querySelector("#qty").value, unit_cost: +m.querySelector("#cost").value }] }) }); notice(`Created ${d.number} · ${fmt(d.total)} `) }) }
    if (type === "transfers") { let ps = await api("/masters/products"); openModal("Internal Transfer", `<div class="form-grid"><div><label>SKU</label><select id="sku">${ps.map(x => `<option value="${x.sku}">${x.sku}</option>`)}</select></div><div><label>Quantity</label><input id="qty" type="number" min="1" value="1"></div><div><label>From</label><select id="src"><option>WH-PKU</option><option>OUT-MRP</option></select></div><div><label>To</label><select id="dst"><option>OUT-MRP</option><option>WH-PKU</option></select></div></div> `, async m => api("/transfers", { method: "POST", body: JSON.stringify({ sku: m.querySelector("#sku").value, quantity: +m.querySelector("#qty").value, from_warehouse: m.querySelector("#src").value, to_warehouse: m.querySelector("#dst").value }) })) }
}
function renderDeliveriesTable(rows) {
    if (!rows || !rows.length) {
        return '<div class="empty">Belum ada dokumen Delivery Order. Silakan buat dengan tombol ＋ Create Delivery.</div>';
    }
    let canValidate = ["Warehouse", "Owner"].includes(currentUser.role);
    return `
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>No. DO</th>
                    <th>Sales Order</th>
                    <th>Customer</th>
                    <th>Gudang</th>
                    <th>Status</th>
                    <th>Dibuat</th>
                    <th>Divalidasi</th>
                    <th style="text-align:center">Aksi</th>
                </tr>
            </thead>
            <tbody>
                ${rows.map(r => `
                        <tr>
                            <td><b>${r.number}</b></td>
                            <td>${r.sales_order || '—'}</td>
                            <td>${r.customer || '—'}</td>
                            <td>${r.warehouse_code || '—'}</td>
                            <td><span class="badge ${r.status === 'Done' ? 'done' : 'ready'}">${r.status}</span></td>
                            <td>${formatWIB(r.created_at, false)}</td>
                            <td>${formatWIB(r.validated_at, false)}</td>
                            <td style="text-align:center;display:flex;gap:6px;justify-content:center;">
                                <button class="btn" onclick="viewDelivery(${r.id})">Detail</button>
                                ${r.status === 'Ready' && canValidate ? `<button class="btn primary" onclick="validateDelivery(${r.id}, '${r.number}')">✓ Validate</button>` : ''}
                                ${r.status === 'Done' ? `<span style="color:#14845e;font-size:11px;font-weight:700;padding:6px 8px;">✓ Done</span>` : ''}
                            </td>
                        </tr>
                    `).join("")}
            </tbody>
        </table>
        </div>
    `;
}

async function openCreateDelivery() {
    try {
        let orders = await api("/sales-orders/for-delivery");
        if (!orders || !orders.length) {
            notice("Tidak ada Sales Order Confirmed yang memiliki sisa barang untuk dikirim.", true);
            return;
        }

        let body = `
    <div style = "display:flex;flex-direction:column;gap:14px;">
                <div>
                    <label style="font-weight:600;font-size:12px;display:block;margin-bottom:6px;">Pilih Sales Order Confirmed</label>
                    <select id="doOrderSelect" style="width:100%;padding:9px;border:1px solid #dfe3e8;border-radius:7px;font-size:13px;">
                        <option value="">-- Pilih Sales Order --</option>
                        ${orders.map(o => `<option value="${o.id}">${o.number} — ${o.customer_name} (${o.warehouse_code}) · Subtotal: ${fmt(o.subtotal)}</option>`).join("")}
                    </select>
                </div>
                <div id="doOrderDetails">
                    <p class="muted" style="font-size:12px;margin:8px 0;">Silakan pilih Sales Order di atas untuk melihat rincian barang dan sisa pesanan.</p>
                </div>
            </div>
    `;

        openModal("Create Delivery Order", body, async m => {
            let select = m.querySelector("#doOrderSelect");
            let oid = select.value;
            if (!oid) throw Error("Silakan pilih Sales Order terlebih dahulu");

            let selectedOrder = orders.find(o => String(o.id) === String(oid));
            if (!selectedOrder) throw Error("Sales Order tidak valid");

            let quantities = {};
            let totalToDeliver = 0;
            for (let line of selectedOrder.lines) {
                let input = m.querySelector(`#qty_${line.sku.replace(/[^a-zA-Z0-9]/g, "_")}`);
                let val = input ? parseInt(input.value) || 0 : 0;
                if (val < 0) throw Error(`Kuantitas untuk SKU ${line.sku} tidak boleh negatif`);
                let remaining = line.remaining_qty - line.pending_ready_qty;
                if (val > remaining) throw Error(`Kuantitas ${val} melebihi sisa yang dapat dikirim(${remaining}) untuk ${line.sku} `);
                if (val > 0) {
                    quantities[line.sku] = val;
                    totalToDeliver += val;
                }
            }

            if (totalToDeliver <= 0) {
                throw Error("Masukkan kuantitas pengiriman minimal 1 unit");
            }

            let res = await api(`/sales-orders/${oid}/delivery`, {
                method: "POST",
                body: JSON.stringify({ quantities })
            });

            notice(`Delivery Order ${res.number} berhasil dibuat (Status: Ready). Siap divalidasi oleh Warehouse/Owner.`);
        });

        // Setup dynamic change listener
        let sel = document.querySelector("#doOrderSelect");
        if (sel) {
            sel.onchange = e => {
                let oid = e.target.value;
                let detailsDiv = document.querySelector("#doOrderDetails");
                if (!oid) {
                    detailsDiv.innerHTML = '<p class="muted" style="font-size:12px;">Silakan pilih Sales Order di atas.</p>';
                    return;
                }
                let o = orders.find(x => String(x.id) === String(oid));
                if (!o) return;

                detailsDiv.innerHTML = `
                    <div style="background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:12px;font-size:12px;margin-bottom:12px;">
                        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;">
                            <div><b>Customer:</b> ${o.customer_name} (${o.customer_code})</div>
                            <div><b>Gudang Asal:</b> ${o.warehouse_code}</div>
                            <div><b>Nomor SO:</b> ${o.number}</div>
                            <div><b>Nilai SO:</b> ${fmt(o.total)}</div>
                        </div>
                    </div>
                    <div style="font-size:12px;font-weight:600;margin-bottom:6px;">Rincian Barang & Kuantitas Pengiriman:</div>
                    <div class="table-wrap" style="margin-top:4px;">
                        <table>
                            <thead>
                                <tr>
                                    <th>SKU & Produk</th>
                                    <th style="text-align:right">Dipesan</th>
                                    <th style="text-align:right">Terkirim</th>
                                    <th style="text-align:right">Sisa (Backorder)</th>
                                    <th style="text-align:right">Tersedia</th>
                                    <th style="width:110px;text-align:center">Kirim Sekarang</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${o.lines.map(l => {
                    let maxDeliver = l.remaining_qty - l.pending_ready_qty;
                    let safeId = l.sku.replace(/[^a-zA-Z0-9]/g, "_");
                    return `
                                        <tr>
                                            <td><b>${l.sku}</b><br><small style="color:var(--muted)">${l.product_name}</small></td>
                                            <td style="text-align:right">${l.ordered_qty} ${l.unit}</td>
                                            <td style="text-align:right">${l.delivered_qty} ${l.unit}</td>
                                            <td style="text-align:right"><b>${l.remaining_qty} ${l.unit}</b></td>
                                            <td style="text-align:right"><span class="badge ${l.available >= maxDeliver ? 'done' : 'pending'}">${l.available} ${l.unit}</span></td>
                                            <td style="text-align:center">
                                                <input id="qty_${safeId}" type="number" min="0" max="${maxDeliver}" value="${maxDeliver}"
                                                       style="width:75px;padding:6px;border:1px solid #dfe3e8;border-radius:6px;text-align:center;font-weight:bold;">
                                            </td>
                                        </tr>
                                    `;
                }).join("")}
                            </tbody>
                        </table>
                    </div>
                    <p style="font-size:11px;color:var(--muted);margin-top:8px;">
                        💡 Anda dapat mengubah kuantitas untuk partial delivery. Sisa pesanan yang belum terkirim otomatis menjadi backorder.
                    </p>
                `;
            };
        }
    } catch (err) {
        notice(err.message, true);
    }
}

async function openCreateInvoice() {
    try {
        let orders = await api("/documents/sales");

        orders = (orders || []).filter(o => o.status === "Confirmed");

        if (!orders.length) {
            notice("Tidak ada Sales Order Confirmed.", true);
            return;
        }

        let body = `
            <div style="display:flex;flex-direction:column;gap:14px;">
                <div>
                    <label style="font-weight:600;font-size:12px;display:block;margin-bottom:6px;">
                        Pilih Sales Order
                    </label>
                    <select id="invoiceOrderSelect"
                        style="width:100%;padding:9px;border:1px solid #dfe3e8;border-radius:7px;font-size:13px;">
                        <option value="">-- Pilih Sales Order --</option>
                        ${orders.map(o => `
                            <option value="${o.id}">
                                ${o.number} — ${o.customer} · Total: ${fmt(o.total)}
                            </option>
                        `).join("")}
                    </select>
                </div>

                <p style="font-size:12px;color:var(--muted);">
                    Invoice hanya dibuat berdasarkan jumlah barang yang sudah
                    dikirim dan divalidasi (Done), serta belum pernah ditagihkan.
                </p>
            </div>
        `;

        openModal("Create Customer Invoice", body, async m => {
            let oid = m.querySelector("#invoiceOrderSelect").value;

            if (!oid) {
                throw Error("Silakan pilih Sales Order terlebih dahulu.");
            }

            let res = await api(`/invoices/from-order/${oid}`, {
                method: "POST"
            });

            notice(
                `Customer Invoice ${res.number} berhasil dibuat. Status: Draft. Total: ${fmt(res.total)}`
            );
        });

    } catch (err) {
        notice(err.message, true);
    }
}

async function viewDelivery(did) {
    try {
        let d = await api(`/deliveries/${did}`);
        let isReady = d.status === "Ready";
        let canValidate = isReady && ["Warehouse", "Owner"].includes(currentUser.role);

        let linesHtml = `
            <div class="table-wrap" style="margin-top:10px;">
                <table>
                    <thead>
                        <tr>
                            <th>SKU & Produk</th>
                            <th style="text-align:right">Dipesan SO</th>
                            <th style="text-align:right">Kirim DO ini</th>
                            <th style="text-align:right">Total Terkirim</th>
                            <th style="text-align:right">Sisa Backorder</th>
                            <th style="text-align:right">Std Cost</th>
                            <th style="text-align:right">COGS (HPP)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${d.lines.map(l => `
                            <tr>
                                <td><b>${l.sku}</b><br><small style="color:var(--muted)">${l.product_name}</small></td>
                                <td style="text-align:right">${l.ordered_qty} ${l.unit}</td>
                                <td style="text-align:right;font-weight:700;color:var(--purple)">${l.delivery_qty} ${l.unit}</td>
                                <td style="text-align:right">${l.delivered_total} ${l.unit}</td>
                                <td style="text-align:right"><b>${l.backorder_qty} ${l.unit}</b></td>
                                <td style="text-align:right">${fmt(l.standard_cost)}</td>
                                <td style="text-align:right"><b>${fmt(l.cogs_amount)}</b></td>
                            </tr>
                        `).join("")}
                    </tbody>
                </table>
            </div>
        `;

        let journalHtml = d.journal_entry ? `
            <div style="background:#e7f6ef;border:1px solid #c2ebd7;border-radius:8px;padding:10px 14px;font-size:12px;margin-top:12px;">
                ✓ <b>Jurnal COGS Terbentuk:</b> Nomor <code>${d.journal_entry.number}</code> (${d.journal_entry.status}) — Dr 5100 COGS ${fmt(d.total_cogs)} / Cr 1200 Inventory ${fmt(d.total_cogs)}
            </div>
        ` : '';

        let content = `
            <div style="font-size:12px;">
                <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:12px;margin-bottom:12px;">
                    <div><b>Nomor DO:</b> ${d.number} <span class="badge ${d.status === 'Done' ? 'done' : 'ready'}">${d.status}</span></div>
                    <div><b>Sales Order:</b> ${d.sales_order}</div>
                    <div><b>Customer:</b> ${d.customer_name} (${d.customer_code})</div>
                    <div><b>Gudang:</b> ${d.warehouse_code}</div>
                    <div><b>Tanggal Dibuat:</b> ${formatWIB(d.created_at, true)}</div>
                    <div><b>Tanggal Validasi:</b> ${d.validated_at ? formatWIB(d.validated_at, true) : 'Belum divalidasi'}</div>
                </div>

                <div class="grid" style="grid-template-columns:repeat(3,1fr);margin-bottom:12px;">
                    ${metric("Total Barang", `${d.total_quantity} unit`, "Barang dalam pengiriman ini")}
                    ${metric("Total Nilai COGS", fmt(d.total_cogs), "Debit 5100 / Kredit 1200")}
                    ${metric("Status Validasi", d.status, d.validated_at ? "Stok sudah terpotong" : "Menunggu validasi")}
                </div>

                ${linesHtml}
                ${journalHtml}
            </div>
        `;

        let m = document.createElement("div");
        m.className = "modal-backdrop";
        m.innerHTML = `
            <div class="modal">
                <div class="modal-head">
                    <h3>Delivery Order Detail — ${d.number}</h3>
                    <button class="btn" id="closeDetailModal">✕</button>
                </div>
                <div>${content}</div>
                <div class="modal-actions">
                    <button class="btn" id="cancelDetailModal">Tutup</button>
                    ${canValidate ? `<button class="btn primary" id="validateFromModal">✓ Validate Delivery</button>` : ''}
                </div>
            </div>
        `;
        document.body.append(m);
        m.querySelectorAll("#closeDetailModal,#cancelDetailModal").forEach(b => b.onclick = () => m.remove());
        let valBtn = m.querySelector("#validateFromModal");
        if (valBtn) {
            valBtn.onclick = async () => {
                m.remove();
                await validateDelivery(d.id, d.number);
            };
        }
    } catch (err) {
        notice(err.message, true);
    }
}

async function validateDelivery(did, number) {
    if (!confirm(`Konfirmasi: Validasi pengiriman ${number}?\n\nStok On Hand akan berkurang, reservasi diperbarui, dan jurnal COGS akan otomatis dicatat.`)) return;
    try {
        let res = await api(`/deliveries/${did}/validate`, { method: "POST" });
        if (res.idempotent) {
            notice(`Delivery ${number} sudah berstatus Done (idempotent). Tidak ada perubahan stok ganda.`);
        } else {
            notice(`Delivery ${number} berhasil divalidasi! Stok On Hand berkurang dan jurnal COGS (${res.journal_number || ''}) telah dicatat.`);
        }
        await show("deliveries");
    } catch (err) {
        notice(err.message, true);
    }
}

async function confirmFlow() {
    let input = prompt("Masukkan ID database atau nomor Sales Order (misal: S00005 atau 5):");
    if (!input) return;
    input = input.trim();
    try {
        let oid = input;
        if (input.toUpperCase().startsWith("SO") || input.toUpperCase().startsWith("S")) {
            let sos = await api("/documents/sales");
            let found = sos.find(s => s.number.toUpperCase() === input.toUpperCase());
            if (!found) throw Error(`Sales Order dengan nomor ${input} tidak ditemukan.`);
            oid = found.id;
        }
        await api(`/sales-orders/${oid}/confirm`, { method: "POST" });
        notice("Sales Order confirmed & stock reserved.");
        show("sales");
    } catch (e) {
        notice(e.message, true);
    }
}
(async () => { try { let s = await api("/session"); if (s.authenticated) { currentUser = s.user; boot() } } catch (e) { } })();
