import sqlite3
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
import werkzeug.security as ws

app = Flask(__name__)
app.secret_key = 'markaz_pro_ultra_secure_2026'

def get_db():
    conn = sqlite3.connect('markaz_pro.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# --- BAZANI BOSHIDAN TO'LIQ SOZLANISHI ---
def init_db():
    conn = get_db()
    c = conn.cursor()
    # Foydalanuvchilar
    c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)')
    # Guruhlar
    c.execute('CREATE TABLE IF NOT EXISTS courses (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, price REAL DEFAULT 0, is_archived INTEGER DEFAULT 0)')
    # O'quvchilar
    c.execute('CREATE TABLE IF NOT EXISTS students (id INTEGER PRIMARY KEY AUTOINCREMENT, course_id INTEGER, name TEXT, phone TEXT)')
    # Sanalar
    c.execute('CREATE TABLE IF NOT EXISTS class_dates (id INTEGER PRIMARY KEY AUTOINCREMENT, course_id INTEGER, date_str TEXT)')
    # Davomat
    c.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, date_id INTEGER, status TEXT, UNIQUE(student_id, date_id))')
    # To'lovlar
    c.execute('CREATE TABLE IF NOT EXISTS payments (id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, amount REAL, p_date TEXT)')
    conn.commit()
    conn.close()

init_db()

# --- AVTORIZATSIYA (LOGIN & REGISTER) ---

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        hashed_p = ws.generate_password_hash(p)
        
        conn = get_db()
        try:
            conn.execute('INSERT INTO users (username, password) VALUES (?, ?)', (u, hashed_p))
            conn.commit()
            flash("Muvaffaqiyatli ro'yxatdan o'tdingiz! Endi kiring.", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("Bu login band, boshqasini tanlang!", "danger")
        finally:
            conn.close()
    return render_template('registratsiya.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form.get('username')
        p = request.form.get('password')
        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (u,)).fetchone()
        conn.close()
        
        if user and ws.check_password_hash(user['password'], p):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('dashboard'))
        else:
            flash("Login yoki parol xato!", "danger")
    return render_template('login.html')

# Loginda foydalanuvchini tekshirish uchun API (Sizning JS uchun)
@app.route('/check_user/<username>')
def check_user(username):
    conn = get_db()
    user = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    return jsonify({'exists': True if user else False})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ASOSIY DASHBOARD ---
@app.route('/')
@app.route('/dashboard')
@app.route('/course/<int:course_id>')
def dashboard(course_id=None):
    if 'user_id' not in session: return redirect(url_for('login'))
    conn = get_db()
    courses = conn.execute('SELECT * FROM courses WHERE user_id = ? AND is_archived = 0', (session['user_id'],)).fetchall()
    
    cv, students, dates, att_data, stats = None, [], [], {}, {}
    
    if course_id:
        cv = conn.execute('SELECT * FROM courses WHERE id=? AND user_id=?', (course_id, session['user_id'])).fetchone()
        if cv:
            students = conn.execute('''SELECT s.*, (SELECT IFNULL(SUM(p.amount), 0) FROM payments p WHERE p.student_id = s.id) as total_paid
                                     FROM students s WHERE s.course_id = ?''', (course_id,)).fetchall()
            dates = conn.execute('SELECT * FROM class_dates WHERE course_id=? ORDER BY id ASC', (course_id,)).fetchall()
            for s in students:
                att_data[s['id']] = {}
                p, total = 0, 0
                rows = conn.execute('SELECT date_id, status FROM attendance WHERE student_id=?', (s['id'],)).fetchall()
                s_att = {r['date_id']: r['status'] for r in rows}
                for d in dates:
                    st = s_att.get(d['id'], '')
                    att_data[s['id']][d['id']] = st
                    if st in ['present', 'absent']:
                        total += 1
                        if st == 'present': p += 1
                stats[s['id']] = int(p/total*100) if total > 0 else 100
    conn.close()
    return render_template('dashboard.html', courses=courses, course_view=cv, students=students, dates=dates, attendance_data=att_data, stats=stats)

# --- FUNKSIYALAR (ADD, EDIT, DELETE) ---

@app.route('/add_course', methods=['POST'])
def add_course():
    conn = get_db()
    conn.execute('INSERT INTO courses (user_id, name, price) VALUES (?, ?, ?)', (session['user_id'], request.form['name'], request.form['price']))
    conn.commit(); conn.close()
    return redirect(url_for('dashboard'))

@app.route('/add_student', methods=['POST'])
def add_student():
    cid = request.form['course_id']
    conn = get_db()
    conn.execute('INSERT INTO students (course_id, name, phone) VALUES (?, ?, ?)', (cid, request.form['name'], request.form['phone']))
    conn.commit(); conn.close()
    return redirect(url_for('dashboard', course_id=cid))

@app.route('/add_range_dates', methods=['POST'])
def add_range_dates():
    cid = request.form['course_id']
    start = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
    end = datetime.strptime(request.form['end_date'], '%Y-%m-%d')
    conn = get_db()
    curr = start
    while curr <= end:
        if curr.weekday() != 6:
            conn.execute('INSERT INTO class_dates (course_id, date_str) VALUES (?, ?)', (cid, curr.strftime('%d-%b')))
        curr += timedelta(days=1)
    conn.commit(); conn.close()
    return redirect(url_for('dashboard', course_id=cid))

@app.route('/update_attendance', methods=['POST'])
def update_att():
    d = request.json
    conn = get_db()
    conn.execute('''INSERT INTO attendance (student_id, date_id, status) VALUES (?, ?, ?) 
                    ON CONFLICT(student_id, date_id) DO UPDATE SET status=excluded.status''', 
                 (d['student_id'], d['date_id'], d['status']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'}) # MUHIM: Faqat JSON qaytishi shart

@app.route('/add_payment', methods=['POST'])
def add_payment():
    d = request.json
    conn = get_db()
    conn.execute('INSERT INTO payments (student_id, amount, p_date) VALUES (?, ?, ?)',
                 (d['student_id'], d['amount'], datetime.now().strftime('%Y-%m-%d')))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

# Tahrirlash va Arxivlash API lari
@app.route('/edit_course', methods=['POST'])
def edit_course():
    d = request.json
    conn = get_db()
    conn.execute('UPDATE courses SET name=?, price=? WHERE id=?', (d['name'], d['price'], d['id']))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/archive_course', methods=['POST'])
def archive_course():
    d = request.json
    conn = get_db()
    conn.execute('UPDATE courses SET is_archived=1 WHERE id=?', (d['id'],))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/delete_course', methods=['POST'])
def delete_course():
    d = request.json
    conn = get_db()
    conn.execute('DELETE FROM courses WHERE id=?', (d['id'],))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/delete_date', methods=['POST'])
def delete_date():
    d = request.json
    conn = get_db()
    conn.execute('DELETE FROM class_dates WHERE id=?', (d['id'],))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)