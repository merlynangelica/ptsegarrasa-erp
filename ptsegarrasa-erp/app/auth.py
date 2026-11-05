"""RBAC SegarRasa ERP (SR-16, F-01).

Otorisasi ditegakkan di API. Menu yang disembunyikan di UI BUKAN batas keamanan.
Akses yang ditolak dicatat ke audit log (bukti T-02).
"""
from functools import wraps
from flask import session, jsonify, request

PERMISSIONS = {
    "Owner": {"*"},
    "Sales": {
        "dashboard.view", "contacts.view", "products.view", "inventory.view",
        "crm.view", "crm.create", "crm.edit",
        "sales.view", "sales.create", "sales.edit", "sales.confirm",
        "delivery.view", "delivery.create", "invoicing.view",
    },
    "Warehouse": {
        "dashboard.view", "contacts.view", "products.view", "inventory.view",
        "delivery.view", "delivery.create", "delivery.validate",
        "transfer.view", "transfer.create", "transfer.validate",
        "receipt.view", "receipt.create", "receipt.validate", "quality.view",
        "returns.view", "returns.create", "purchase.view", "sales.view", "reports.view",
    },
    "Procurement": {
        "dashboard.view", "contacts.view", "products.view", "inventory.view",
        "purchase.view", "purchase.create", "purchase.confirm",
        "receipt.view", "quality.view", "bills.view",
    },
    "Quality": {
        "dashboard.view", "products.view", "purchase.view",
        "receipt.view", "quality.view", "quality.check",
    },
    "Finance": {
        "dashboard.view", "contacts.view", "products.view", "inventory.view",
        "sales.view", "purchase.view", "receipt.view", "delivery.view",
        "invoicing.view", "invoicing.post", "bills.view", "bills.post",
        "payments.view", "payments.create", "accounting.view", "accounting.post",
        "reports.view", "audit.view", "returns.view", "returns.create", "returns.validate",
    },
}

# App switcher (S-01): 7 aplikasi utama + izin minimal untuk melihatnya.
APPS = [
    {"key": "sales", "name": "Sales", "perm": "sales.view"},
    {"key": "crm", "name": "CRM", "perm": "crm.view"},
    {"key": "purchase", "name": "Purchase", "perm": "purchase.view"},
    {"key": "inventory", "name": "Inventory", "perm": "inventory.view"},
    {"key": "accounting", "name": "Accounting", "perm": "accounting.view"},
    {"key": "invoicing", "name": "Invoicing", "perm": "invoicing.view"},
    {"key": "quality", "name": "Quality", "perm": "quality.view"},
]


def role():
    return session.get("user", {}).get("role")


def allowed(permission):
    r = role()
    return r == "Owner" or permission in PERMISSIONS.get(r, set())


def permissions_for(r):
    return sorted(PERMISSIONS.get(r, set()))


def deny(permission):
    from .db import audit, get_db
    try:
        audit(session.get("user", {}).get("username", "anonymous"), "Access denied",
              request.path, f"role={role()} required={permission}")
        get_db().commit()
    except Exception:
        pass
    return jsonify({"error": "Akses ditolak untuk role ini", "required_permission": permission}), 403


def require(permission):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return jsonify({"error": "Authentication required"}), 401
            if not allowed(permission):
                return deny(permission)
            return fn(*args, **kwargs)
        return wrapped
    return deco
