from flask import Flask, request, jsonify, session, redirect, url_for, render_template, send_file, abort
import sqlite3, os, csv, io, json
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.environ.get('APP_DB',os.path.join(BASE,'construction.db'))
app=Flask(__name__); app.secret_key=os.environ.get('SECRET_KEY','change-this-secret-before-deployment'); app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','0')=='1',PERMANENT_SESSION_LIFETIME=60*60*8)

SCHEMA='''
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,site TEXT,client TEXT,budget REAL DEFAULT 0,status TEXT DEFAULT 'Active',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,date TEXT,category TEXT,vendor TEXT,description TEXT,rate REAL DEFAULT 0,quantity REAL DEFAULT 0,amount REAL DEFAULT 0,paid REAL DEFAULT 0,mode TEXT,paid_to TEXT,paid_by TEXT,reference TEXT,notes TEXT,source_sheet TEXT,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,date TEXT,received_from TEXT,description TEXT,amount REAL DEFAULT 0,mode TEXT,reference TEXT,notes TEXT,source_sheet TEXT,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS labour_records(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,vendor TEXT,date TEXT,start_date TEXT,end_date TEXT,description TEXT,amount REAL DEFAULT 0,invoice_no TEXT,source_sheet TEXT,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT UNIQUE,password TEXT,role TEXT DEFAULT 'admin',active INTEGER DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS contractor_invoices(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,invoice_no TEXT NOT NULL,invoice_date TEXT NOT NULL,contractor TEXT NOT NULL,work_category TEXT,period_from TEXT,period_to TEXT,site_location TEXT,description TEXT,status TEXT DEFAULT 'Draft',tax_percent REAL DEFAULT 0,retention_percent REAL DEFAULT 0,other_deductions REAL DEFAULT 0,notes TEXT,created_by TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,linked_transaction_id INTEGER,FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS measurement_lines(id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id INTEGER NOT NULL,sl_no INTEGER,work_date TEXT,location TEXT,description TEXT NOT NULL,nos REAL DEFAULT 1,length REAL DEFAULT 0,width REAL DEFAULT 0,height REAL DEFAULT 0,quantity REAL DEFAULT 0,unit TEXT DEFAULT 'Nos',rate REAL DEFAULT 0,amount REAL DEFAULT 0,remarks TEXT,FOREIGN KEY(invoice_id) REFERENCES contractor_invoices(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS invoice_payments(id INTEGER PRIMARY KEY AUTOINCREMENT,invoice_id INTEGER NOT NULL,payment_date TEXT NOT NULL,amount REAL NOT NULL,mode TEXT,reference TEXT,paid_to TEXT,paid_by TEXT,notes TEXT,FOREIGN KEY(invoice_id) REFERENCES contractor_invoices(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT,user TEXT,action TEXT,entity TEXT,entity_id INTEGER,details TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS transaction_payments(id INTEGER PRIMARY KEY AUTOINCREMENT,transaction_id INTEGER NOT NULL,payment_date TEXT NOT NULL,amount REAL NOT NULL,mode TEXT,reference TEXT,paid_to TEXT,paid_by TEXT,notes TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE);
CREATE UNIQUE INDEX IF NOT EXISTS ux_invoice_project_number ON contractor_invoices(project_id,invoice_no);
'''
def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c
def init_db():
 c=db(); c.executescript(SCHEMA)
 # Safe in-place upgrades for databases created by earlier app builds.
 cols={r['name'] for r in c.execute('PRAGMA table_info(users)')}
 if 'active' not in cols: c.execute('ALTER TABLE users ADD COLUMN active INTEGER DEFAULT 1')
 if 'created_at' not in cols:
  # SQLite cannot add a column with a non-constant DEFAULT to an existing table.
  # Add it nullable, then backfill existing accounts.
  c.execute('ALTER TABLE users ADD COLUMN created_at TEXT')
  c.execute("UPDATE users SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
 # Additive schema upgrades keep existing workbook data intact.
 for table, definitions in {
  'transactions': {'bill_no':'TEXT','due_date':'TEXT','tax_percent':'REAL DEFAULT 0','tax_amount':'REAL DEFAULT 0','discount_amount':'REAL DEFAULT 0','cost_code':'TEXT','payment_terms':'TEXT','status':"TEXT DEFAULT 'Open'",'attachment_name':'TEXT','created_at':'TEXT'},
  'receipts': {'receipt_no':'TEXT','bank_account':'TEXT','attachment_name':'TEXT','created_at':'TEXT'},
  'labour_records': {'work_type':'TEXT','workers_count':'REAL DEFAULT 0','days':'REAL DEFAULT 0','rate':'REAL DEFAULT 0','quantity':'REAL DEFAULT 0','unit':'TEXT','paid':'REAL DEFAULT 0','notes':'TEXT','status':"TEXT DEFAULT 'Open'"}
 }.items():
  existing={r['name'] for r in c.execute(f'PRAGMA table_info({table})')}
  for col, definition in definitions.items():
   if col not in existing: c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {definition}')
 invcols={r['name'] for r in c.execute('PRAGMA table_info(contractor_invoices)')}
 if 'linked_transaction_id' not in invcols: c.execute('ALTER TABLE contractor_invoices ADD COLUMN linked_transaction_id INTEGER')
 c.execute("UPDATE transactions SET status=CASE WHEN paid>=amount AND amount>0 THEN 'Paid' WHEN paid>0 THEN 'Part-paid' ELSE 'Open' END WHERE status IS NULL OR (status='Open' AND paid>0)")
 if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): c.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',(os.environ.get('ADMIN_USERNAME','admin'),generate_password_hash(os.environ.get('ADMIN_PASSWORD','ChangeMe123!')),'admin'))
 else:
  u=c.execute('SELECT id,password FROM users WHERE username=?',('admin',)).fetchone()
  if u and not str(u['password']).startswith(('pbkdf2:','scrypt:')): c.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(u['password']),u['id']))
 c.commit(); c.close()
init_db()
def auth(f):
 @wraps(f)
 def w(*a,**k):
  if not session.get('user'): return jsonify({'error':'Authentication required'}),401
  if request.method in ('POST','PUT','DELETE') and session.get('role')=='viewer' and request.endpoint!='change_password': return jsonify({'error':'Viewer accounts have read-only access.'}),403
  return f(*a,**k)
 return w
def admin_required(f):
 @wraps(f)
 def w(*a,**k):
  if not session.get('user'): return jsonify({'error':'Authentication required'}),401
  if session.get('role')!='admin': return jsonify({'error':'Administrator permission required'}),403
  return f(*a,**k)
 return w
def rowdict(r): return dict(r) if r else None
def audit(action,entity,eid=None,details=''):
 c=db(); c.execute('INSERT INTO audit_log(user,action,entity,entity_id,details) VALUES(?,?,?,?,?)',(session.get('user'),action,entity,eid,str(details))); c.commit(); c.close()
def measured_quantity(nos,length,width,height,manual=0):
 if manual and float(manual)>0: return float(manual)
 nos=float(nos or 1); length=float(length or 0); width=float(width or 0); height=float(height or 0)
 if length>0 and width>0 and height>0: return nos*length*width*height
 if length>0 and width>0: return nos*length*width
 if length>0: return nos*length
 return nos
def sync_invoice_transaction(inv_id):
 c=db(); inv=c.execute('SELECT * FROM contractor_invoices WHERE id=?',(inv_id,)).fetchone()
 if not inv: c.close(); return
 subtotal=c.execute('SELECT COALESCE(SUM(amount),0) n FROM measurement_lines WHERE invoice_id=?',(inv_id,)).fetchone()['n']
 paid=c.execute('SELECT COALESCE(SUM(amount),0) n FROM invoice_payments WHERE invoice_id=?',(inv_id,)).fetchone()['n']
 tax=float(subtotal)*float(inv['tax_percent'] or 0)/100; retention=float(subtotal)*float(inv['retention_percent'] or 0)/100
 net=float(subtotal)+tax-float(inv['other_deductions'] or 0)-retention
 txid=inv['linked_transaction_id']; values=(inv['project_id'],inv['invoice_date'],inv['work_category'] or 'Contractor',inv['contractor'],f"Contractor invoice {inv['invoice_no']}",net,paid,inv['invoice_no'],inv['notes'])
 if txid:
  c.execute('UPDATE transactions SET project_id=?,date=?,category=?,vendor=?,description=?,amount=?,paid=?,reference=?,notes=? WHERE id=?',values+(txid,))
 else:
  cur=c.execute('INSERT INTO transactions(project_id,date,category,vendor,description,amount,paid,reference,notes) VALUES(?,?,?,?,?,?,?,?,?)',values);c.execute('UPDATE contractor_invoices SET linked_transaction_id=? WHERE id=?',(cur.lastrowid,inv_id))
 c.commit();c.close()
def invoice_totals(inv_id):
 c=db(); inv=c.execute('SELECT * FROM contractor_invoices WHERE id=?',(inv_id,)).fetchone()
 if not inv: c.close(); return None
 lines=[rowdict(x) for x in c.execute('SELECT * FROM measurement_lines WHERE invoice_id=? ORDER BY sl_no,id',(inv_id,))]
 paid=c.execute('SELECT COALESCE(SUM(amount),0) n FROM invoice_payments WHERE invoice_id=?',(inv_id,)).fetchone()['n']; c.close()
 subtotal=sum(float(x['amount'] or 0) for x in lines); tax=subtotal*float(inv['tax_percent'] or 0)/100; retention=subtotal*float(inv['retention_percent'] or 0)/100; gross=subtotal+tax-float(inv['other_deductions'] or 0)-retention
 return {'invoice':rowdict(inv),'lines':lines,'subtotal':subtotal,'tax':tax,'retention':retention,'deductions':float(inv['other_deductions'] or 0),'gross':gross,'paid':paid,'balance':gross-paid}

@app.route('/')
def index(): return render_template('index.html',logged=bool(session.get('user')))
@app.route('/login',methods=['POST'])
def login():
 d=request.get_json(silent=True) or request.form; c=db(); u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(d.get('username'),)).fetchone(); c.close()
 if not u or not check_password_hash(u['password'],d.get('password','')): return jsonify({'error':'Invalid username or password'}),401
 session.clear(); session['user']=u['username']; session['role']=u['role']; session.permanent=True; return jsonify({'ok':True})
@app.route('/logout',methods=['POST'])
def logout(): session.clear(); return jsonify({'ok':True})
@app.route('/api/me')
def me(): return jsonify({'user':session.get('user'),'role':session.get('role')})
@app.route('/api/projects',methods=['GET','POST'])
@auth
def projects():
 c=db()
 if request.method=='POST':
  d=request.get_json(); cur=c.execute('INSERT INTO projects(name,site,client,budget,status) VALUES(?,?,?,?,?)',(d['name'],d.get('site'),d.get('client'),float(d.get('budget') or 0),d.get('status','Active'))); c.commit(); p=c.execute('SELECT * FROM projects WHERE id=?',(cur.lastrowid,)).fetchone(); c.close(); return jsonify(rowdict(p)),201
 out=[rowdict(x) for x in c.execute('SELECT * FROM projects ORDER BY id DESC')]; c.close(); return jsonify(out)
@app.route('/api/projects/<int:pid>',methods=['PUT','DELETE'])
@auth
def project_detail(pid):
 c=db()
 if request.method=='DELETE': c.execute('DELETE FROM transactions WHERE project_id=?',(pid,)); c.execute('DELETE FROM receipts WHERE project_id=?',(pid,)); c.execute('DELETE FROM labour_records WHERE project_id=?',(pid,)); c.execute('DELETE FROM contractor_invoices WHERE project_id=?',(pid,)); c.execute('DELETE FROM projects WHERE id=?',(pid,)); c.commit(); c.close(); return jsonify({'ok':True})
 d=request.get_json(); c.execute('UPDATE projects SET name=?,site=?,client=?,budget=?,status=? WHERE id=?',(d['name'],d.get('site'),d.get('client'),float(d.get('budget') or 0),d.get('status','Active'),pid)); c.commit(); p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone(); c.close(); return jsonify(rowdict(p))
@app.route('/api/summary/<int:pid>')
@auth
def summary(pid):
 c=db(); p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
 if not p: c.close(); return jsonify({'error':'Project not found'}),404
 t=c.execute('SELECT COALESCE(SUM(amount),0) amount,COALESCE(SUM(paid),0) paid,COUNT(*) count FROM transactions WHERE project_id=?',(pid,)).fetchone(); r=c.execute('SELECT COALESCE(SUM(amount),0) received FROM receipts WHERE project_id=?',(pid,)).fetchone()
 cats=[rowdict(x) for x in c.execute('SELECT category,COALESCE(SUM(amount),0) amount,COALESCE(SUM(paid),0) paid FROM transactions WHERE project_id=? GROUP BY category ORDER BY amount DESC',(pid,))]
 vendors=[rowdict(x) for x in c.execute('SELECT vendor,COALESCE(SUM(amount),0) amount,COALESCE(SUM(paid),0) paid,COALESCE(SUM(amount-paid),0) balance FROM transactions WHERE project_id=? GROUP BY vendor ORDER BY balance DESC',(pid,))]
 recent=[rowdict(x) for x in c.execute('SELECT * FROM transactions WHERE project_id=? ORDER BY date DESC,id DESC LIMIT 8',(pid,))]
 c.close(); return jsonify({'project':rowdict(p),'expense':t['amount'],'paid':t['paid'],'balance':t['amount']-t['paid'],'received':r['received'],'transaction_count':t['count'],'categories':cats,'vendors':vendors,'recent':recent})
@app.route('/api/transactions',methods=['GET','POST'])
@auth
def transactions():
 c=db()
 if request.method=='POST':
  d=request.get_json() or {}; amount=float(d.get('amount') or 0); rate=float(d.get('rate') or 0); qty=float(d.get('quantity') or 0)
  subtotal=amount or rate*qty; tax=float(d.get('tax_amount') or (subtotal*float(d.get('tax_percent') or 0)/100)); discount=float(d.get('discount_amount') or 0); amount=max(0,subtotal+tax-discount); paid=float(d.get('paid') or 0)
  if not d.get('project_id') or not d.get('description') or amount<0 or paid<0 or paid>amount: c.close(); return jsonify({'error':'Check project, description, amount and paid value. Paid cannot exceed the total.'}),400
  tx_status=d.get('status'); tx_status=tx_status if tx_status and tx_status!='Automatic' else ('Paid' if paid>=amount and amount>0 else ('Part-paid' if paid>0 else 'Open'))
  cur=c.execute('INSERT INTO transactions(project_id,date,category,vendor,description,rate,quantity,amount,paid,mode,paid_to,paid_by,reference,notes,bill_no,due_date,tax_percent,tax_amount,discount_amount,cost_code,payment_terms,status,attachment_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(d['project_id'],d.get('date'),d.get('category') or 'General',d.get('vendor'),d.get('description'),rate,qty,amount,paid,d.get('mode'),d.get('paid_to'),d.get('paid_by'),d.get('reference'),d.get('notes'),d.get('bill_no'),d.get('due_date'),float(d.get('tax_percent') or 0),tax,discount,d.get('cost_code'),d.get('payment_terms'),tx_status,d.get('attachment_name'),datetime.now().isoformat(timespec='seconds'))) 
  tid=cur.lastrowid
  if paid>0: c.execute('INSERT INTO transaction_payments(transaction_id,payment_date,amount,mode,reference,paid_to,paid_by,notes) VALUES(?,?,?,?,?,?,?,?)',(tid,d.get('payment_date') or d.get('date') or datetime.now().strftime('%Y-%m-%d'),paid,d.get('mode'),d.get('reference'),d.get('paid_to'),d.get('paid_by'),'Opening payment'))
  c.commit(); x=c.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone(); c.close(); audit('create','transaction',tid,d.get('description','')); return jsonify(rowdict(x)),201
 pid=request.args.get('project_id'); q='SELECT * FROM transactions'; args=[]
 if pid: q+=' WHERE project_id=?'; args.append(pid)
 q+=' ORDER BY date DESC,id DESC'; out=[rowdict(x) for x in c.execute(q,args)]; c.close(); return jsonify(out)
@app.route('/api/transactions/<int:tid>',methods=['PUT','DELETE'])
@auth
def transaction_detail(tid):
 c=db()
 if c.execute('SELECT 1 FROM contractor_invoices WHERE linked_transaction_id=?',(tid,)).fetchone(): c.close(); return jsonify({'error':'This expense is linked to a contractor invoice. Edit or remove it from Contractor invoices.'}),400
 if request.method=='DELETE': c.execute('DELETE FROM transactions WHERE id=?',(tid,)); c.commit(); c.close(); return jsonify({'ok':True})
 d=request.get_json() or {}; rate=float(d.get('rate') or 0); qty=float(d.get('quantity') or 0); subtotal=float(d.get('amount') or rate*qty); tax=float(d.get('tax_amount') or subtotal*float(d.get('tax_percent') or 0)/100); discount=float(d.get('discount_amount') or 0); amount=max(0,subtotal+tax-discount)
 payment_total=c.execute('SELECT COALESCE(SUM(amount),0) n FROM transaction_payments WHERE transaction_id=?',(tid,)).fetchone()['n']
 paid=float(payment_total if payment_total>0 else (d.get('paid') or 0))
 if paid>amount: c.close(); return jsonify({'error':'Total payments cannot exceed the revised transaction amount.'}),400
 tx_status=d.get('status'); tx_status=tx_status if tx_status and tx_status!='Automatic' else ('Paid' if paid>=amount and amount>0 else ('Part-paid' if paid>0 else 'Open'))
 c.execute('UPDATE transactions SET date=?,category=?,vendor=?,description=?,rate=?,quantity=?,amount=?,paid=?,mode=?,paid_to=?,paid_by=?,reference=?,notes=?,bill_no=?,due_date=?,tax_percent=?,tax_amount=?,discount_amount=?,cost_code=?,payment_terms=?,status=? WHERE id=?',(d.get('date'),d.get('category'),d.get('vendor'),d.get('description'),rate,qty,amount,paid,d.get('mode'),d.get('paid_to'),d.get('paid_by'),d.get('reference'),d.get('notes'),d.get('bill_no'),d.get('due_date'),float(d.get('tax_percent') or 0),tax,discount,d.get('cost_code'),d.get('payment_terms'),tx_status,tid)); c.commit(); x=c.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone(); c.close(); audit('update','transaction',tid,d.get('description','')); return jsonify(rowdict(x))
@app.route('/api/receipts',methods=['GET','POST'])
@auth
def receipts():
 c=db()
 if request.method=='POST':
  d=request.get_json() or {}; amount=float(d.get('amount') or 0)
  if not d.get('project_id') or not d.get('received_from') or amount<=0: c.close(); return jsonify({'error':'Project, received-from and a positive amount are required.'}),400
  cur=c.execute('INSERT INTO receipts(project_id,date,received_from,description,amount,mode,reference,notes,receipt_no,bank_account,attachment_name,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(d['project_id'],d.get('date') or today_date(),d.get('received_from'),d.get('description'),amount,d.get('mode'),d.get('reference'),d.get('notes'),d.get('receipt_no'),d.get('bank_account'),d.get('attachment_name'),datetime.now().isoformat(timespec='seconds'))); c.commit(); x=c.execute('SELECT * FROM receipts WHERE id=?',(cur.lastrowid,)).fetchone(); c.close(); audit('create','receipt',x['id'],d.get('receipt_no','')); return jsonify(rowdict(x)),201
 pid=request.args.get('project_id'); q='SELECT * FROM receipts'; args=[]
 if pid:q+=' WHERE project_id=?';args.append(pid)
 q+=' ORDER BY date DESC,id DESC'; out=[rowdict(x) for x in c.execute(q,args)];c.close();return jsonify(out)

@app.route('/api/receipts/<int:rid>',methods=['PUT','DELETE'])
@auth
def receipt_detail(rid):
 c=db(); row=c.execute('SELECT * FROM receipts WHERE id=?',(rid,)).fetchone()
 if not row:c.close();return jsonify({'error':'Receipt not found'}),404
 if request.method=='DELETE':c.execute('DELETE FROM receipts WHERE id=?',(rid,));c.commit();c.close();audit('delete','receipt',rid);return jsonify({'ok':True})
 d=request.get_json() or {}; amount=float(d.get('amount') or 0)
 if amount<=0:c.close();return jsonify({'error':'Receipt amount must be positive.'}),400
 c.execute('UPDATE receipts SET date=?,received_from=?,description=?,amount=?,mode=?,reference=?,notes=?,receipt_no=?,bank_account=? WHERE id=?',(d.get('date'),d.get('received_from'),d.get('description'),amount,d.get('mode'),d.get('reference'),d.get('notes'),d.get('receipt_no'),d.get('bank_account'),rid));c.commit();x=c.execute('SELECT * FROM receipts WHERE id=?',(rid,)).fetchone();c.close();audit('update','receipt',rid);return jsonify(rowdict(x))

def today_date(): return datetime.now().strftime('%Y-%m-%d')

@app.route('/api/labour',methods=['GET','POST'])
@auth
def labour():
 c=db()
 if request.method=='POST':
  d=request.get_json() or {}; amount=float(d.get('amount') or (float(d.get('quantity') or 0)*float(d.get('rate') or 0)))
  if not d.get('project_id') or not d.get('vendor') or amount<0:c.close();return jsonify({'error':'Project, contractor and valid bill amount are required.'}),400
  cur=c.execute('INSERT INTO labour_records(project_id,vendor,date,start_date,end_date,description,amount,invoice_no,work_type,workers_count,days,rate,quantity,unit,paid,notes,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(d['project_id'],d.get('vendor'),d.get('date'),d.get('start_date'),d.get('end_date'),d.get('description'),amount,d.get('invoice_no'),d.get('work_type'),float(d.get('workers_count') or 0),float(d.get('days') or 0),float(d.get('rate') or 0),float(d.get('quantity') or 0),d.get('unit'),float(d.get('paid') or 0),d.get('notes'),d.get('status') or 'Open')); c.commit(); x=c.execute('SELECT * FROM labour_records WHERE id=?',(cur.lastrowid,)).fetchone(); c.close();return jsonify(rowdict(x)),201
 pid=request.args.get('project_id'); q='SELECT * FROM labour_records';args=[]
 if pid:q+=' WHERE project_id=?';args.append(pid)
 q+=' ORDER BY date DESC,id DESC';out=[rowdict(x) for x in c.execute(q,args)];c.close();return jsonify(out)

@app.route('/api/labour/<int:lid>',methods=['PUT','DELETE'])
@auth
def labour_detail(lid):
 c=db(); row=c.execute('SELECT * FROM labour_records WHERE id=?',(lid,)).fetchone()
 if not row:c.close();return jsonify({'error':'Labour record not found'}),404
 if request.method=='DELETE':c.execute('DELETE FROM labour_records WHERE id=?',(lid,));c.commit();c.close();return jsonify({'ok':True})
 d=request.get_json() or {}; amount=float(d.get('amount') or float(d.get('quantity') or 0)*float(d.get('rate') or 0))
 c.execute('UPDATE labour_records SET vendor=?,date=?,start_date=?,end_date=?,description=?,amount=?,invoice_no=?,work_type=?,workers_count=?,days=?,rate=?,quantity=?,unit=?,paid=?,notes=?,status=? WHERE id=?',(d.get('vendor'),d.get('date'),d.get('start_date'),d.get('end_date'),d.get('description'),amount,d.get('invoice_no'),d.get('work_type'),float(d.get('workers_count') or 0),float(d.get('days') or 0),float(d.get('rate') or 0),float(d.get('quantity') or 0),d.get('unit'),float(d.get('paid') or 0),d.get('notes'),d.get('status') or 'Open',lid));c.commit();x=c.execute('SELECT * FROM labour_records WHERE id=?',(lid,)).fetchone();c.close();return jsonify(rowdict(x))

@app.route('/api/transactions/<int:tid>/payments',methods=['GET','POST'])
@auth
def transaction_payments(tid):
 c=db(); tx=c.execute('SELECT * FROM transactions WHERE id=?',(tid,)).fetchone()
 if not tx:c.close();return jsonify({'error':'Transaction not found'}),404
 if request.method=='POST':
  d=request.get_json() or {}; amount=float(d.get('amount') or 0)
  linked=c.execute('SELECT id FROM contractor_invoices WHERE linked_transaction_id=?',(tid,)).fetchone()
  if linked:c.close();return jsonify({'error':'This payment is controlled by its contractor invoice. Open the invoice to record payment.'}),400
  already=float(c.execute('SELECT COALESCE(SUM(amount),0) n FROM transaction_payments WHERE transaction_id=?',(tid,)).fetchone()['n'])
  if already==0 and float(tx['paid'] or 0)>0:
   already=float(tx['paid']); c.execute('INSERT INTO transaction_payments(transaction_id,payment_date,amount,mode,reference,paid_to,paid_by,notes) VALUES(?,?,?,?,?,?,?,?)',(tid,tx['date'] or today_date(),already,tx['mode'],tx['reference'],tx['paid_to'],tx['paid_by'],'Imported / opening payment balance'))
  if amount<=0 or already+amount>float(tx['amount'] or 0):c.close();return jsonify({'error':'Enter a positive payment that does not exceed the outstanding balance.'}),400
  cur=c.execute('INSERT INTO transaction_payments(transaction_id,payment_date,amount,mode,reference,paid_to,paid_by,notes) VALUES(?,?,?,?,?,?,?,?)',(tid,d.get('payment_date') or today_date(),amount,d.get('mode'),d.get('reference'),d.get('paid_to'),d.get('paid_by'),d.get('notes'))); total=already+amount
  c.execute('UPDATE transactions SET paid=?,status=? WHERE id=?',(total,'Paid' if total>=float(tx['amount'] or 0) else 'Part-paid',tid));c.commit();c.close();audit('payment','transaction',tid,amount);return jsonify({'id':cur.lastrowid,'paid':total,'balance':float(tx['amount'])-total}),201
 rows=[rowdict(x) for x in c.execute('SELECT * FROM transaction_payments WHERE transaction_id=? ORDER BY payment_date DESC,id DESC',(tid,))];c.close();return jsonify(rows)

@app.route('/api/export/<int:pid>.csv')
@auth
def export_csv(pid):
 c=db(); rows=c.execute('SELECT date,bill_no,due_date,category,cost_code,vendor,description,rate,quantity,tax_percent,tax_amount,discount_amount,amount,paid,status,payment_terms,mode,paid_to,paid_by,reference,notes FROM transactions WHERE project_id=? ORDER BY date',(pid,)).fetchall(); c.close(); s=io.StringIO(); w=csv.writer(s); w.writerow(rows[0].keys() if rows else ['date','bill_no','due_date','category','cost_code','vendor','description','rate','quantity','tax_percent','tax_amount','discount_amount','amount','paid','status','payment_terms','mode','paid_to','paid_by','reference','notes']); w.writerows([tuple(x) for x in rows]); mem=io.BytesIO(s.getvalue().encode('utf-8-sig')); mem.seek(0); return send_file(mem,mimetype='text/csv',as_attachment=True,download_name=f'project-{pid}-transactions.csv')

@app.route('/api/export/<int:pid>.xlsx')
@auth
def export_project_xlsx(pid):
 try:
  from openpyxl import Workbook
  from openpyxl.styles import Font, PatternFill, Alignment
  from openpyxl.utils import get_column_letter
  c=db(); project=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
  if not project:c.close();return jsonify({'error':'Project not found'}),404
  datasets={
   'Expenses': ('SELECT date,bill_no,due_date,category,cost_code,vendor,description,rate,quantity,tax_percent,tax_amount,discount_amount,amount,paid,status,payment_terms,mode,paid_to,paid_by,reference,notes FROM transactions WHERE project_id=? ORDER BY date,id',(pid,)),
   'Receipts': ('SELECT date,receipt_no,received_from,description,amount,mode,bank_account,reference,notes FROM receipts WHERE project_id=? ORDER BY date,id',(pid,)),
   'Labour Bills': ('SELECT vendor,invoice_no,date,start_date,end_date,work_type,description,workers_count,days,quantity,unit,rate,amount,paid,status,notes FROM labour_records WHERE project_id=? ORDER BY date,id',(pid,)),
   'Contractor Invoices': ('SELECT invoice_no,invoice_date,contractor,work_category,period_from,period_to,site_location,description,status,tax_percent,retention_percent,other_deductions,notes FROM contractor_invoices WHERE project_id=? ORDER BY invoice_date,id',(pid,)),
   'Expense Payments': ('SELECT p.payment_date,t.bill_no,t.vendor,t.description,p.amount,p.mode,p.reference,p.paid_to,p.paid_by,p.notes FROM transaction_payments p JOIN transactions t ON t.id=p.transaction_id WHERE t.project_id=? ORDER BY p.payment_date,p.id',(pid,)),
   'Invoice Measurements': ('SELECT i.invoice_no,i.contractor,m.sl_no,m.work_date,m.location,m.description,m.nos,m.length,m.width,m.height,m.quantity,m.unit,m.rate,m.amount,m.remarks FROM measurement_lines m JOIN contractor_invoices i ON i.id=m.invoice_id WHERE i.project_id=? ORDER BY i.invoice_id,m.sl_no',(pid,)),
   'Invoice Payments': ('SELECT i.invoice_no,i.contractor,p.payment_date,p.amount,p.mode,p.reference,p.paid_to,p.paid_by,p.notes FROM invoice_payments p JOIN contractor_invoices i ON i.id=p.invoice_id WHERE i.project_id=? ORDER BY p.payment_date,p.id',(pid,))
  }
  wb=Workbook(); ws=wb.active; ws.title='Project Info'; ws.append(['Project','Site','Client','Budget','Status']);ws.append([project['name'],project['site'],project['client'],project['budget'],project['status']])
  for name,(sql,args) in datasets.items():
   sh=wb.create_sheet(name); rows=c.execute(sql,args).fetchall()
   if rows:
    headers=list(rows[0].keys());sh.append(headers)
    for row in rows: sh.append([row[h] for h in headers])
   else: sh.append(['No records'])
  c.close()
  for sh in wb.worksheets:
   sh.freeze_panes='A2';sh.auto_filter.ref=sh.dimensions
   for cell in sh[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='173C5A');cell.alignment=Alignment(vertical='center')
   for col in sh.columns:
    letter=get_column_letter(col[0].column); width=min(36,max(12,max((len(str(x.value or '')) for x in col),default=10)+2));sh.column_dimensions[letter].width=width
  mem=io.BytesIO();wb.save(mem);mem.seek(0)
  return send_file(mem,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name=f'project-{pid}-full-ledger.xlsx')
 except Exception as e:return jsonify({'error':f'Could not generate Excel report: {e}'}),500

@app.route('/voucher/<int:tid>')
@auth
def voucher(tid):
 c=db(); t=c.execute('SELECT t.*,p.name project,p.site FROM transactions t JOIN projects p ON p.id=t.project_id WHERE t.id=?',(tid,)).fetchone(); c.close()
 if not t: abort(404)
 return render_template('voucher.html',t=rowdict(t))
@app.route('/api/import',methods=['POST'])
@auth
def import_workbook():
 f=request.files.get('file')
 if not f:return jsonify({'error':'Select an .xlsm or .xlsx file'}),400
 try:
  import openpyxl
  wb=openpyxl.load_workbook(f,read_only=True,data_only=True)
  project_name=request.form.get('project_name') or 'Imported project - Sucheth Infra'; site=''; client=''
  if 'M.Sheet ' in wb.sheetnames:
   ws=wb['M.Sheet ']; site=str(ws['B3'].value or ''); client=str(ws['I3'].value or '')
  c=db(); cur=c.execute('INSERT INTO projects(name,site,client,status) VALUES(?,?,? ,?)',(project_name,site,client,'Active')); pid=cur.lastrowid; count=0
  # Master transaction sheet is canonical. Import its recorded transaction rows; category tabs remain represented in category/vendor fields.
  if 'M.Sheet ' in wb.sheetnames:
   ws=wb['M.Sheet ']
   for row in ws.iter_rows(min_row=6,values_only=True):
    vals=list(row)+[None]*11; date=vals[1]
    if not date or not vals[2] and not vals[3]:continue
    if isinstance(date,datetime): date=date.strftime('%Y-%m-%d')
    elif hasattr(date,'strftime'):date=date.strftime('%Y-%m-%d')
    try: rate=float(vals[4] or 0); qty=float(vals[5] or 0); amount=float(vals[6] or 0) or rate*qty; paid=float(vals[7] or 0)
    except: rate=qty=amount=paid=0
    c.execute('INSERT INTO transactions(project_id,date,category,vendor,description,rate,quantity,amount,paid,mode,paid_to,paid_by,source_sheet) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(pid,str(date)[:10],str(vals[2] or 'General').strip(),str(vals[2] or '').strip(),str(vals[3] or ''),rate,qty,amount,paid,str(vals[8] or ''),str(vals[9] or ''),str(vals[10] or ''),'M.Sheet'));count+=1
  receipt_count=0
  if 'Pay Ledger' in wb.sheetnames:
   ws=wb['Pay Ledger']
   for row in ws.iter_rows(min_row=4,values_only=True):
    vals=list(row)+[None]*7; date=vals[1]
    if not date or not vals[4]:continue
    if hasattr(date,'strftime'):date=date.strftime('%Y-%m-%d')
    try:amount=float(vals[4] or 0)
    except:continue
    c.execute('INSERT INTO receipts(project_id,date,received_from,description,amount,mode,notes,source_sheet) VALUES(?,?,?,?,?,?,?,?)',(pid,str(date)[:10],str(vals[2] or ''),str(vals[3] or ''),amount,str(vals[5] or ''),str(vals[6] or ''),'Pay Ledger'));receipt_count+=1
  # Preserve the historical weekly contractor-bill register from the two Hamani summary sheets.
  labour_count=0
  for sheet_name in ('Hamani','Hamani (2)'):
   if sheet_name not in wb.sheetnames: continue
   ws=wb[sheet_name]
   for row in ws.iter_rows(min_row=3,max_row=ws.max_row,values_only=True):
    vals=list(row)+[None]*7
    vendor=vals[2]; amount=vals[6] if sheet_name=='Hamani' else vals[5]
    bill_date=vals[1]; start=vals[4] if sheet_name=='Hamani' else vals[3]; end=vals[5] if sheet_name=='Hamani' else vals[4]
    if not vendor or not bill_date or amount in (None,''): continue
    try: amount=float(amount)
    except (ValueError,TypeError): continue
    def date_text(v):
     return v.strftime('%Y-%m-%d') if hasattr(v,'strftime') else (str(v)[:10] if v else None)
    c.execute('INSERT INTO labour_records(project_id,vendor,date,start_date,end_date,description,amount,invoice_no,source_sheet) VALUES(?,?,?,?,?,?,?,?,?)',(pid,str(vendor).strip(),date_text(bill_date),date_text(start),date_text(end),'Imported weekly contractor bill / labour summary',amount,None,sheet_name)); labour_count+=1
  c.commit();c.close();wb.close();return jsonify({'ok':True,'project_id':pid,'transactions_imported':count,'receipts_imported':receipt_count,'legacy_labour_bills_imported':labour_count,'note':'Imported M.Sheet expense transactions, Pay Ledger receipts and weekly contractor bill summaries from Hamani / Hamani (2). Detailed historical measurement layouts remain in the original workbook; new invoices can be entered with item-wise measurements in the app.'})
 except Exception as e:return jsonify({'error':f'Could not import workbook: {e}'}),400

@app.route('/api/invoices',methods=['GET','POST'])
@auth
def invoices():
 c=db()
 if request.method=='POST':
  d=request.get_json() or {}; required=['project_id','invoice_no','invoice_date','contractor']
  if any(not d.get(k) for k in required): c.close(); return jsonify({'error':'Project, invoice number, invoice date and contractor are required.'}),400
  try:
   cur=c.execute('INSERT INTO contractor_invoices(project_id,invoice_no,invoice_date,contractor,work_category,period_from,period_to,site_location,description,status,tax_percent,retention_percent,other_deductions,notes,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(int(d['project_id']),d['invoice_no'].strip(),d['invoice_date'],d['contractor'].strip(),d.get('work_category'),d.get('period_from'),d.get('period_to'),d.get('site_location'),d.get('description'),d.get('status','Draft'),float(d.get('tax_percent') or 0),float(d.get('retention_percent') or 0),float(d.get('other_deductions') or 0),d.get('notes'),session.get('user')))
   iid=cur.lastrowid; c.commit(); c.close(); sync_invoice_transaction(iid); audit('create','invoice',iid,d['invoice_no']); return jsonify(invoice_totals(iid)),201
  except sqlite3.IntegrityError: c.close(); return jsonify({'error':'Could not save invoice. Check project and invoice details.'}),400
 pid=request.args.get('project_id'); q='SELECT id FROM contractor_invoices'; args=[]
 if pid:q+=' WHERE project_id=?';args.append(pid)
 q+=' ORDER BY invoice_date DESC,id DESC'; ids=[x['id'] for x in c.execute(q,args)]; c.close(); return jsonify([invoice_totals(i) for i in ids])

@app.route('/api/invoices/<int:iid>',methods=['GET','PUT','DELETE'])
@auth
def invoice_detail(iid):
 c=db(); inv=c.execute('SELECT * FROM contractor_invoices WHERE id=?',(iid,)).fetchone()
 if not inv:c.close();return jsonify({'error':'Invoice not found'}),404
 if request.method=='DELETE':
  if inv['linked_transaction_id']: c.execute('DELETE FROM transactions WHERE id=?',(inv['linked_transaction_id'],))
  c.execute('DELETE FROM contractor_invoices WHERE id=?',(iid,));c.commit();c.close();audit('delete','invoice',iid);return jsonify({'ok':True})
 if request.method=='PUT':
  d=request.get_json() or {}; c.execute('UPDATE contractor_invoices SET invoice_no=?,invoice_date=?,contractor=?,work_category=?,period_from=?,period_to=?,site_location=?,description=?,status=?,tax_percent=?,retention_percent=?,other_deductions=?,notes=? WHERE id=?',(d.get('invoice_no'),d.get('invoice_date'),d.get('contractor'),d.get('work_category'),d.get('period_from'),d.get('period_to'),d.get('site_location'),d.get('description'),d.get('status','Draft'),float(d.get('tax_percent') or 0),float(d.get('retention_percent') or 0),float(d.get('other_deductions') or 0),d.get('notes'),iid));c.commit();c.close();sync_invoice_transaction(iid);audit('update','invoice',iid);return jsonify(invoice_totals(iid))
 c.close();return jsonify(invoice_totals(iid))

@app.route('/api/invoices/<int:iid>/lines',methods=['POST'])
@auth
def invoice_line_add(iid):
 d=request.get_json() or {}; c=db()
 if not c.execute('SELECT id FROM contractor_invoices WHERE id=?',(iid,)).fetchone():c.close();return jsonify({'error':'Invoice not found'}),404
 try:
  nos=float(d.get('nos') or 1); length=float(d.get('length') or 0); width=float(d.get('width') or 0); height=float(d.get('height') or 0); rate=float(d.get('rate') or 0)
  # Quantity defaults to entered manual quantity; otherwise derive from dimensions.
  qty=measured_quantity(nos,length,width,height,d.get('quantity') or 0); amount=qty*rate
  cur=c.execute('INSERT INTO measurement_lines(invoice_id,sl_no,work_date,location,description,nos,length,width,height,quantity,unit,rate,amount,remarks) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(iid,int(d.get('sl_no') or 0),d.get('work_date'),d.get('location'),d.get('description'),nos,length,width,height,qty,d.get('unit') or 'Nos',rate,amount,d.get('remarks')));c.commit(); lid=cur.lastrowid;c.close();sync_invoice_transaction(iid);audit('add','measurement',lid,f'invoice {iid}');return jsonify({'id':lid,'quantity':qty,'amount':amount}),201
 except (ValueError,TypeError):c.close();return jsonify({'error':'Measurement values must be valid numbers.'}),400

@app.route('/api/invoices/<int:iid>/lines/<int:lid>',methods=['PUT','DELETE'])
@auth
def invoice_line_detail(iid,lid):
 c=db(); row=c.execute('SELECT * FROM measurement_lines WHERE id=? AND invoice_id=?',(lid,iid)).fetchone()
 if not row:c.close();return jsonify({'error':'Measurement line not found'}),404
 if request.method=='DELETE':c.execute('DELETE FROM measurement_lines WHERE id=?',(lid,));c.commit();c.close();sync_invoice_transaction(iid);return jsonify({'ok':True})
 d=request.get_json() or {};nos=float(d.get('nos') or 1);l=float(d.get('length') or 0);w=float(d.get('width') or 0);h=float(d.get('height') or 0);rate=float(d.get('rate') or 0);qty=measured_quantity(nos,l,w,h,d.get('quantity') or 0);amount=qty*rate
 c.execute('UPDATE measurement_lines SET sl_no=?,work_date=?,location=?,description=?,nos=?,length=?,width=?,height=?,quantity=?,unit=?,rate=?,amount=?,remarks=? WHERE id=?',(int(d.get('sl_no') or 0),d.get('work_date'),d.get('location'),d.get('description'),nos,l,w,h,qty,d.get('unit') or 'Nos',rate,amount,d.get('remarks'),lid));c.commit();c.close();sync_invoice_transaction(iid);return jsonify({'id':lid,'quantity':qty,'amount':amount})

@app.route('/api/invoices/<int:iid>/payments',methods=['GET','POST'])
@auth
def invoice_payments(iid):
 c=db()
 if not c.execute('SELECT id FROM contractor_invoices WHERE id=?',(iid,)).fetchone():c.close();return jsonify({'error':'Invoice not found'}),404
 if request.method=='POST':
  d=request.get_json() or {}; amount=float(d.get('amount') or 0)
  if amount<=0:c.close();return jsonify({'error':'Payment amount must be greater than zero.'}),400
  cur=c.execute('INSERT INTO invoice_payments(invoice_id,payment_date,amount,mode,reference,paid_to,paid_by,notes) VALUES(?,?,?,?,?,?,?,?)',(iid,d.get('payment_date') or datetime.now().strftime('%Y-%m-%d'),amount,d.get('mode'),d.get('reference'),d.get('paid_to'),d.get('paid_by'),d.get('notes')));c.commit();pid=cur.lastrowid;c.close();sync_invoice_transaction(iid);audit('payment','invoice',iid,amount);return jsonify({'id':pid,'amount':amount}),201
 rows=[rowdict(x) for x in c.execute('SELECT * FROM invoice_payments WHERE invoice_id=? ORDER BY payment_date DESC,id DESC',(iid,))];c.close();return jsonify(rows)

@app.route('/invoice/<int:iid>/print')
@auth
def invoice_print(iid):
 data=invoice_totals(iid)
 if not data:abort(404)
 return render_template('invoice.html',data=data)

@app.route('/api/users',methods=['GET','POST'])
@admin_required
def users():
 c=db()
 if request.method=='POST':
  d=request.get_json() or {}; username=(d.get('username') or '').strip(); password=d.get('password') or ''; role=d.get('role') or 'accountant'
  if not username or len(password)<10 or role not in ('admin','manager','accountant','viewer'):c.close();return jsonify({'error':'Enter a username, password of at least 10 characters and a valid role.'}),400
  try:cur=c.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',(username,generate_password_hash(password),role));c.commit();uid=cur.lastrowid;c.close();audit('create','user',uid,username);return jsonify({'id':uid,'username':username,'role':role}),201
  except sqlite3.IntegrityError:c.close();return jsonify({'error':'Username already exists.'}),409
 out=[{'id':x['id'],'username':x['username'],'role':x['role'],'active':x['active']} for x in c.execute('SELECT * FROM users ORDER BY username')];c.close();return jsonify(out)

@app.route('/api/users/<int:uid>',methods=['PUT'])
@admin_required
def user_update(uid):
 d=request.get_json() or {};c=db();u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
 if not u:c.close();return jsonify({'error':'User not found'}),404
 if uid==1 and not d.get('active',1):c.close();return jsonify({'error':'The primary admin account cannot be disabled.'}),400
 c.execute('UPDATE users SET role=?,active=? WHERE id=?',(d.get('role',u['role']),int(d.get('active',u['active'])),uid))
 if d.get('password'):
  if len(d['password'])<10:c.close();return jsonify({'error':'Password must be at least 10 characters.'}),400
  c.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(d['password']),uid))
 c.commit();c.close();return jsonify({'ok':True})

@app.route('/api/change-password',methods=['POST'])
@auth
def change_password():
 d=request.get_json() or {};c=db();u=c.execute('SELECT * FROM users WHERE username=?',(session['user'],)).fetchone()
 if not check_password_hash(u['password'],d.get('current_password','')):c.close();return jsonify({'error':'Current password is incorrect.'}),400
 if len(d.get('new_password',''))<12:c.close();return jsonify({'error':'New password must be at least 12 characters.'}),400
 c.execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(d['new_password']),u['id']));c.commit();c.close();return jsonify({'ok':True})

@app.route('/manifest.json')
def manifest(): return send_file(os.path.join(BASE,'static','manifest.json'))
@app.route('/sw.js')
def sw(): return send_file(os.path.join(BASE,'static','sw.js'),mimetype='application/javascript')
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
