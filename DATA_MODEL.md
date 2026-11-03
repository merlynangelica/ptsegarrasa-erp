# Data model

Core entities: users, customers, vendors, products, warehouses, stock, reservations, leads, sales_orders/lines, deliveries/lines, purchase_orders/lines, receipts/lines, invoices/lines, vendor_bills, payments, transfers, returns, journal_entries/lines, audit_logs, sequences, settings.

Foreign keys are enabled. Money is stored as integer IDR. SKU variants are separate product records. `stock` is keyed by SKU + warehouse. Reservation rows are linked to the originating document number. Accounting uses chart-of-account codes from the handout:
1010 Bank, 1020 Cash on Hand, 1100 AR, 1200 Stock Valuation, 1210 GRNI, 1300 VAT Input, 2100 AP, 2200 VAT Output, 3100 Opening Equity, 4100 Sales Revenue, 4110 Sales Discounts, 5100 COGS, 6100 Quality Loss, 6200 Shipping Expense, 6300 Bank Charges.
