from flask import Flask, redirect, request, session, render_template, send_file, jsonify
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
import io, urllib.parse, os
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)

# ---------------- CONFIG ----------------
app.config['SECRET_KEY'] = os.environ.get("SECRET_KEY", "fallback-secret")

# Determine writable instance folder
if "VERCEL" in os.environ:
    # Use temporary folder on Vercel
    instance_path = "/tmp/instance"
else:
    # Local development (Termux)
    instance_path = os.path.join(os.path.dirname(__file__), "instance")

os.makedirs(instance_path, exist_ok=True)

# SQLite database path
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{instance_path}/db.sqlite3"
)
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

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)
    stock = db.Column(db.Integer, default=0)
    created_at = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    gst_number = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    customer = db.relationship('Customer')

    amount = db.Column(db.Float)
    gst_percent = db.Column(db.Float, default=18)
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))
    status = db.Column(db.String(50), default="pending")

    def total(self):
        return round(self.amount + (self.amount * self.gst_percent / 100), 2)

    def paid_amount(self):
        payments = Payment.query.filter_by(invoice_id=self.id).all()
        return sum([p.amount for p in payments])

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    amount = db.Column(db.Float)
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float)
    type = db.Column(db.String(50))  # income / expense
    date = db.Column(db.String(50), default=lambda: datetime.now().strftime("%d-%m-%Y"))

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

# ---------------- ADMIN DASHBOARD ----------------
class MyAdminHome(AdminIndexView):
    @expose("/")
    def index(self):
        if not session.get("admin"):
            return redirect("/")

        income = db.session.query(func.sum(Transaction.amount)).filter_by(type="income").scalar() or 0
        expense = db.session.query(func.sum(Transaction.amount)).filter_by(type="expense").scalar() or 0
        profit = income - expense

        invoices = Invoice.query.order_by(Invoice.id.desc()).limit(10).all()
        total_invoices = Invoice.query.count()
        paid = Invoice.query.filter_by(status="paid").count()
        customers = Customer.query.count()

        return self.render(
            "admin_home.html",
            income=income,
            expense=expense,
            profit=profit,
            total_invoices=total_invoices,
            paid_invoices=paid,
            customers=customers,
            invoices=invoices
        )

class SecureModelView(ModelView):
    def is_accessible(self):
        return session.get("admin")
    def inaccessible_callback(self, name, **kwargs):
        return redirect("/")

admin = Admin(app, name="Mobi Invoice", index_view=MyAdminHome())
admin.add_view(SecureModelView(User, db.session, category="Management"))
admin.add_view(SecureModelView(Customer, db.session, category="Management"))
admin.add_view(SecureModelView(Product, db.session, category="Inventory"))
admin.add_view(SecureModelView(Invoice, db.session, category="Accounting"))
admin.add_view(SecureModelView(Payment, db.session, category="Accounting"))
admin.add_view(SecureModelView(Transaction, db.session, category="Accounting"))
admin.add_view(SecureModelView(Company, db.session, category="Settings"))

# ---------------- INVOICE LIST ----------------
@app.route("/invoices")
def invoices_page():
    if not session.get("admin"):
        return redirect("/")
    data = Invoice.query.order_by(Invoice.id.desc()).all()
    return render_template("invoice_list.html", invoices=data)

# ---------------- INVENTORY PAGE ----------------
@app.route("/products")
def products_page():
    if not session.get("admin"):
        return redirect("/")
    products = Product.query.order_by(Product.id.desc()).all()
    return render_template("products.html", products=products)

# ---------------- PDF ----------------
@app.route("/invoice/pdf/<int:id>")
def invoice_pdf(id):
    invoice = db.session.get(Invoice, id)
    if not invoice:
        return "Invoice not found", 404

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer)
    styles = getSampleStyleSheet()
    gst = invoice.amount * invoice.gst_percent / 100

    table_data = [
        ["Field", "Value"],
        ["Invoice ID", invoice.id],
        ["Customer", invoice.customer.name],
        ["Phone", invoice.customer.phone],
        ["Date", invoice.date],
        ["Amount", f"₹{invoice.amount}"],
        ["GST", f"₹{gst}"],
        ["Total", f"₹{invoice.total()}"]
    ]

    table = Table(table_data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))

    content = [
        Paragraph("<b>Mobi Invoice</b>", styles["Title"]),
        Spacer(1, 10),
        table
    ]

    doc.build(content)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"invoice_{id}.pdf")

# ---------------- WHATSAPP ----------------
@app.route("/invoice/whatsapp/<int:id>")
def whatsapp_send(id):
    invoice = db.session.get(Invoice, id)
    if not invoice:
        return "Invoice not found", 404

    msg = f"""Invoice #{invoice.id}
Customer: {invoice.customer.name}
Amount: ₹{invoice.total()}
Status: {invoice.status}

Thank you 🙏"""
    url = "https://wa.me/" + invoice.customer.phone + "?text=" + urllib.parse.quote(msg)
    return redirect(url)

# ---------------- API ----------------
@app.route("/api/invoices")
def api_invoices():
    data = Invoice.query.all()
    return jsonify([
        {"id": i.id, "customer": i.customer.name, "amount": i.total(), "status": i.status}
        for i in data
    ])

@app.route("/api/transactions")
def api_transactions():
    data = Transaction.query.all()
    return jsonify([
        {"id": t.id, "amount": t.amount, "type": t.type, "date": t.date}
        for t in data
    ])

# ---------------- INIT ----------------
with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(username="admin", password=generate_password_hash("admin")))
        db.session.commit()

# ---------------- VERCEL ----------------
app = app

if __name__ == "__main__":
    app.run(debug=True)
