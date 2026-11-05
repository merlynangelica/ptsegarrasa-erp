# Data model

Core entities: users, customers, vendors, vendor_pricelist, products, warehouses, stock, reservations, stock_moves, leads, lead_stage_history, sales_orders/lines, deliveries/lines, purchase_orders/lines, receipts/lines (termasuk hasil QC), invoices/lines, vendor_bills/vendor_bill_lines (three-way match), payments, transfers, returns (credit note), accounts, journal_entries/lines, audit_logs, sequences, settings, reorder_points.

Foreign keys aktif. Uang disimpan sebagai integer IDR. Variant SKU = record produk terpisah. `stock` per SKU + lokasi. Reservasi terhubung ke nomor dokumen asal. Timestamp disimpan UTC (CURRENT_TIMESTAMP) dan ditampilkan WIB di UI.

Penomoran: S00005…, PO00004…, CRM00010…, WH/OUT/0001, WH/IN/0001, WH/INT/0001, WH/RET/0001, QC00001, INV/2026/0001, BILL/2026/0001, RINV/2026/0001, PAY/2026/0001, MISC00002….

Chart of accounts: 1010 Bank, 1020 Cash on Hand, 1100 AR, 1200 Stock Valuation, 1210 GRNI, 1300 VAT Input, 2100 AP, 2200 VAT Output, 3100 Opening Equity, 4100 Sales Revenue, 4110 Sales Discounts, 5100 COGS, 6100 Quality Loss, 6200 Shipping Expense, 6300 Bank Charges.

Database lama otomatis dimigrasi (kolom baru ditambahkan), tetapi untuk baseline yang bersih hapus `instance/segarrasa_erp.db` atau gunakan Reset baseline seed.
