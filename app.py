from flask import Flask, redirect, request, session, render_template, send_file, jsonify
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
import io, urllib.parse, os
from datetime import datetime

app = Flask(__name__)

# ---------------- CONFIG ----------------
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "fallback-secret")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------------- MODELS ----------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(200))
    role = db.Column(db.String(20), default="admin")

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))
    address = db.Column(db.String(200))

# ---------------- INVENTORY ----------------

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)
    cost_price = db.Column(db.Float, default=0)
    stock = db.Column(db.Integer, default=0)
    created_at = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

# ---------------- INVOICE ITEMS ----------------

class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))

    product = db.relationship('Product')

    quantity = db.Column(db.Integer)
    price = db.Column(db.Float)

    def subtotal(self):
        return self.quantity * self.price

    def profit(self):
        return (self.price - (self.product.cost_price or 0)) * self.quantity

# ---------------- COMPANY ----------------

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    gst_number = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))

# ---------------- INVOICE ----------------

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    customer = db.relationship('Customer')

    amount = db.Column(db.Float, default=0)
    gst_percent = db.Column(db.Float, default=18)
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))
    status = db.Column(db.String(50), default="pending")

    items = db.relationship(
        'InvoiceItem',
        backref='invoice',
        lazy=True,
        cascade="all, delete-orphan"
    )

    def subtotal(self):
        return sum([i.subtotal() for i in self.items])

    def total(self):
        subtotal = self.subtotal()
        return round(subtotal + (subtotal * self.gst_percent / 100), 2)

    def profit(self):
        return sum([i.profit() for i in self.items])

# ---------------- PAYMENT ----------------

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    amount = db.Column(db.Float)
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

# ---------------- TRANSACTION ----------------

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float)
    type = db.Column(db.String(50))  # income / expense
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

# ---------------- HELPERS ----------------

def update_invoice_amount(invoice):
    invoice.amount = sum([i.subtotal() for i in invoice.items])

# ---------------- AUTH ----------------

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(username=request.form["username"]).first()

        if user and check_password_hash(user.password, request.form["password"]):
            session["admin"] = True
            session["user_id"] = user.id
            return redirect("/admin")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ---------------- ADMIN ----------------

class MyAdminHome(AdminIndexView):
    @expose("/")
    def index(self):
        if not session.get("admin"):
            return redirect("/")

        income = db.session.query(func.sum(Transaction.amount)).filter_by(type="income").scalar() or 0
        expense = db.session.query(func.sum(Transaction.amount)).filter_by(type="expense").scalar() or 0

        total_profit = sum([i.profit() for i in InvoiceItem.query.all()])
        low_stock = Product.query.filter(Product.stock < 5).all()

        return self.render(
            "admin_home.html",
            income=income,
            expense=expense,
            profit=income - expense,
            total_invoices=Invoice.query.count(),
            paid_invoices=Invoice.query.filter_by(status="paid").count(),
            customers=Customer.query.count(),
            invoices=Invoice.query.order_by(Invoice.id.desc()).limit(10),
            product_profit=total_profit,
            low_stock=low_stock
        )

class SecureModelView(ModelView):
    def is_accessible(self):
        return session.get("admin")

    def inaccessible_callback(self, name, **kwargs):
        return redirect("/")

admin = Admin(app, name="Mobi Invoice", index_view=MyAdminHome())
admin.add_view(SecureModelView(User, db.session))
admin.add_view(SecureModelView(Customer, db.session))
admin.add_view(SecureModelView(Product, db.session))
admin.add_view(SecureModelView(Invoice, db.session))
admin.add_view(SecureModelView(InvoiceItem, db.session))
admin.add_view(SecureModelView(Payment, db.session))
admin.add_view(SecureModelView(Transaction, db.session))

# ---------------- BILLING ----------------

@app.route("/invoice/create", methods=["GET", "POST"])
def create_invoice():
    if not session.get("admin"):
        return redirect("/")

    customers = Customer.query.all()
    products = Product.query.all()

    if request.method == "POST":
        invoice = Invoice(customer_id=request.form.get("customer_id"))
        db.session.add(invoice)
        db.session.commit()

        return redirect(f"/invoice/edit/{invoice.id}")

    return render_template("create_invoice.html", customers=customers, products=products)

@app.route("/invoice/edit/<int:id>")
def edit_invoice(id):
    if not session.get("admin"):
        return redirect("/")

    invoice = db.session.get(Invoice, id)
    products = Product.query.all()

    return render_template("create_invoice.html", invoice=invoice, products=products)

# ---------------- ITEM ACTIONS ----------------

@app.route("/invoice/add_item/<int:invoice_id>", methods=["POST"])
def add_item(invoice_id):
    invoice = db.session.get(Invoice, invoice_id)

    if invoice.status == "paid":
        return "Invoice locked"

    product = db.session.get(Product, request.form.get("product_id"))
    qty = int(request.form.get("quantity"))

    if product.stock < qty:
        return "Stock not available"

    item = InvoiceItem(
        invoice_id=invoice_id,
        product_id=product.id,
        quantity=qty,
        price=product.price
    )

    product.stock -= qty
    db.session.add(item)

    update_invoice_amount(invoice)

    db.session.commit()
    return redirect(f"/invoice/edit/{invoice_id}")

@app.route("/invoice/remove_item/<int:item_id>")
def remove_item(item_id):
    item = db.session.get(InvoiceItem, item_id)
    invoice = item.invoice

    if invoice.status == "paid":
        return "Invoice locked"

    item.product.stock += item.quantity
    db.session.delete(item)

    update_invoice_amount(invoice)

    db.session.commit()
    return redirect(f"/invoice/edit/{invoice.id}")

@app.route("/invoice/update_item/<int:item_id>", methods=["POST"])
def update_item(item_id):
    item = db.session.get(InvoiceItem, item_id)
    invoice = item.invoice

    if invoice.status == "paid":
        return "Invoice locked"

    new_qty = int(request.form.get("quantity"))
    diff = new_qty - item.quantity

    if diff > 0:
        if item.product.stock < diff:
            return "Not enough stock"
        item.product.stock -= diff

    elif diff < 0:
        item.product.stock += abs(diff)

    item.quantity = new_qty

    update_invoice_amount(invoice)

    db.session.commit()
    return redirect(f"/invoice/edit/{invoice.id}")

# ---------------- FINALIZE ----------------

@app.route("/invoice/finalize/<int:id>")
def finalize_invoice(id):
    invoice = db.session.get(Invoice, id)

    invoice.status = "paid"

    db.session.add(Transaction(
        amount=invoice.total(),
        type="income"
    ))

    db.session.commit()

    return redirect("/invoices")

# ---------------- STOCK ALERT ----------------

@app.route("/stock-alert")
def stock_alert():
    products = Product.query.filter(Product.stock < 5).all()
    return jsonify([{"name": p.name, "stock": p.stock} for p in products])

# ---------------- INIT ----------------

with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(
            username="admin",
            password=generate_password_hash("admin")
        ))
        db.session.commit()

# ---------------- VERCEL ----------------
app = app
