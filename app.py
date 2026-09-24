import os, re, ast, datetime, operator as op
import requests
from flask import Flask, request
from fpdf import FPDF

app = Flask(__name__)

TOKEN = os.environ.get("WA_TOKEN", "")
PHONE_ID = os.environ.get("PHONE_ID", "")
VERIFY = os.environ.get("VERIFY_TOKEN", "my_verify_token")
SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
SHOP = os.environ.get("SHOP_NAME", "مؤسستي")
OWNERS = {x.strip() for x in os.environ.get("OWNER_NUMBERS", "").split(",") if x.strip()}
GRAPH = "https://graph.facebook.com/v20.0"

HELP = (
    "الأوامر:\n"
    "سجل أحمد 200 غداء  (دين عليه)\n"
    "دفع أحمد 50  (سداد)\n"
    "رصيد أحمد\n"
    "الكل  (كل الأرصدة)\n"
    "صفر أحمد  (تصفير الحساب)\n"
    "كشف أحمد  (ملف PDF)\n"
    "عرض العنوان | التفاصيل | السعر  (تصميم عرض PDF)\n"
    "أو اكتب عملية حسابية: 150+200"
)

# ---------------- حاسبة آمنة ----------------
OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
       ast.Pow: op.pow, ast.USub: op.neg, ast.Mod: op.mod}


def safe_eval(n):
    if isinstance(n, ast.Expression):
        return safe_eval(n.body)
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        return OPS[type(n.op)](safe_eval(n.left), safe_eval(n.right))
    if isinstance(n, ast.UnaryOp) and type(n.op) in OPS:
        return OPS[type(n.op)](safe_eval(n.operand))
    raise ValueError


def normalize(t):
    t = t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    return t.replace("×", "*").replace("÷", "/").strip()


def calc(t):
    m = re.match(r"^قسم\s+([\d.]+)\s+على\s+(\d+)$", t)
    if m:
        n = int(m.group(2))
        return "ما يمكن القسمة على صفر" if n == 0 else f"كل شخص يدفع: {float(m.group(1)) / n:.2f}"
    m = re.match(r"^([\d.]+)\s*%\s*من\s+([\d.]+)$", t)
    if m:
        return f"النتيجة: {float(m.group(1)) * float(m.group(2)) / 100:.2f}"
    try:
        v = safe_eval(ast.parse(t, mode="eval"))
        if isinstance(v, float):
            v = int(v) if v.is_integer() else round(v, 4)
        return f"النتيجة: {v}"
    except Exception:
        return None

# ---------------- قاعدة البيانات (Supabase) ----------------


def sb(method, path, **kw):
    h = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
    r = requests.request(method, f"{SB_URL}/rest/v1/{path}", headers=h, timeout=15, **kw)
    r.raise_for_status()
    return r


def add_entry(owner, name, amount, note=""):
    sb("POST", "ledger", json={"owner": owner, "name": name, "amount": amount, "note": note})


def entries(owner, name=None):
    p = {"owner": f"eq.{owner}", "order": "created_at.asc", "select": "name,amount,note,created_at"}
    if name:
        p["name"] = f"eq.{name}"
    return sb("GET", "ledger", params=p).json()


def balance(rows):
    return sum(float(r["amount"]) for r in rows)

# ---------------- PDF ----------------
FONT_DIR = "/tmp/fonts"
FONTS = {
    "Amiri-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf",
    "Amiri-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Bold.ttf",
}


def ensure_fonts():
    os.makedirs(FONT_DIR, exist_ok=True)
    for f, url in FONTS.items():
        path = os.path.join(FONT_DIR, f)
        if not os.path.exists(path):
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            open(path, "wb").write(r.content)


GREEN = (18, 140, 126)
DARK = (30, 41, 59)
LIGHT = (236, 246, 244)


def new_pdf():
    ensure_fonts()
    pdf = FPDF(format="A4")
    pdf.add_font("Amiri", "", os.path.join(FONT_DIR, "Amiri-Regular.ttf"))
    pdf.add_font("Amiri", "B", os.path.join(FONT_DIR, "Amiri-Bold.ttf"))
    pdf.set_text_shaping(True, direction="rtl")
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_fill_color(*GREEN)
    pdf.rect(0, 0, 210, 40, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Amiri", "B", 28)
    pdf.set_xy(10, 12)
    pdf.cell(190, 14, SHOP, align="C")
    pdf.set_text_color(*DARK)
    return pdf


def footer(pdf):
    pdf.set_y(-25)
    pdf.set_font("Amiri", "", 11)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(190, 8, "شكراً لتعاملكم معنا", align="C")


def statement_pdf(name, rows):
    pdf = new_pdf()
    pdf.set_xy(10, 50)
    pdf.set_font("Amiri", "B", 20)
    pdf.cell(190, 10, f"كشف حساب: {name}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Amiri", "", 12)
    pdf.cell(190, 8, f"التاريخ: {datetime.date.today().isoformat()}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    # ترتيب الأعمدة من اليسار لليمين: المبلغ | البيان | التاريخ
    pdf.set_fill_color(*GREEN)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Amiri", "B", 13)
    pdf.cell(45, 10, "المبلغ", border=1, align="C", fill=True)
    pdf.cell(95, 10, "البيان", border=1, align="C", fill=True)
    pdf.cell(50, 10, "التاريخ", border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*DARK)
    pdf.set_font("Amiri", "", 12)
    for i, r in enumerate(rows[-40:]):
        pdf.set_fill_color(*(LIGHT if i % 2 == 0 else (255, 255, 255)))
        amt = float(r["amount"])
        pdf.cell(45, 9, f"{amt:,.2f}", border=1, align="C", fill=True)
        pdf.cell(95, 9, (r.get("note") or "-")[:45], border=1, align="R", fill=True)
        pdf.cell(50, 9, r["created_at"][:10], border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    total = balance(rows)
    pdf.set_fill_color(*DARK)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Amiri", "B", 18)
    label = "المتبقي عليه" if total > 0 else "الحساب مسدد"
    pdf.cell(190, 14, f"{label}: {max(total, 0):,.2f}", align="C", fill=True)
    footer(pdf)
    return bytes(pdf.output())


def offer_pdf(title, details, price):
    pdf = new_pdf()
    pdf.set_fill_color(*LIGHT)
    pdf.rect(15, 55, 180, 150, "F")
    pdf.set_xy(20, 65)
    pdf.set_text_color(*GREEN)
    pdf.set_font("Amiri", "B", 34)
    pdf.multi_cell(170, 16, title, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_x(20)
    pdf.set_text_color(*DARK)
    pdf.set_font("Amiri", "", 18)
    pdf.multi_cell(170, 10, details, align="C", new_x="LMARGIN", new_y="NEXT")
    if price:
        pdf.set_xy(45, 165)
        pdf.set_fill_color(*GREEN)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Amiri", "B", 26)
        pdf.cell(120, 22, f"السعر: {price}", align="C", fill=True)
    footer(pdf)
    return bytes(pdf.output())

# ---------------- واتساب ----------------


def wa_headers():
    return {"Authorization": f"Bearer {TOKEN}"}


def send_text(to, body):
    requests.post(f"{GRAPH}/{PHONE_ID}/messages", headers=wa_headers(), timeout=15,
                  json={"messaging_product": "whatsapp", "to": to, "text": {"body": body}})


def send_pdf(to, data, filename, caption=""):
    up = requests.post(f"{GRAPH}/{PHONE_ID}/media", headers=wa_headers(), timeout=30,
                       data={"messaging_product": "whatsapp", "type": "application/pdf"},
                       files={"file": (filename, data, "application/pdf")})
    up.raise_for_status()
    media_id = up.json()["id"]
    requests.post(f"{GRAPH}/{PHONE_ID}/messages", headers=wa_headers(), timeout=15,
                  json={"messaging_product": "whatsapp", "to": to, "type": "document",
                        "document": {"id": media_id, "filename": filename, "caption": caption}})

# ---------------- معالجة الأوامر ----------------


def handle(sender, text):
    t = normalize(text)
    is_owner = (not OWNERS) or (sender in OWNERS)

    if t in ("مساعدة", "help", "start", "ابدأ"):
        return send_text(sender, HELP)

    r = calc(t)
    if r:
        return send_text(sender, r)

    if not is_owner:
        return send_text(sender, "هذا الأمر غير مصرح لك.")

    m = re.match(r"^(سجل|دفع)\s+(\S+)\s+([\d.]+)\s*(.*)$", t)
    if m:
        cmd, name, amt, note = m.group(1), m.group(2), float(m.group(3)), m.group(4)
        add_entry(sender, name, amt if cmd == "سجل" else -amt, note or ("سداد" if cmd == "دفع" else ""))
        bal = balance(entries(sender, name))
        return send_text(sender, f"تم. رصيد {name} الآن: {bal:,.2f}")

    m = re.match(r"^رصيد\s+(\S+)$", t)
    if m:
        return send_text(sender, f"رصيد {m.group(1)}: {balance(entries(sender, m.group(1))):,.2f}")

    if t == "الكل":
        totals = {}
        for row in entries(sender):
            totals[row["name"]] = totals.get(row["name"], 0) + float(row["amount"])
        lines = [f"{n}: {v:,.2f}" for n, v in totals.items() if abs(v) > 0.001]
        return send_text(sender, "\n".join(lines) or "ما فيه أرصدة مفتوحة.")

    m = re.match(r"^صفر\s+(\S+)$", t)
    if m:
        name = m.group(1)
        bal = balance(entries(sender, name))
        if abs(bal) > 0.001:
            add_entry(sender, name, -bal, "تصفير الحساب")
        return send_text(sender, f"تم تصفير حساب {name}.")

    m = re.match(r"^كشف\s+(\S+)$", t)
    if m:
        name = m.group(1)
        rows = entries(sender, name)
        if not rows:
            return send_text(sender, "ما فيه حركات لهذا الاسم.")
        return send_pdf(sender, statement_pdf(name, rows), "statement.pdf", f"كشف حساب {name}")

    if t.startswith("عرض"):
        parts = [p.strip() for p in t[3:].split("|")]
        if len(parts) < 2 or not parts[0]:
            return send_text(sender, "الصيغة: عرض العنوان | التفاصيل | السعر")
        title, details = parts[0], parts[1]
        price = parts[2] if len(parts) > 2 else ""
        cap = f"{title}\n{details}" + (f"\nالسعر: {price}" if price else "")
        return send_pdf(sender, offer_pdf(title, details, price), "offer.pdf", cap)

    send_text(sender, "ما فهمت الأمر.\n\n" + HELP)


@app.get("/")
def home():
    return "Bot is running"


@app.get("/webhook")
def verify():
    if request.args.get("hub.verify_token") == VERIFY:
        return request.args.get("hub.challenge", ""), 200
    return "forbidden", 403


@app.post("/webhook")
def receive():
    data = request.get_json(silent=True) or {}
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    except (KeyError, IndexError):
        return "ok", 200
    if msg.get("type") == "text":
        try:
            handle(msg["from"], msg["text"]["body"])
        except Exception as e:
            print("ERROR:", e)
            try:
                send_text(msg["from"], "صار خطأ، حاول مرة ثانية.")
            except Exception:
                pass
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
