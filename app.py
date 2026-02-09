import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import werkzeug.security as ws

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'markaz_pro_ultra_secure_2026')

# --- BAZA BILAN ULANISH ---
def get_db():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise Exception("DATABASE_URL topilmadi! Render'da Environment Variable qo'shing.")
    
    # Render va Neon uchun postgres:// ni postgresql:// ga to'g'rilash
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    
    conn = psycopg2.connect(db_url)
    return conn

# --- BAZANI BOSHIDAN TO'LIQ SOZLANISHI ---
def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT UNIQUE, password TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS courses (id SERIAL PRIMARY KEY, user_id INTEGER, name TEXT, price REAL DEFAULT 0, is_archived INTEGER DEFAULT 0)')
    c.execute('CREATE TABLE IF NOT EXISTS students (id SERIAL PRIMARY KEY, course_id INTEGER, name TEXT, phone TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS class_dates (id SERIAL PRIMARY KEY, course_id INTEGER, date_str TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS attendance (id SERIAL PRIMARY KEY, student_id INTEGER, date_id INTEGER, status TEXT, UNIQUE(student_id, date_id))')
    c.execute('CREATE TABLE IF NOT EXISTS payments (id SERIAL PRIMARY KEY, student_id INTEGER, amount REAL, p_date TEXT)')
    conn.commit()
    c.close()
    conn.close()

with app.app_context():
    init_db()

# --- AVTORIZATSIYA ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        hashed_p = ws.generate_password_hash(p)
        conn = get_db(); c = conn.cursor()
        try:
            c.execute('INSERT INTO users (username, password) VALUES (%s, %s)', (u, hashed_p))
            conn.commit()
            flash("Muvaffaqiyatli ro'yxatdan o'tdingiz!", "success")
            return redirect(url_for('login'))
        except:
            flash("Bu login band!", "danger")
        finally:
            c.close(); conn.close()
    return render_template('registratsiya.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        conn = get_db(); c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute('SELECT * FROM users WHERE username = %s', (u,))
        user = c.fetchone()
        c.close(); conn.close()
        if user and ws.check_password_hash(user['password'], p):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        else:
            flash("Login yoki parol xato!", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- DASHBOARD ---
@app.route('/')
@app.route('/dashboard')
@app.route('/course/<int:course_id>')
def dashboard(course_id=None):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db(); c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute('SELECT * FROM courses WHERE user_id = %s AND is_archived = 0', (session['user_id'],))
    courses = c.fetchall()
    cv, students, dates, att_data, stats = None, [], [], {}, {}
    if course_id:
        c.execute('SELECT * FROM courses WHERE id=%s AND user_id=%s', (course_id, session['user_id']))
        cv = c.fetchone()
        if cv:
            c.execute('''SELECT s.*, (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.student_id = s.id) as total_paid
                         FROM students s WHERE s.course_id = %s''', (course_id,))
            students = c.fetchall()
            c.execute('SELECT * FROM class_dates WHERE course_id=%s ORDER BY id ASC', (course_id,))
            dates = c.fetchall()
            for s in students:
                att_data[s['id']] = {}
                p, total = 0, 0
                c.execute('SELECT date_id, status FROM attendance WHERE student_id=%s', (s['id'],))
                rows = c.fetchall()
                s_att = {r['date_id']: r['status'] for r in rows}
                for d in dates:
                    st = s_att.get(d['id'], '')
                    att_data[s['id']][d['id']] = st
                    if st in ['present', 'absent']:
                        total += 1
                        if st == 'present': p += 1
                stats[s['id']] = int(p/total*100) if total > 0 else 100
    c.close(); conn.close()
    return render_template('dashboard.html', courses=courses, course_view=cv, students=students, dates=dates, attendance_data=att_data, stats=stats)

# --- FUNKSIYALAR ---
@app.route('/add_course', methods=['POST'])
def add_course():
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO courses (user_id, name, price) VALUES (%s, %s, %s)', (session['user_id'], request.form['name'], request.form['price']))
    conn.commit(); c.close(); conn.close()
    return redirect(url_for('dashboard'))

@app.route('/add_student', methods=['POST'])
def add_student():
    cid = request.form['course_id']
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO students (course_id, name, phone) VALUES (%s, %s, %s)', (cid, request.form['name'], request.form['phone']))
    conn.commit(); c.close(); conn.close()
    return redirect(url_for('dashboard', course_id=cid))

@app.route('/add_range_dates', methods=['POST'])
def add_range_dates():
    cid = request.form['course_id']
    start = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
    end = datetime.strptime(request.form['end_date'], '%Y-%m-%d')
    conn = get_db(); c = conn.cursor()
    curr = start
    while curr <= end:
        if curr.weekday() != 6:
            c.execute('INSERT INTO class_dates (course_id, date_str) VALUES (%s, %s)', (cid, curr.strftime('%d-%b')))
        curr += timedelta(days=1)
    conn.commit(); c.close(); conn.close()
    return redirect(url_for('dashboard', course_id=cid))

@app.route('/update_attendance', methods=['POST'])
def update_att():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('''INSERT INTO attendance (student_id, date_id, status) VALUES (%s, %s, %s) 
                 ON CONFLICT(student_id, date_id) DO UPDATE SET status=EXCLUDED.status''', 
              (d['student_id'], d['date_id'], d['status']))
    conn.commit(); c.close(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/add_payment', methods=['POST'])
def add_payment():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO payments (student_id, amount, p_date) VALUES (%s, %s, %s)',
              (d['student_id'], d['amount'], datetime.now().strftime('%Y-%m-%d')))
    conn.commit(); c.close(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/archive_course', methods=['POST'])
def archive_course():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('UPDATE courses SET is_archived=1 WHERE id=%s', (d['id'],))
    conn.commit(); c.close(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/delete_date', methods=['POST'])
def delete_date():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('DELETE FROM class_dates WHERE id=%s', (d['id'],))
    conn.commit(); c.close(); conn.close()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True)
