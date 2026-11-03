from functools import wraps
from flask import session, jsonify

PERMISSIONS = {
 "Owner": {"*"},
 "Sales": {"dashboard.view","crm.view","crm.create","sales.view","sales.create","sales.confirm","contacts.view","products.view","delivery.view"},
 "Warehouse": {"dashboard.view","contacts.view","products.view","inventory.view","delivery.view","delivery.validate","transfer.view","transfer.create","transfer.validate","receipt.view"},
 "Procurement": {"dashboard.view","contacts.view","products.view","purchase.view","purchase.create","purchase.confirm","receipt.view"},
 "Quality": {"dashboard.view","contacts.view","products.view","receipt.view","quality.view","quality.check"},
 "Finance": {"dashboard.view","contacts.view","products.view","invoicing.view","invoicing.post","bills.view","bills.post","payments.view","payments.create","accounting.view","accounting.post","reports.view","returns.view","returns.validate"},
}
def allowed(permission):
    role = session.get("user", {}).get("role")
    return role == "Owner" or permission in PERMISSIONS.get(role, set())

def require(permission):
    def deco(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return jsonify({"error":"Authentication required"}), 401
            if not allowed(permission):
                return jsonify({"error":"Akses ditolak untuk role ini","required_permission":permission}), 403
            return fn(*args, **kwargs)
        return wrapped
    return deco
