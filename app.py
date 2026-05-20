import os
import sys
import subprocess
import hashlib
import sqlite3
import json
import calendar as cal
import weasyprint
from datetime import datetime, date
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, jsonify, abort,
                   make_response, Response)
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE  = os.path.join(BASE_DIR, 'school.db')
UPLOAD_FOLDER  = os.path.join(BASE_DIR, 'static', 'uploads')
REPORTS_FOLDER = os.path.join(BASE_DIR, 'static', 'reports')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif'}

#suppressing gtk messages
os.environ["GIO_USE_VFS"] = "local"
os.environ["G_MESSAGES_DEBUG"] = "none"


app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
app.secret_key = os.environ.get('SESSION_SECRET', 'hclv-sms-jinja-2024')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER,  exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)

HOUSES = ['Hesse', 'Safari', 'Lwanga', 'McD', 'Kabalega', 'Lutaaya']

ALEVEL_SUBSIDIARIES = [
    {'name': 'Subsidiary Mathematics',      'code': 'SM'},
    {'name': 'Subsidiary Computer Studies', 'code': 'ICT'},
]

ADMIN_PERMISSION_OPTIONS = {
    'view_admins':    'View admin users',
    'edit_admins':    'Create / edit admin users',
    'view_students':  'View students',
    'edit_students':  'Edit students',
    'view_teachers':  'View teachers',
    'edit_teachers':  'Edit teachers',
    'view_subjects':  'View subjects',
    'edit_subjects':  'Edit subjects',
    'view_classes':   'View classes',
    'edit_classes':   'Edit classes',
    'view_marks':     'View marks',
    'view_reports':   'View reports',
}


# ─── database helpers ─────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_setting(key, default=''):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def check_pw(pw, hashed):
    return hash_pw(pw) == hashed


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def subject_code(name):
    if not name:
        return ''
    if 'Subsidiary Mathematics' in name:
        return 'SM'
    if 'Subsidiary Computer Studies' in name:
        return 'ICT'
    if 'General Paper' in name:
        return 'GP'
    cleaned = name.replace('&', ' ').replace('(', ' ').replace(')', ' ')
    return ''.join([part[0].upper() for part in cleaned.split() if part])


def build_full_name(first_name, last_name, other_names=''):
    first_name = (first_name or '').strip()
    last_name = (last_name or '').strip()
    other_names = (other_names or '').strip()
    parts = [first_name]
    if other_names:
        parts.append(other_names)
    if last_name:
        parts.append(last_name)
    return ' '.join([p for p in parts if p])


def split_full_name(full_name):
    full_name = (full_name or '').strip()
    parts = full_name.split()
    if not parts:
        return '', '', ''
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else parts[0]
    other_names = ' '.join(parts[1:-1]) if len(parts) > 2 else ''
    return first_name, last_name, other_names


# ─── grade helpers ────────────────────────────────────────────────────────────

GRADE_TABLE = [
    (80, 'A', 'EXCEPTIONAL ACHIEVEMENT',  5),
    (70, 'B', 'OUTSTANDING PERFORMANCE',  4),
    (60, 'C', 'SATISFACTORY PERFORMANCE', 3),
    (50, 'D', 'BASIC UNDERSTANDING',      2),
    (0,  'E', 'ELEMENTARY UNDERSTANDING', 1),
]


def get_grade(score):
    if score is None:
        return 'X', 'NOT TAKEN', 7
    for threshold, grade, desc, pts in GRADE_TABLE:
        if score >= threshold:
            return grade, desc, pts
    return 'O', 'FAIL', 6


def calc_final(bot, mt, eot):
    scores = [s for s in (bot, mt) if s is not None]
    mot = sum(scores) / len(scores) if scores else None
    if mot is None and eot is None:
        return None, None
    if mot is None:
        return None, eot
    if eot is None:
        return mot, mot
    return mot, (mot * 0.5 + eot * 0.5)


def generate_student_no(conn):
    now    = datetime.now()
    yyyymm = now.strftime('%Y%m')
    hhmm   = now.strftime('%H%M')
    count  = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    index  = f"{count + 1:04d}"
    return f"ST-{index}-{yyyymm}-{hhmm}"


# ─── decorators ───────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*a, **kw)
    return deco


def admin_required(f):
    @wraps(f)
    def deco(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Admin access required.', 'danger')
            return redirect(url_for('home'))
        return f(*a, **kw)
    return deco


def teacher_or_admin(f):
    @wraps(f)
    def deco(*a, **kw):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') not in ('admin', 'teacher'):
            flash('Access denied.', 'danger')
            return redirect(url_for('home'))
        return f(*a, **kw)
    return deco


def parse_permissions(value):
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def current_user_permissions():
    perms = session.get('permissions')
    if perms is None:
        return []
    return perms if isinstance(perms, list) else []


def has_permission(permission):
    if session.get('role') != 'admin':
        return False
    perms = current_user_permissions()
    return not perms or permission in perms


def permission_required(permission):
    def decorator(f):
        @wraps(f)
        def deco(*a, **kw):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if session.get('role') != 'admin':
                flash('Admin access required.', 'danger')
                return redirect(url_for('home'))
            if not has_permission(permission):
                flash('You do not have permission to access that page.', 'danger')
                dest = 'admin_dashboard' if session.get('role') == 'admin' else 'home'
                return redirect(url_for(dest))
            return f(*a, **kw)
        return deco
    return decorator


# ─── subject / offered-paper helpers ─────────────────────────────────────────

def get_student_offered_subjects(conn, student_id, class_name, level):
    """Return a list of subject-paper dicts the student actually offers."""
    student = conn.execute(
        "SELECT combination FROM students WHERE id=?", (student_id,)
    ).fetchone()
    combo = student['combination'] if student else ''
    all_subjects = []

    if level == 'alevel':
        principal_part  = combo.split('/')[0] if '/' in combo else combo
        principal_chars = [c for c in principal_part.strip().upper() if c.isalpha()]

        principals = conn.execute("""
            SELECT id, name, code, level FROM subjects
            WHERE level='A'
              AND name NOT IN ('General Paper','Subsidiary Mathematics','Subsidiary Computer Studies')
            ORDER BY name
        """).fetchall()

        letter_map = {}
        for s in principals:
            ch = s['name'].strip()[0].upper()
            letter_map.setdefault(ch, []).append(s)

        matched_ids = set()
        for char in principal_chars:
            for sub in letter_map.get(char, []):
                if sub['id'] not in matched_ids:
                    all_subjects.append(sub)
                    matched_ids.add(sub['id'])
                    break

        gp = conn.execute(
            "SELECT id,name,code,level FROM subjects WHERE name='General Paper' AND level='A'"
        ).fetchone()
        if gp:
            all_subjects.append(gp)

        matched_names = [s['name'] for s in all_subjects]
        has_math = any('Mathematics' in n for n in matched_names)
        has_econ = any('Economics' in n for n in matched_names)

        if has_math:
            sub_name = 'Subsidiary Computer Studies'
        elif has_econ:
            sub_name = 'Subsidiary Mathematics'
        else:
            parts    = combo.split('/')
            sub_code = parts[1].strip().upper() if len(parts) > 1 else 'SM'
            sub_name = 'Subsidiary Mathematics' if sub_code == 'SM' else 'Subsidiary Computer Studies'

        sub = conn.execute(
            "SELECT id,name,code,level FROM subjects WHERE name=? AND level='A'", (sub_name,)
        ).fetchone()
        if sub:
            all_subjects.append(sub)

    else:  # O-Level
        compulsory = conn.execute("""
            SELECT id, name, code, level FROM subjects
            WHERE level='O' AND name IN (
                'English Language','Mathematics','Biology','Chemistry','Physics',
                'Geography','History & Political Education','Kiswahili',
                'Entrepreneurship Education','Physical Education','CRE', 'Chinese'
            )
            ORDER BY name
        """).fetchall()
        all_subjects.extend(compulsory)

        if class_name in ('S.3', 'S.4') and '(' in combo and ')' in combo:
            elec_letter = combo.split('(')[1].split(')')[0].strip().upper()
            elec = conn.execute("""
                SELECT id, name, code, level FROM subjects
                WHERE level='O'
                  AND UPPER(substr(name,1,1))=?
                  AND name NOT IN (
                      'English Language','Mathematics','Biology','Chemistry','Physics',
                      'Geography','History & Political Education','Kiswahili',
                      'Entrepreneurship Education','Physical Education','CRE', 'Chinese'
                  )
                LIMIT 1
            """, (elec_letter,)).fetchone()
            if elec:
                all_subjects.append(elec)

    result = []
    for subj in all_subjects:
        papers = conn.execute(
            "SELECT id, paper_number, paper_code FROM subject_papers "
            "WHERE subject_id=? ORDER BY paper_number",
            (subj['id'],)
        ).fetchall()
        for p in papers:
            result.append({
                'sp_id':        p['id'],
                'paper_code':   p['paper_code'],
                'paper_number': p['paper_number'],
                'subject_name': subj['name'],
                'subject_code': subj['code'],
                'level':        subj['level'],
            })
    return result


def student_offers_subject(student_combo, subject_name, level, class_name):
    if not student_combo:
        return level == 'olevel'
    if level == 'alevel':
        principal_part  = student_combo.split('/')[0] if '/' in student_combo else student_combo
        subject_letter  = subject_name.strip()[0].upper()
        return subject_letter in principal_part
    else:
        if class_name in ('S.1', 'S.2'):
            return True
        if class_name in ('S.3', 'S.4'):
            if '(' in student_combo and ')' in student_combo:
                elective_letter = student_combo.split('(')[1].split(')')[0].strip().upper()
                return elective_letter == subject_name.strip()[0].upper()
            return True
        return True


def _get_term_averages(conn, student_id, term, year):
    from collections import defaultdict
    rows = conn.execute(
        "SELECT sp.id as sp_id, sp.paper_code, m.exam_type, m.score "
        "FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id "
        "WHERE m.student_id=? AND m.term=? AND m.year=?",
        (student_id, term, year)
    ).fetchall()
    by_paper = defaultdict(dict)
    for r in rows:
        by_paper[r['paper_code']][r['exam_type']] = r['score']
    result = {}
    for code, exams in by_paper.items():
        _, final = calc_final(exams.get('BOT'), exams.get('MT'), exams.get('EOT'))
        if final is not None:
            result[code] = final
    return result


# ─── db initialisation ────────────────────────────────────────────────────────

OLEVEL_SUBJECTS = [
    # --- Compulsory Core Subjects ---
    ('English Language',                          '112', [('1','112/1')]),
    ('Mathematics',                               '456', [('1','456/1')]),
    ('History & Political Education',             '241', [('1','241/1')]),
    ('Geography',                                 '273', [('1','273/1')]),
    ('Physics',                                   '535', [('1','535/1'),('2','535/2')]),
    ('Chemistry',                                 '545', [('1','545/1'),('2','545/2')]),
    ('Biology',                                   '553', [('1','553/1'),('2','553/2')]),
    ('Kiswahili',                                 '336', [('1','336/1')]),
    ('Physical Education',                        '555', [('1','555/1'),('2','555/2')]), 
    ('Entrepreneurship Education',                '845', [('1','845/1')]),
    ('CRE (Christian Religious Ed.)',             '223', [('1','223/1')]),
    # ('IRE (Islamic Religious Ed.)',               '225', [('1','225/1')]), # Added Core Option
    
    # --- Pre-Vocational & Language Electives ---
    ('Information & Communications Technology (ICT)', '840', [('1','840/1'),('2','840/2')]),
    ('Agriculture',                               '527', [('1','527/1'),('2','527/2')]),
    ('Art and Design',                            '612', [('1','612/1'),('2','612/2')]), 
    ('Performing Arts (Music)',                   '621', [('1','621/1'),('2','621/2')]), 
    ('Nutrition & Food Technology',               '662', [('1','662/1'),('2','662/2')]),
    ('Literature in English',                     '208', [('1','208/1')]), 
    ('Chinese',                                   '396', [('1','396/1'),('2','396/2')]),
    
    # --- Technical Component (Custom 2-Paper Track for School Building Path) ---
    ('Technology and Design',                     '745', [('1','745/1'),('2','745/2'),(3,'745/3')]), 
]

ALEVEL_SUBJECTS = [
    # --- Mandatory Subsidiaries ---
    ('General Paper',                 'S101', [('1','S101/1')]),
    ('Subsidiary Mathematics',        'S475', [('1','S475/1')]),
    ('Subsidiary Computer Studies',   'S850', [('1','S850/1')]),
    
    # --- Humanities, Arts & Religious Studies ---
    ('History',                       'P210', [('1','P210/1'),('2','P210/2')]),
    ('Geography',                     'P250', [('1','P250/1'),('2','P250/2')]),
    ('Economics',                     'P220', [('1','P220/1'),('2','P220/2')]),
    ('Entrepreneurship Education',    'P230', [('1','P230/1'),('2','P230/2')]),
    ('Divinity',                      'P245', [('1','P245/1'),('2','P245/2')]),
    ('IRE (Islamic Religious Ed.)',   'P235', [('1','P235/1'),('2','P235/2')]), 
    
    # --- Core Pure Sciences ---
    ('Mathematics',                   'P425', [('1','P425/1'),('2','P425/2')]),
    ('Physics',                       'P510', [('1','P510/1'),('2','P510/2')]),
    ('Chemistry',                     'P525', [('1','P525/1'),('2','P525/2')]),
    ('Biology',                       'P530', [('1','P530/1'),('2','P530/2')]),
    ('Agriculture',                   'P515', [('1','P515/1'),('2','P515/2')]),
    
    # --- Languages & Literature ---
    ('Literature in English',         'P310', [('1','P310/1'),('2','P310/2'),('3','P310/3')]), 
    ('Kiswahili',                     'P320', [('1','P320/1'),('2','P320/2')]),
    ('Chinese',                       'P372', [('1','P372/1'),('2','P372/2')]),
    
    # --- Cultural, Vocational & Separate Technical Drawing Channels ---
    ('Fine Art',                      'P615', [('1','P615/1'),('2','P615/2')]),
    ('Nutrition & Food Technology',   'P640', [('1','P640/1'),('2','P640/2'),('3','P640/3')]),
    
    # BOTH Technical Drawing Tracks Split for Data Integrity
    ('Technical Drawing (Mechanical)',  'P710', [('1','P710/1'),('2','P710/2')]), 
    ('Technical Drawing (Building)',    'P720', [('1','P720/1'),('2','P720/2')]),
]



def init_db():
    conn = get_db()
    c    = conn.cursor()

    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            role        TEXT NOT NULL,
            full_name   TEXT NOT NULL,
            email       TEXT DEFAULT '',
            phone       TEXT DEFAULT '',
            profile_pic TEXT DEFAULT 'default.png',
            permissions TEXT DEFAULT '{}',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS classes (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT NOT NULL,
            stream           TEXT DEFAULT '',
            class_teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            name  TEXT NOT NULL,
            code  TEXT DEFAULT '',
            level TEXT DEFAULT 'O'
        );
        CREATE TABLE IF NOT EXISTS subject_papers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id   INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            paper_number TEXT NOT NULL,
            paper_code   TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS students (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER REFERENCES users(id) ON DELETE CASCADE,
            student_no      TEXT UNIQUE NOT NULL,
            school_pay_no   TEXT DEFAULT '',
            class_id        INTEGER REFERENCES classes(id) ON DELETE SET NULL,
            combination     TEXT DEFAULT '',
            house           TEXT DEFAULT '',
            uce_results     TEXT DEFAULT '',
            enrollment_year INTEGER,
            gender          TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS teachers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER REFERENCES users(id) ON DELETE CASCADE,
            teacher_id    TEXT UNIQUE NOT NULL,
            qualification TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS teacher_assignments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id       INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            subject_paper_id INTEGER NOT NULL REFERENCES subject_papers(id) ON DELETE CASCADE,
            class_id         INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            year             INTEGER NOT NULL,
            UNIQUE(teacher_id, subject_paper_id, class_id, year)
        );
        CREATE TABLE IF NOT EXISTS marks (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id       INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            subject_paper_id INTEGER NOT NULL REFERENCES subject_papers(id) ON DELETE CASCADE,
            term             INTEGER NOT NULL,
            year             INTEGER NOT NULL,
            exam_type        TEXT NOT NULL,
            score            REAL,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, subject_paper_id, term, year, exam_type)
        );
        CREATE TABLE IF NOT EXISTS subject_comments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id       INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            subject_paper_id INTEGER NOT NULL REFERENCES subject_papers(id) ON DELETE CASCADE,
            term             INTEGER NOT NULL,
            year             INTEGER NOT NULL,
            comment          TEXT DEFAULT '',
            teacher_id       INTEGER REFERENCES teachers(id),
            UNIQUE(student_id, subject_paper_id, term, year)
        );
        CREATE TABLE IF NOT EXISTS ct_comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            term       INTEGER NOT NULL,
            year       INTEGER NOT NULL,
            comment    TEXT DEFAULT '',
            teacher_id INTEGER REFERENCES teachers(id),
            UNIQUE(student_id, term, year)
        );
        CREATE TABLE IF NOT EXISTS ht_comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            term       INTEGER NOT NULL,
            year       INTEGER NOT NULL,
            comment    TEXT DEFAULT '',
            UNIQUE(student_id, term, year)
        );
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            event_date  DATE NOT NULL,
            description TEXT DEFAULT '',
            event_type  TEXT DEFAULT 'general',
            created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reports (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id   INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            term         INTEGER NOT NULL,
            year         INTEGER NOT NULL,
            report_type  TEXT DEFAULT 'EOT',
            file_path    TEXT,
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
    ''')

    # ── safe migrations for existing databases ──
    for col, defval in [('house', "''"), ('school_pay_no', "''"), ('gender', "''")]:
        try:
            c.execute(f"ALTER TABLE students ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass
    try:
        c.execute("ALTER TABLE reports ADD COLUMN report_type TEXT DEFAULT 'EOT'")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN permissions TEXT DEFAULT '{}'")
    except Exception:
        pass
    for col, defval in [('first_name', "''"), ('last_name', "''"), ('other_names', "''")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT {defval}")
        except Exception:
            pass
    try:
        users = c.execute("SELECT id, full_name, first_name, last_name, other_names FROM users").fetchall()
        for user in users:
            if (not user['first_name'] or not user['last_name']) and user['full_name']:
                first, last, other = split_full_name(user['full_name'])
                c.execute(
                    "UPDATE users SET first_name=?, last_name=?, other_names=? WHERE id=?",
                    (first, last, other, user['id'])
                )
    except Exception:
        pass
    try:
        c.execute("UPDATE students SET house = dormitory "
                  "WHERE (house IS NULL OR house='') "
                  "AND dormitory IS NOT NULL AND dormitory != ''")
    except Exception:
        pass

    # ── default admin ──
    if not c.execute("SELECT id FROM users WHERE username='admin'").fetchone():
        c.execute("INSERT INTO users (username,password,role,full_name) VALUES (?,?,?,?)",
                  ('admin', hash_pw('admin123'), 'admin', 'System Administrator'))

    # ── classes ──
    if c.execute("SELECT COUNT(*) FROM classes").fetchone()[0] == 0:
        class_streams = {
            'S.1': ['A','B','C','D','E'],
            'S.2': ['A','B','C','D'],
            'S.3': ['A','B','C','D'],
            'S.4': ['A','B','C','D','E'],
            'S.5': ['S','A'],
            'S.6': ['S','A'],
        }
        for cname in ['S.1','S.2','S.3','S.4','S.5','S.6']:
            for stream in class_streams[cname]:
                c.execute("INSERT INTO classes (name, stream) VALUES (?,?)", (cname, stream))

    # ── subjects ──
    if c.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0:
        for name, code, papers in OLEVEL_SUBJECTS:
            c.execute("INSERT INTO subjects (name,code,level) VALUES (?,?,'O')", (name, code))
            sid = c.lastrowid
            for pnum, pcode in papers:
                c.execute("INSERT INTO subject_papers (subject_id,paper_number,paper_code) VALUES (?,?,?)",
                          (sid, pnum, pcode))
        for name, code, papers in ALEVEL_SUBJECTS:
            c.execute("INSERT INTO subjects (name,code,level) VALUES (?,?,'A')", (name, code))
            sid = c.lastrowid
            for pnum, pcode in papers:
                c.execute("INSERT INTO subject_papers (subject_id,paper_number,paper_code) VALUES (?,?,?)",
                          (sid, pnum, pcode))

    # ensure Technical Drawing exists for older DBs
    if not c.execute("SELECT id FROM subjects WHERE name='Technical Drawing (Building)' AND level='A'").fetchone():
        c.execute("INSERT INTO subjects (name,code,level) VALUES (?,?,'A')", ('Technical Drawing (Building)', 'P720'))
        sid = c.lastrowid
        for pnum, pcode in [('1','P720/1'), ('2','P720/2')]:
            c.execute("INSERT INTO subject_papers (subject_id,paper_number,paper_code) VALUES (?,?,?)",
                      (sid, pnum, pcode))

    conn.commit()
    conn.close()


# ─── context processor ────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {
        'current_year': datetime.now().year,
        'school_name':  'Holy Cross Lake View SSS',
        'houses':       HOUSES,
    }


# ─── auth routes ──────────────────────────────────────────────────────────────

@app.route('/')
def home():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    role = session.get('role')
    if role == 'admin':
        return redirect(url_for('admin_dashboard'))
    if role == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    return redirect(url_for('student_dashboard'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_pw(password, user['password']):
            session['user_id']     = user['id']
            session['username']    = user['username']
            session['role']        = user['role']
            session['full_name']   = user['full_name']
            session['first_name']  = user['first_name'] or user['full_name'].split()[0] if user['full_name'] else ''
            session['profile_pic'] = user['profile_pic']
            session['permissions'] = parse_permissions(user['permissions'])
            flash(f'Welcome back, {user["full_name"]}!', 'success')
            return redirect(url_for('home'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ─── admin: dashboard ─────────────────────────────────────────────────────────

@app.route('/admin/results/olevel', methods=['GET', 'POST'])
@admin_required
def admin_olevel_master_results():
    if request.method == 'GET':
        return render_template('admin/master_olevel_results.html',
                               current_year=datetime.now().year,
                               term=1, year=datetime.now().year,
                               sheets=[], streams=HOUSES)

    term          = int(request.form.get('term', 1))
    year          = int(request.form.get('year', datetime.now().year))
    filter_stream = request.form.get('stream', '').strip()
    conn          = get_db()

    # 🔑 Exact O-Level subject order matching your OLEVEL.docx
    OLEVEL_SUBJECT_ORDER = [
        'Physical Education',
        'Information & Communications Technology (ICT)',
        'English Language',
        'Mathematics',
        'Physics',
        'Biology',
        'Chemistry',
        'History & Political Education',
        'Geography',
        'CRE',
        'Agriculture',
        'Kiswahili',
        'Entrepreneurship Education',
        'Literature in English',
        'Chinese',
        'Fine Art',
        'Nutrition & Food Technology'
    ]

    classes = conn.execute("""
        SELECT c.id, c.name, c.stream, COUNT(s.id) as student_count
        FROM classes c LEFT JOIN students s ON c.id=s.class_id
        WHERE c.name IN ('S.1','S.2','S.3','S.4')
        GROUP BY c.id HAVING student_count > 0 ORDER BY c.name, c.stream
    """).fetchall()

    sheets = []
    for c in classes:
        if filter_stream and c['stream'] != filter_stream:
            continue
        class_name = f"{c['name']}/{c['stream']}" if c['stream'] else c['name']

        # 🔑 Explicit alias prevents column collision & guarantees raw DB value
        students = conn.execute(
            "SELECT s.id, u.full_name, s.combination AS db_combo, s.gender "
            "FROM students s JOIN users u ON s.user_id=u.id "
            "WHERE s.class_id=? ORDER BY u.full_name",
            (c['id'],)
        ).fetchall()
        if not students: continue

        s_ids = [s['id'] for s in students]
        placeholder = ','.join(['?'] * len(s_ids))
        marks = conn.execute(f"""
            SELECT m.student_id, s.name as subject_name, m.exam_type, m.score
            FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id
            JOIN subjects s ON sp.subject_id=s.id
            WHERE m.student_id IN ({placeholder}) AND m.term=? AND m.year=?
        """, s_ids + [term, year]).fetchall()

        student_marks = {sid: {} for sid in s_ids}
        for m in marks:
            student_marks.setdefault(m['student_id'], {}).setdefault(m['subject_name'], {})[m['exam_type']] = m['score']

        results = []
        for s in students:
            exams = student_marks.get(s['id'], {})
            subject_grades = {}
            total_score = 0
            valid_count = 0

            for sub_name in OLEVEL_SUBJECT_ORDER:
                sub_exams = exams.get(sub_name, {})
                bot = sub_exams.get('BOT')
                mt  = sub_exams.get('MT')
                eot = sub_exams.get('EOT')
                _, final = calc_final(bot, mt, eot)
                grade, _, pts = get_grade(final)
                
                subject_grades[sub_name] = grade if final is not None else '-'

                if final is not None:
                    total_score += final  # Sum of final scores for TOTAL SCORE column
                    valid_count += 1

            # Calculate averages & descriptor
            avg_score = round(total_score / valid_count, 2) if valid_count > 0 else 0
            descriptor = 'NOT TAKEN'
            if avg_score >= 80: descriptor = 'EXCEPTIONAL'
            elif avg_score >= 70: descriptor = 'OUTSTANDING'
            elif avg_score >= 60: descriptor = 'SATISFACTORY'
            elif avg_score >= 50: descriptor = 'BASIC'
            elif avg_score > 0: descriptor = 'ELEMENTARY'

            results.append({
                'name': s['full_name'],
                'gender': s['gender'] if s['gender'] else ('M' if any(x in s['full_name'].upper() for x in ['MR.','MASTER']) else 'F'),
                # 🔑 STRICT DB BINDING: No fallback masking, exact value shown
                'combination': s['db_combo'] if s['db_combo'] and s['db_combo'].strip() else '—',
                'grades': subject_grades,
                'total_score': round(total_score, 2),
                'average_score': avg_score,
                'descriptor': descriptor,
                'stream_rank': 0,  # Assigned later
                'class_rank': 0
            })

        # Sort by average score DESC (higher = better for O-Level), then name
        results.sort(key=lambda x: (-x['average_score'], x['name']))
        rank = 1
        for i, res in enumerate(results):
            if i > 0 and res['average_score'] != results[i-1]['average_score']:
                rank = i + 1
            res['stream_rank'] = res['class_rank'] = rank

        sheets.append({
            'class_name': class_name,
            'students': results,
            'stream': c['stream'],
            'subjects': OLEVEL_SUBJECT_ORDER,
        })

    conn.close()
    return render_template('admin/master_olevel_results.html',
                           sheets=sheets, term=term, year=year, streams=HOUSES)

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db()
    stats = {
        'students': conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
        'teachers': conn.execute("SELECT COUNT(*) FROM teachers").fetchone()[0],
        'classes':  conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0],
        'subjects': conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0],
    }
    today   = date.today().isoformat()
    upcoming = conn.execute(
        "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date LIMIT 5", (today,)
    ).fetchall()
    recent_students = conn.execute(
        "SELECT u.full_name, s.student_no, c.name as class_name, c.stream, u.created_at "
        "FROM students s JOIN users u ON s.user_id=u.id "
        "LEFT JOIN classes c ON s.class_id=c.id ORDER BY u.created_at DESC LIMIT 6"
    ).fetchall()
    conn.close()
    now       = datetime.now()
    month_cal = cal.monthcalendar(now.year, now.month)
    next_term_date = get_setting('next_term_start_date', '')
    return render_template('admin/dashboard.html',
                           stats=stats, upcoming=upcoming,
                           recent_students=recent_students,
                           month_cal=month_cal, now=now,
                           next_term_date=next_term_date)


@app.route('/admin/settings/save-next-term', methods=['POST'])
@admin_required
def admin_save_next_term():
    date_val = request.form.get('next_term_date', '').strip()
    set_setting('next_term_start_date', date_val)
    flash('Next term date updated successfully.', 'success')
    return redirect(url_for('admin_dashboard'))


# ─── admin: students ──────────────────────────────────────────────────────────

@app.route('/admin/students')
@permission_required('view_students')
def admin_students():
    q    = request.args.get('q', '')
    cls  = request.args.get('class_id', '')
    sort = request.args.get('sort', 'class')
    conn = get_db()
    sql  = ("SELECT s.*, u.full_name, u.email, u.username, u.profile_pic, "
            "c.name as class_name, c.stream "
            "FROM students s JOIN users u ON s.user_id=u.id "
            "LEFT JOIN classes c ON s.class_id=c.id WHERE 1=1")
    params = []
    if q:
        sql += " AND (u.full_name LIKE ? OR s.student_no LIKE ?)"
        params += [f'%{q}%', f'%{q}%']
    if cls:
        sql += " AND s.class_id=?"
        params.append(cls)
    if sort == 'name_asc':
        sql += " ORDER BY u.full_name COLLATE NOCASE ASC"
    elif sort == 'name_desc':
        sql += " ORDER BY u.full_name COLLATE NOCASE DESC"
    else:
        sql += " ORDER BY c.name, c.stream, u.full_name"
    students = conn.execute(sql, params).fetchall()
    classes  = conn.execute("SELECT * FROM classes ORDER BY name, stream").fetchall()
    conn.close()
    return render_template('admin/students.html', students=students,
                           classes=classes, q=q, selected_class=cls, sort=sort)


@app.route('/admin/students/add/alevel', methods=['GET', 'POST'])
@permission_required('edit_students')
def admin_add_alevel_student():
    conn = get_db()

    classes = conn.execute(
        "SELECT * FROM classes WHERE name IN ('S.5','S.6') ORDER BY name, stream"
    ).fetchall()
    principals = conn.execute(
        "SELECT id, name FROM subjects WHERE level='A' "
        "AND name NOT IN ('General Paper','Subsidiary Mathematics','Subsidiary Computer Studies') "
        "ORDER BY name"
    ).fetchall()

    # Ensure subsidiaries exist
    subs_db = conn.execute(
        "SELECT id, name FROM subjects WHERE level='A' AND name LIKE '%Subsidiary%' ORDER BY name"
    ).fetchall()
    if not subs_db:
        for s in ALEVEL_SUBSIDIARIES:
            conn.execute(
                "INSERT OR IGNORE INTO subjects (name, code, level) VALUES (?,?,'A')",
                (s['name'], s['code'])
            )
        conn.commit()
        subs_db = conn.execute(
            "SELECT id, name FROM subjects WHERE level='A' AND name LIKE '%Subsidiary%' ORDER BY name"
        ).fetchall()
    subsidiaries = [
        {'id': s['id'], 'name': s['name'],
         'code': 'SM' if 'Mathematics' in s['name'] else 'ICT'}
        for s in subs_db
    ]

    if request.method == 'POST':
        last_name   = request.form['last_name'].strip()
        first_name  = request.form['first_name'].strip()
        other_names = request.form.get('other_names', '').strip()
        username    = request.form['username'].strip()
        password    = request.form.get('password', '').strip() or 'student123'
        gender      = request.form.get('gender', '').strip().upper()
        school_pay  = request.form.get('school_pay_no', '').strip()
        class_id    = request.form.get('class_id') or None
        house       = request.form.get('house', '').strip()
        email       = request.form.get('email', '').strip()
        enroll_year = request.form.get('enrollment_year') or datetime.now().year
        uce_results = request.form.get('uce_results', '').strip()

        if not first_name or not last_name:
            flash('Please enter both last name and first name.', 'danger')
            conn.close()
            return render_template('admin/student_form_alevel.html',
                                   classes=classes, principals=principals,
                                   subsidiaries=subsidiaries, houses=HOUSES,
                                   current_year=datetime.now().year)

        if gender not in ('M', 'F'):
            flash('Please select a valid gender (M or F).', 'danger')
            conn.close()
            return render_template('admin/student_form_alevel.html',
                                   classes=classes, principals=principals,
                                   subsidiaries=subsidiaries, houses=HOUSES,
                                   current_year=datetime.now().year)

        # Build combination from selected principals + subsidiary
        p_ids = [request.form.get(f'principal_{i}') for i in [1, 2, 3]]
        combo_letters = ''
        for pid in p_ids:
            if pid:
                row = conn.execute("SELECT name FROM subjects WHERE id=?", (pid,)).fetchone()
                if row:
                    combo_letters += row['name'].strip()[0].upper()

        subsidiary_id = request.form.get('subsidiary')
        sub_code = 'SM'
        if subsidiary_id:
            sub_row = conn.execute("SELECT name FROM subjects WHERE id=?", (subsidiary_id,)).fetchone()
            if sub_row:
                sub_code = 'ICT' if 'Computer' in sub_row['name'] else 'SM'

        combination = f"{combo_letters}/{sub_code}/GP" if combo_letters else ''
        student_no  = generate_student_no(conn)
        full_name   = build_full_name(first_name, last_name, other_names)

        try:
            conn.execute(
                "INSERT INTO users (username,password,role,full_name,email,first_name,last_name,other_names) VALUES (?,?,?,?,?,?,?,?)",
                (username, hash_pw(password), 'student', full_name, email, first_name, last_name, other_names)
            )
            uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            pic_fname = 'default.png'
            if 'profile_pic' in request.files:
                f = request.files['profile_pic']
                if f and f.filename and allowed_file(f.filename):
                    ext = f.filename.rsplit('.', 1)[1].lower()
                    pic_fname = secure_filename(f'avatar_{uid}.{ext}')
                    f.save(os.path.join(UPLOAD_FOLDER, pic_fname))
                    conn.execute("UPDATE users SET profile_pic=? WHERE id=?", (pic_fname, uid))

            conn.execute(
                "INSERT INTO students "
                "(user_id,student_no,school_pay_no,class_id,combination,house,"
                "uce_results,enrollment_year,gender) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, student_no, school_pay, class_id, combination,
                 house, uce_results, enroll_year, gender)
            )
            conn.commit()
            flash(f'✅ A-Level student added! No: {student_no} | Combo: {combination}', 'success')
            conn.close()
            return redirect(url_for('admin_students'))
        except sqlite3.IntegrityError as e:
            conn.rollback()
            flash(f'❌ Error: {e}', 'danger')

    conn.close()
    return render_template('admin/student_form_alevel.html',
                           classes=classes, principals=principals,
                           subsidiaries=subsidiaries, houses=HOUSES,
                           current_year=datetime.now().year)


@app.route('/admin/students/add/olevel', methods=['GET', 'POST'])
@permission_required('edit_students')
def admin_add_olevel_student():
    conn = get_db()

    classes = conn.execute(
        "SELECT * FROM classes WHERE name IN ('S.1','S.2','S.3','S.4') ORDER BY name, stream"
    ).fetchall()
    
    # 🔒 FIX: Dynamically generate placeholders to match list length exactly
    compulsory_names = [
        'English Language', 'Mathematics', 'Biology', 'Chemistry', 'Physics', 'Geography',
        'History & Political Education', 'Kiswahili', 'Entrepreneurship Education',
        'Physical Education', 'CRE'
    ]
    comp_placeholders = ','.join(['?'] * len(compulsory_names))
    compulsory = conn.execute(
        f"SELECT id,name FROM subjects WHERE level='O' AND name IN ({comp_placeholders}) ORDER BY name",
        compulsory_names
    ).fetchall()
    
    elective_names = [
        'Agriculture', 'Information & Communications Technology (ICT)',
        'Entrepreneurship Education', 'Art and Design', 'Nutrition & Food Technology',
        'Physical Education', 'Literature in English', 'Kiswahili', 'Chinese'
    ]
    elec_placeholders = ','.join(['?'] * len(elective_names))
    electives = conn.execute(
        f"SELECT id,name FROM subjects WHERE level='O' AND name IN ({elec_placeholders}) ORDER BY name",
        elective_names
    ).fetchall()

    if request.method == 'POST':
        last_name   = request.form['last_name'].strip()
        first_name  = request.form['first_name'].strip()
        other_names = request.form.get('other_names', '').strip()
        username    = request.form['username'].strip()
        password    = request.form.get('password', '').strip() or 'student123'
        gender      = request.form.get('gender', '').strip().upper()
        school_pay  = request.form.get('school_pay_no', '').strip()
        class_id    = request.form.get('class_id') or None
        house       = request.form.get('house', '').strip()
        email       = request.form.get('email', '').strip()
        enroll_year = request.form.get('enrollment_year') or datetime.now().year

        if not first_name or not last_name:
            flash('Please enter both last name and first name.', 'danger')
            conn.close()
            return render_template('admin/student_form_olevel.html',
                                   classes=classes, compulsory=compulsory,
                                   electives=electives, houses=HOUSES,
                                   current_year=datetime.now().year)

        # 🔑 Strict Gender Validation
        if gender not in ('M', 'F'):
            flash('Please select a valid gender (M or F).', 'danger')
            conn.close()
            return render_template('admin/student_form_olevel.html',
                                   classes=classes, compulsory=compulsory,
                                   electives=electives, houses=HOUSES,
                                   current_year=datetime.now().year)

        class_row  = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
        class_name = class_row['name'] if class_row else ''

        combination = 'O-Level'
        if class_name in ('S.3', 'S.4'):
            elective_id = request.form.get('elective')
            if not elective_id:
                flash('S.3/S.4 students must select one elective.', 'danger')
                conn.close()
                return render_template('admin/student_form_olevel.html',
                                       classes=classes, compulsory=compulsory,
                                       electives=electives, houses=HOUSES,
                                       current_year=datetime.now().year)
            elec = conn.execute("SELECT name FROM subjects WHERE id=?", (elective_id,)).fetchone()
            if elec:
                combination = f"O-Level ({elec['name'].strip()[0].upper()})"
        elif class_name in ('S.1', 'S.2'):
            # Allow optional elective for S.1/S.2 combo preview
            elective_id = request.form.get('elective')
            if elective_id:
                elec = conn.execute("SELECT name FROM subjects WHERE id=?", (elective_id,)).fetchone()
                if elec:
                    combination = f"O-Level ({elec['name'].strip()[0].upper()})"

        student_no = generate_student_no(conn)
        full_name = build_full_name(first_name, last_name, other_names)
        try:
            conn.execute(
                "INSERT INTO users (username,password,role,full_name,email,first_name,last_name,other_names) VALUES (?,?,?,?,?,?,?,?)",
                (username, hash_pw(password), 'student', full_name, email, first_name, last_name, other_names)
            )
            uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            pic_fname = 'default.png'
            if 'profile_pic' in request.files:
                f = request.files['profile_pic']
                if f and f.filename and allowed_file(f.filename):
                    ext = f.filename.rsplit('.', 1)[1].lower()
                    pic_fname = secure_filename(f'avatar_{uid}.{ext}')
                    f.save(os.path.join(UPLOAD_FOLDER, pic_fname))
                    conn.execute("UPDATE users SET profile_pic=? WHERE id=?", (pic_fname, uid))

            # 🔑 INSERT includes 'gender' column explicitly
            conn.execute(
                "INSERT INTO students (user_id,student_no,school_pay_no,class_id,combination,house,uce_results,enrollment_year,gender) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, student_no, school_pay, class_id, combination, house, '', enroll_year, gender)
            )
            conn.commit()
            flash(f'✅ O-Level student added! No: {student_no}', 'success')
            conn.close()
            return redirect(url_for('admin_students'))
        except sqlite3.IntegrityError as e:
            conn.rollback()
            flash(f'❌ Error: {e}', 'danger')

    conn.close()
    return render_template('admin/student_form_olevel.html',
                           classes=classes, compulsory=compulsory,
                           electives=electives, houses=HOUSES,
                           current_year=datetime.now().year)

@app.route('/admin/students/<int:sid>/edit', methods=['GET', 'POST'])
@permission_required('edit_students')
def admin_edit_student(sid):
    conn = get_db()
    student = conn.execute(
        "SELECT s.*, u.first_name, u.last_name, u.other_names, u.full_name, u.email, u.username, u.profile_pic "
        "FROM students s JOIN users u ON s.user_id=u.id WHERE s.id=?", (sid,)
    ).fetchone()
    if not student:
        conn.close()
        abort(404)

    class_row  = conn.execute("SELECT name FROM classes WHERE id=?", (student['class_id'],)).fetchone()
    class_name = class_row['name'] if class_row else ''
    level      = 'alevel' if class_name in ('S.5', 'S.6') else 'olevel'

    if level == 'alevel':
        principals = conn.execute(
            "SELECT id, name FROM subjects WHERE level='A' "
            "AND name NOT IN ('General Paper','Subsidiary Mathematics','Subsidiary Computer Studies') "
            "ORDER BY name"
        ).fetchall()
        subs_db = conn.execute(
            "SELECT id, name FROM subjects WHERE level='A' AND name LIKE '%Subsidiary%' ORDER BY name"
        ).fetchall()
        subsidiaries = [
            {'id': s['id'], 'name': s['name'],
             'code': 'SM' if 'Mathematics' in s['name'] else 'ICT'}
            for s in subs_db
        ]
        electives = []
    else:
        principals   = []
        subsidiaries = []
        electives    = conn.execute(
            "SELECT id,name FROM subjects WHERE level='O' AND name IN "
            "('Agriculture','Information & Communications Technology (ICT)',"
            "'Entrepreneurship Education','Art and Design','Nutrition & Food Technology',"
            "'Physical Education','Literature in English','Kiswahili') ORDER BY name"
        ).fetchall()

    if request.method == 'POST':
        last_name   = request.form['last_name'].strip()
        first_name  = request.form['first_name'].strip()
        other_names = request.form.get('other_names', '').strip()
        username    = request.form['username'].strip()
        school_pay  = request.form.get('school_pay_no', '').strip()
        gender      = request.form.get('gender', '').strip().upper()
        house       = request.form.get('house', '').strip()
        email       = request.form.get('email', '').strip()
        enroll_year = request.form.get('enrollment_year') or student['enrollment_year']
        new_pw      = request.form.get('new_password', '').strip()
        combination = student['combination']

        if gender and gender not in ('M', 'F'):
            flash('Invalid gender value.', 'danger')
            conn.close()
            return redirect(url_for('admin_edit_student', sid=sid))

        # Rebuild combination if A-Level
        if level == 'alevel':
            p_ids = [request.form.get(f'principal_{i}') for i in [1, 2, 3]]
            combo_letters = ''
            for pid in p_ids:
                if pid:
                    row = conn.execute("SELECT name FROM subjects WHERE id=?", (pid,)).fetchone()
                    if row:
                        combo_letters += row['name'].strip()[0].upper()
            subsidiary_id = request.form.get('subsidiary')
            sub_code = 'SM'
            if subsidiary_id:
                sub_row = conn.execute(
                    "SELECT name FROM subjects WHERE id=?", (subsidiary_id,)
                ).fetchone()
                if sub_row:
                    sub_code = 'ICT' if 'Computer' in sub_row['name'] else 'SM'
            if combo_letters:
                combination = f"{combo_letters}/{sub_code}/GP"

        if not first_name or not last_name:
            flash('Please enter both last name and first name.', 'danger')
            conn.close()
            return redirect(url_for('admin_edit_student', sid=sid))
        full_name = build_full_name(first_name, last_name, other_names)
        if new_pw:
            conn.execute(
                "UPDATE users SET full_name=?,username=?,email=?,password=?,first_name=?,last_name=?,other_names=? WHERE id=?",
                (full_name, username, email, hash_pw(new_pw), first_name, last_name, other_names, student['user_id'])
            )
        else:
            conn.execute(
                "UPDATE users SET full_name=?,username=?,email=?,first_name=?,last_name=?,other_names=? WHERE id=?",
                (full_name, username, email, first_name, last_name, other_names, student['user_id'])
            )

        conn.execute(
            "UPDATE students SET school_pay_no=?,combination=?,house=?,enrollment_year=?,gender=? "
            "WHERE id=?",
            (school_pay, combination, house, enroll_year, gender, sid)
        )

        if 'profile_pic' in request.files:
            f = request.files['profile_pic']
            if f and f.filename and allowed_file(f.filename):
                ext = f.filename.rsplit('.', 1)[1].lower()
                pic_fname = secure_filename(f'avatar_{student["user_id"]}.{ext}')
                f.save(os.path.join(UPLOAD_FOLDER, pic_fname))
                conn.execute("UPDATE users SET profile_pic=? WHERE id=?",
                             (pic_fname, student['user_id']))

        conn.commit()
        flash('Student updated successfully.', 'success')
        conn.close()
        return redirect(url_for('admin_students'))

    conn.close()
    return render_template('admin/student_form_edit.html',
                           student=student, level=level, class_name=class_name,
                           principals=principals, subsidiaries=subsidiaries,
                           electives=electives, houses=HOUSES,
                           current_year=datetime.now().year)


@app.route('/admin/students/<int:sid>/delete', methods=['POST'])
@permission_required('edit_students')
def admin_delete_student(sid):
    conn = get_db()
    row = conn.execute("SELECT user_id FROM students WHERE id=?", (sid,)).fetchone()
    if row:
        conn.execute("DELETE FROM students WHERE id=?", (sid,))
        conn.execute("DELETE FROM users WHERE id=?", (row['user_id'],))
        conn.commit()
        flash('Student deleted.', 'success')
    conn.close()
    return redirect(url_for('admin_students'))


# ─── admin: teachers ──────────────────────────────────────────────────────────

@app.route('/admin/teachers')
@permission_required('view_teachers')
def admin_teachers():
    conn = get_db()
    teachers = conn.execute(
        "SELECT t.*,u.full_name,u.email,u.username,u.phone "
        "FROM teachers t JOIN users u ON t.user_id=u.id ORDER BY u.full_name"
    ).fetchall()
    conn.close()
    return render_template('admin/teachers.html', teachers=teachers)


def generate_teacher_id(conn):
    rows = conn.execute("SELECT teacher_id FROM teachers").fetchall()
    max_num = 0
    for row in rows:
        tid = row['teacher_id'] or ''
        if tid.upper().startswith('TCH') and tid[3:].isdigit():
            max_num = max(max_num, int(tid[3:]))
    return f"TCH{max_num + 1:03d}"


@app.route('/admin/teachers/add', methods=['GET', 'POST'])
@permission_required('edit_teachers')
def admin_add_teacher():
    conn     = get_db()
    classes  = conn.execute("SELECT * FROM classes ORDER BY name, stream").fetchall()
    subjects = conn.execute(
        "SELECT sp.id, s.name, sp.paper_number, sp.paper_code, s.level "
        "FROM subject_papers sp JOIN subjects s ON sp.subject_id=s.id "
        "ORDER BY s.level, s.name, sp.paper_number"
    ).fetchall()
    next_teacher_id = generate_teacher_id(conn)
    if request.method == 'POST':
        last_name     = request.form['last_name'].strip()
        first_name    = request.form['first_name'].strip()
        other_names   = request.form.get('other_names', '').strip()
        username      = request.form['username'].strip()
        password      = request.form.get('password', '').strip() or 'teacher123'
        teacher_id    = next_teacher_id
        qualification = request.form.get('qualification', '').strip()
        email         = request.form.get('email', '').strip()
        phone         = request.form.get('phone', '').strip()
        year          = request.form.get('year', datetime.now().year)
        assigned_sp   = request.form.getlist('subject_papers')
        assigned_cls  = request.form.getlist('classes')
        full_name     = build_full_name(first_name, last_name, other_names)
        if not first_name or not last_name:
            flash('Please enter both last name and first name.', 'danger')
        else:
            try:
                conn.execute(
                    "INSERT INTO users (username,password,role,full_name,email,phone,first_name,last_name,other_names) VALUES (?,?,?,?,?,?,?,?,?)",
                    (username, hash_pw(password), 'teacher', full_name, email, phone, first_name, last_name, other_names)
                )
                uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO teachers (user_id,teacher_id,qualification) VALUES (?,?,?)",
                    (uid, teacher_id, qualification)
                )
                tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                for sp in assigned_sp:
                    for cls in assigned_cls:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO teacher_assignments "
                                "(teacher_id,subject_paper_id,class_id,year) VALUES (?,?,?,?)",
                                (tid, sp, cls, year)
                            )
                        except Exception:
                            pass
                conn.commit()
                flash('Teacher added.', 'success')
                conn.close()
                return redirect(url_for('admin_teachers'))
            except sqlite3.IntegrityError as e:
                conn.rollback()
                flash(f'Error: {e}', 'danger')
    conn.close()
    return render_template('admin/teacher_form.html', teacher=None,
                           classes=classes, subjects=subjects, assignments=[],
                           next_teacher_id=next_teacher_id)


@app.route('/admin/teachers/<int:tid>/edit', methods=['GET', 'POST'])
@permission_required('edit_teachers')
def admin_edit_teacher(tid):
    conn    = get_db()
    teacher = conn.execute(
        "SELECT t.*, u.first_name, u.last_name, u.other_names, u.full_name, u.email, u.username, u.phone "
        "FROM teachers t JOIN users u ON t.user_id=u.id WHERE t.id=?", (tid,)
    ).fetchone()
    if not teacher:
        conn.close()
        abort(404)
    classes  = conn.execute("SELECT * FROM classes ORDER BY name, stream").fetchall()
    subjects = conn.execute(
        "SELECT sp.id, s.name, sp.paper_number, sp.paper_code, s.level "
        "FROM subject_papers sp JOIN subjects s ON sp.subject_id=s.id "
        "ORDER BY s.level, s.name, sp.paper_number"
    ).fetchall()
    year        = request.args.get('year', datetime.now().year)
    assignments = conn.execute(
        "SELECT subject_paper_id, class_id FROM teacher_assignments "
        "WHERE teacher_id=? AND year=?", (tid, year)
    ).fetchall()
    if request.method == 'POST':
        last_name     = request.form['last_name'].strip()
        first_name    = request.form['first_name'].strip()
        other_names   = request.form.get('other_names', '').strip()
        username      = request.form['username'].strip()
        qualification = request.form.get('qualification', '').strip()
        email         = request.form.get('email', '').strip()
        phone         = request.form.get('phone', '').strip()
        year          = request.form.get('year', datetime.now().year)
        new_pw        = request.form.get('new_password', '').strip()
        assigned_sp   = request.form.getlist('subject_papers')
        assigned_cls  = request.form.getlist('classes')
        full_name     = build_full_name(first_name, last_name, other_names)
        if not first_name or not last_name:
            flash('Please enter both last name and first name.', 'danger')
        else:
            try:
                if new_pw:
                    conn.execute(
                        "UPDATE users SET full_name=?,username=?,email=?,phone=?,password=?,first_name=?,last_name=?,other_names=? WHERE id=?",
                        (full_name, username, email, phone, hash_pw(new_pw), first_name, last_name, other_names, teacher['user_id'])
                    )
                else:
                    conn.execute(
                        "UPDATE users SET full_name=?,username=?,email=?,phone=?,first_name=?,last_name=?,other_names=? WHERE id=?",
                        (full_name, username, email, phone, first_name, last_name, other_names, teacher['user_id'])
                    )
                conn.execute("UPDATE teachers SET qualification=? WHERE id=?", (qualification, tid))
                conn.execute(
                    "DELETE FROM teacher_assignments WHERE teacher_id=? AND year=?", (tid, year)
                )
                for sp in assigned_sp:
                    for cls in assigned_cls:
                        conn.execute(
                            "INSERT OR IGNORE INTO teacher_assignments "
                            "(teacher_id,subject_paper_id,class_id,year) VALUES (?,?,?,?)",
                            (tid, sp, cls, year)
                        )
                conn.commit()
                flash('Teacher updated.', 'success')
                conn.close()
                return redirect(url_for('admin_teachers'))
            except sqlite3.IntegrityError as e:
                conn.rollback()
                flash(f'Error: {e}', 'danger')
    conn.close()
    return render_template('admin/teacher_form.html', teacher=teacher,
                           classes=classes, subjects=subjects, assignments=assignments)


@app.route('/admin/teachers/<int:tid>/delete', methods=['POST'])
@permission_required('edit_teachers')
def admin_delete_teacher(tid):
    conn = get_db()
    row  = conn.execute("SELECT user_id FROM teachers WHERE id=?", (tid,)).fetchone()
    if row:
        conn.execute("DELETE FROM teachers WHERE id=?", (tid,))
        conn.execute("DELETE FROM users WHERE id=?", (row['user_id'],))
        conn.commit()
        flash('Teacher deleted.', 'success')
    conn.close()
    return redirect(url_for('admin_teachers'))


# ─── admin: admins ────────────────────────────────────────────────────────────

@app.route('/admin/admins')
@permission_required('view_admins')
def admin_admins():
    conn   = get_db()
    admins = [dict(row) for row in conn.execute(
        "SELECT * FROM users WHERE role='admin' ORDER BY full_name"
    ).fetchall()]
    conn.close()
    for admin in admins:
        admin['permissions']       = parse_permissions(admin.get('permissions'))
        admin['permission_labels'] = ', '.join(
            ADMIN_PERMISSION_OPTIONS.get(p, p) for p in admin['permissions']
        )
    return render_template('admin/admins.html', admins=admins)


@app.route('/admin/admins/add', methods=['GET', 'POST'])
@permission_required('edit_admins')
def admin_add_admin():
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        username  = request.form['username'].strip()
        password  = request.form.get('password', '').strip() or 'admin123'
        email     = request.form.get('email', '').strip()
        phone     = request.form.get('phone', '').strip()
        perms     = request.form.getlist('permissions')
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO users (username,password,role,full_name,email,phone,permissions) "
                "VALUES (?,?,?,?,?,?,?)",
                (username, hash_pw(password), 'admin', full_name, email, phone, json.dumps(perms))
            )
            conn.commit()
            conn.close()
            flash('Admin user created.', 'success')
            return redirect(url_for('admin_admins'))
        except sqlite3.IntegrityError as e:
            flash(f'Error: {e}', 'danger')
    return render_template('admin/admin_form.html', admin=None,
                           permission_options=ADMIN_PERMISSION_OPTIONS)


@app.route('/admin/admins/<int:uid>/edit', methods=['GET', 'POST'])
@permission_required('edit_admins')
def admin_edit_admin(uid):
    conn  = get_db()
    admin = conn.execute("SELECT * FROM users WHERE id=? AND role='admin'", (uid,)).fetchone()
    if not admin:
        conn.close()
        abort(404)
    admin = dict(admin)
    admin['permissions'] = parse_permissions(admin.get('permissions'))
    if request.method == 'POST':
        full_name = request.form['full_name'].strip()
        username  = request.form['username'].strip()
        email     = request.form.get('email', '').strip()
        phone     = request.form.get('phone', '').strip()
        perms     = request.form.getlist('permissions')
        new_pw    = request.form.get('password', '').strip()
        try:
            if new_pw:
                conn.execute(
                    "UPDATE users SET full_name=?,username=?,email=?,phone=?,password=?,permissions=? "
                    "WHERE id=?",
                    (full_name, username, email, phone, hash_pw(new_pw), json.dumps(perms), uid)
                )
            else:
                conn.execute(
                    "UPDATE users SET full_name=?,username=?,email=?,phone=?,permissions=? WHERE id=?",
                    (full_name, username, email, phone, json.dumps(perms), uid)
                )
            conn.commit()
            conn.close()
            flash('Admin user updated.', 'success')
            return redirect(url_for('admin_admins'))
        except sqlite3.IntegrityError as e:
            conn.rollback()
            flash(f'Error: {e}', 'danger')
    conn.close()
    return render_template('admin/admin_form.html', admin=admin,
                           permission_options=ADMIN_PERMISSION_OPTIONS)


@app.route('/admin/admins/<int:uid>/delete', methods=['POST'])
@permission_required('edit_admins')
def admin_delete_admin(uid):
    if uid == session.get('user_id'):
        flash('You cannot delete your own admin account.', 'danger')
        return redirect(url_for('admin_admins'))
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id=? AND role='admin'", (uid,))
    conn.commit()
    conn.close()
    flash('Admin deleted.', 'success')
    return redirect(url_for('admin_admins'))


# ─── admin: classes & subjects ────────────────────────────────────────────────

@app.route('/admin/classes')
@permission_required('view_classes')
def admin_classes():
    conn     = get_db()
    classes  = conn.execute(
        "SELECT c.*, u.full_name as teacher_name "
        "FROM classes c LEFT JOIN users u ON c.class_teacher_id=u.id ORDER BY c.name, c.stream"
    ).fetchall()
    teachers = conn.execute(
        "SELECT u.id, u.full_name FROM users u WHERE u.role='teacher' ORDER BY u.full_name"
    ).fetchall()
    conn.close()
    return render_template('admin/classes.html', classes=classes, teachers=teachers)


@app.route('/admin/classes/update', methods=['POST'])
@permission_required('edit_classes')
def admin_update_class():
    conn    = get_db()
    cid     = request.form['class_id']
    stream  = request.form.get('stream', '').strip()
    teacher = request.form.get('class_teacher_id') or None
    conn.execute("UPDATE classes SET stream=?,class_teacher_id=? WHERE id=?", (stream, teacher, cid))
    conn.commit()
    conn.close()
    flash('Class updated.', 'success')
    return redirect(url_for('admin_classes'))


@app.route('/admin/subjects')
@permission_required('view_subjects')
def admin_subjects():
    conn     = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY level, name").fetchall()
    papers   = conn.execute(
        "SELECT sp.*, s.name as subject_name, s.level FROM subject_papers sp "
        "JOIN subjects s ON sp.subject_id=s.id ORDER BY s.level, s.name, sp.paper_number"
    ).fetchall()
    conn.close()
    return render_template('admin/subjects.html', subjects=subjects, papers=papers)


@app.route('/admin/subjects/add', methods=['POST'])
@permission_required('edit_subjects')
def admin_add_subject():
    conn  = get_db()
    name  = request.form['name'].strip()
    code  = request.form.get('code', '').strip().upper()
    level = request.form.get('level', 'O')
    conn.execute("INSERT INTO subjects (name,code,level) VALUES (?,?,?)", (name, code, level))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    papers = request.form.get('papers', '1').strip()
    for p in [x.strip() for x in papers.split(',') if x.strip()]:
        pcode = request.form.get(f'paper_code_{p}', f'{code}/{p}').strip() or f'{code}/{p}'
        conn.execute(
            "INSERT INTO subject_papers (subject_id,paper_number,paper_code) VALUES (?,?,?)",
            (sid, p, pcode)
        )
    conn.commit()
    conn.close()
    flash('Subject added.', 'success')
    return redirect(url_for('admin_subjects'))


@app.route('/admin/subjects/<int:sid>/update', methods=['POST'])
@permission_required('edit_subjects')
def admin_update_subject(sid):
    conn  = get_db()
    name  = request.form['name'].strip()
    code  = request.form.get('code', '').strip().upper()
    level = request.form.get('level', 'O')
    conn.execute("UPDATE subjects SET name=?,code=?,level=? WHERE id=?", (name, code, level, sid))
    conn.commit()
    conn.close()
    flash('Subject updated.', 'success')
    return redirect(url_for('admin_subjects'))


@app.route('/admin/subjects/<int:sid>/delete', methods=['POST'])
@permission_required('edit_subjects')
def admin_delete_subject(sid):
    conn = get_db()
    conn.execute("DELETE FROM subjects WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    flash('Subject deleted.', 'success')
    return redirect(url_for('admin_subjects'))


# ─── admin: marks overview ────────────────────────────────────────────────────

@app.route('/admin/marks')
@permission_required('view_marks')
def admin_marks():
    conn     = get_db()
    term     = request.args.get('term', '1')
    year     = request.args.get('year', str(datetime.now().year))
    class_id = request.args.get('class_id', '')
    sort     = request.args.get('sort', 'class')
    classes  = conn.execute("SELECT * FROM classes ORDER BY name, stream").fetchall()

    sql    = ("SELECT s.id, u.full_name, s.student_no, c.name as class_name, c.stream "
              "FROM students s JOIN users u ON s.user_id=u.id "
              "LEFT JOIN classes c ON s.class_id=c.id WHERE 1=1")
    params = []
    if class_id:
        sql += " AND s.class_id=?"
        params.append(class_id)
    if sort == 'name_asc':
        sql += " ORDER BY u.full_name COLLATE NOCASE ASC"
    elif sort == 'name_desc':
        sql += " ORDER BY u.full_name COLLATE NOCASE DESC"
    else:
        sql += " ORDER BY c.name, c.stream, u.full_name"
    students = conn.execute(sql, params).fetchall()

    marks_summary = {}
    for st in students:
        rows = conn.execute(
            "SELECT sp.paper_code, m.exam_type, m.score "
            "FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id "
            "WHERE m.student_id=? AND m.term=? AND m.year=?",
            (st['id'], term, year)
        ).fetchall()
        marks_summary[st['id']] = rows

    conn.close()
    return render_template('admin/marks.html', students=students,
                           marks_summary=marks_summary, classes=classes,
                           term=term, year=year, class_id=class_id, sort=sort)


# ─── admin: reports ───────────────────────────────────────────────────────────

@app.route('/admin/reports')
@permission_required('view_reports')
def admin_reports():
    conn     = get_db()
    students = conn.execute(
        "SELECT s.id, u.full_name, s.student_no, c.name as class_name, c.stream "
        "FROM students s JOIN users u ON s.user_id=u.id "
        "LEFT JOIN classes c ON s.class_id=c.id ORDER BY c.name, c.stream, u.full_name"
    ).fetchall()
    reports  = conn.execute(
        "SELECT r.*, u.full_name, s.student_no, c.name as class_name, c.stream "
        "FROM reports r JOIN students s ON r.student_id=s.id "
        "JOIN users u ON s.user_id=u.id LEFT JOIN classes c ON s.class_id=c.id "
        "ORDER BY r.generated_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return render_template('admin/reports.html', students=students, reports=reports,
                           current_year=datetime.now().year)


@app.route('/admin/reports/generate', methods=['POST'])
@admin_required
def admin_generate_report():
    student_id  = request.form.get('student_id')
    term        = int(request.form.get('term', 1))
    year        = int(request.form.get('year', datetime.now().year))
    report_type = request.form.get('report_type', 'EOT')
    ct_comment  = request.form.get('ct_comment', '').strip()
    ht_comment  = request.form.get('ht_comment', '').strip()

    conn = get_db()
    student = conn.execute(
        "SELECT s.*, u.full_name, u.profile_pic, c.name as class_name, c.stream "
        "FROM students s JOIN users u ON s.user_id=u.id "
        "LEFT JOIN classes c ON s.class_id=c.id WHERE s.id=?", (student_id,)
    ).fetchone()
    if not student:
        flash('Student not found.', 'danger')
        conn.close()
        return redirect(url_for('admin_reports'))

    if ct_comment:
        conn.execute("INSERT OR REPLACE INTO ct_comments (student_id,term,year,comment) VALUES (?,?,?,?)", (student_id, term, year, ct_comment))
    if ht_comment:
        conn.execute("INSERT OR REPLACE INTO ht_comments (student_id,term,year,comment) VALUES (?,?,?,?)", (student_id, term, year, ht_comment))

    class_name = student['class_name'] or ''
    level      = 'alevel' if class_name in ('S.5', 'S.6') else 'olevel'

    offered_papers = get_student_offered_subjects(conn, student_id, class_name, level)
    existing_marks = conn.execute("""
        SELECT sp.id as sp_id, sp.paper_code, m.exam_type, m.score
        FROM marks m JOIN subject_papers sp ON m.subject_paper_id = sp.id
        WHERE m.student_id=? AND m.term=? AND m.year=?
    """, (student_id, term, year)).fetchall()
    mark_lookup = {(m['sp_id'], m['exam_type']): m['score'] for m in existing_marks}

    subj_comments = conn.execute(
        "SELECT sp.paper_code, sc.comment FROM subject_comments sc JOIN subject_papers sp ON sc.subject_paper_id=sp.id WHERE sc.student_id=? AND sc.term=? AND sc.year=?",
        (student_id, term, year)
    ).fetchall()
    comment_map = {r['paper_code']: r['comment'] for r in subj_comments}

    subjects_map = {}
    total_pts = valid_count = 0
    GRADE_DESC = {'A': 'EXCEPTIONAL', 'B': 'OUTSTANDING', 'C': 'SATISFACTORY', 'D': 'BASIC', 'E': 'ELEMENTARY'}

    for p in offered_papers:
        sub_name = p['subject_name']
        if sub_name not in subjects_map:
            subjects_map[sub_name] = {'name': sub_name, 'grade': 'X', 'descriptor': 'NOT TAKEN', 'papers': []}

        bot = mark_lookup.get((p['sp_id'], 'BOT'))
        mt  = mark_lookup.get((p['sp_id'], 'MT'))
        eot = mark_lookup.get((p['sp_id'], 'EOT'))
        _, final = calc_final(bot, mt, eot)
        fin_g, _, pts = get_grade(final)

        if final is not None:
            total_pts += pts
            valid_count += 1

        mot_display   = f"{int(mt)} - {get_grade(mt)[0]}" if mt is not None else "--"
        eot_display   = f"{int(eot)} - {get_grade(eot)[0]}" if eot is not None else "--"
        final_display = f"{int(final)} - {fin_g}" if final is not None else "--"

        subjects_map[sub_name]['papers'].append({
            'code': p['paper_code'], 'mot': mot_display, 'eot': eot_display,
            'final': final_display, 'comment': comment_map.get(p['paper_code'], '')
        })

    for sub in subjects_map.values():
        if sub['papers'] and sub['papers'][0]['final'] != '--':
            sub['grade'] = sub['papers'][0]['final'].split(' - ')[-1]
            sub['descriptor'] = GRADE_DESC.get(sub['grade'], 'NOT TAKEN')

    avg = round(total_pts / valid_count, 2) if valid_count > 0 else 0
    avg_grade = get_grade(avg)[0] if avg > 0 else 'X'
    next_term_date = get_setting('next_term_start_date', '')

    report_data = {
        'student': dict(student), 'term': term, 'year': year,
        'ct_comment': ct_comment, 'ht_comment': ht_comment,
        'print_date': datetime.now().strftime("%d-%b-%Y"),
        'subjects': list(subjects_map.values()),
        'summary': {'avg': avg, 'avg_grade': avg_grade, 'total_pts': total_pts},
        'next_term_date': next_term_date
    }

    # 🔑 Render HTML to PNG strictly for export
    html = render_template('report_card.html', **report_data)
    safe_type = 'BOT' if report_type == 'BOT' else 'EOT'
    filename  = f"report_{student_id}_T{term}_{safe_type}_{year}.png"
    filepath  = os.path.join(REPORTS_FOLDER, filename)
    os.makedirs(REPORTS_FOLDER, exist_ok=True)

    try:
        from weasyprint import HTML
        html_obj = HTML(string=html, base_url=request.url_root)
        if hasattr(html_obj, 'write_png'):
            html_obj.write_png(filepath)
        else:
            document = html_obj.render()
            document.write_png(filepath)
    except ImportError:
        flash('WeasyPrint not installed. Run: pip install weasyprint', 'danger')
        conn.close()
        return redirect(url_for('admin_reports'))
    except Exception as e:
        flash(f'PNG generation failed: {e}', 'danger')
        conn.close()
        return redirect(url_for('admin_reports'))

    conn.execute("INSERT INTO reports (student_id,term,year,report_type,file_path) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING", 
                 (student_id, term, year, safe_type, filename))
    conn.execute("UPDATE reports SET file_path=?, generated_at=CURRENT_TIMESTAMP WHERE student_id=? AND term=? AND year=? AND report_type=?", 
                 (filename, student_id, term, year, safe_type))
    conn.commit()
    conn.close()

    flash(f'✅ {safe_type} Report exported as PNG successfully.', 'success')
    return redirect(url_for('admin_reports'))


@app.route('/admin/reports/<int:rid>/delete', methods=['POST'])
@admin_required
def admin_delete_report(rid):
    conn   = get_db()
    report = conn.execute("SELECT file_path FROM reports WHERE id=?", (rid,)).fetchone()
    if report and report['file_path']:
        try:
            fp = os.path.join(REPORTS_FOLDER, report['file_path'])
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass
    if report:
        conn.execute("DELETE FROM reports WHERE id=?", (rid,))
        conn.commit()
        flash('Report deleted successfully.', 'success')
    else:
        flash('Report not found.', 'danger')
    conn.close()
    return redirect(url_for('admin_reports'))


@app.route('/admin/comments/save', methods=['POST'])
@admin_required
def admin_save_comments():
    student_id = request.form['student_id']
    term       = request.form['term']
    year       = request.form['year']
    ct         = request.form.get('ct_comment', '').strip()
    ht         = request.form.get('ht_comment', '').strip()
    conn = get_db()
    if ct:
        conn.execute(
            "INSERT OR REPLACE INTO ct_comments (student_id,term,year,comment) VALUES (?,?,?,?)",
            (student_id, term, year, ct)
        )
    if ht:
        conn.execute(
            "INSERT OR REPLACE INTO ht_comments (student_id,term,year,comment) VALUES (?,?,?,?)",
            (student_id, term, year, ht)
        )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ─── admin: master results ────────────────────────────────────────────────────

@app.route('/admin/results/sheet', methods=['GET', 'POST'])
@admin_required
def admin_results_sheet():
    """Alias kept so existing base.html nav links don't break."""
    return admin_master_results_impl()


@app.route('/admin/results/master', methods=['GET', 'POST'])
@admin_required
def admin_master_results():
    return admin_master_results_impl()


def admin_master_results_impl():
    if request.method == 'GET':
        return render_template('admin/master_results.html',
                               current_year=datetime.now().year,
                               term=request.args.get('term', '1'),
                               sheets=[], streams=HOUSES)

    term          = int(request.form.get('term', 1))
    year          = int(request.form.get('year', datetime.now().year))
    filter_stream = request.form.get('stream', '')
    conn          = get_db()

    classes = conn.execute("""
        SELECT c.id, c.name, c.stream, COUNT(s.id) as student_count
        FROM classes c LEFT JOIN students s ON c.id=s.class_id
        GROUP BY c.id HAVING student_count > 0 ORDER BY c.name, c.stream
    """).fetchall()

    sheets = []
    for c in classes:
        if filter_stream and c['stream'] != filter_stream:
            continue
        class_name = f"{c['name']}/{c['stream']}" if c['stream'] else c['name']
        level      = 'alevel' if c['name'] in ('S.5', 'S.6') else 'olevel'

        students = conn.execute(
            "SELECT s.id, u.full_name FROM students s "
            "JOIN users u ON s.user_id=u.id WHERE s.class_id=? ORDER BY u.full_name",
            (c['id'],)
        ).fetchall()
        if not students:
            continue

        s_ids       = [s['id'] for s in students]
        placeholder = ','.join(['?'] * len(s_ids))
        marks       = conn.execute(f"""
            SELECT m.student_id, s.name as subject_name, m.exam_type, m.score
            FROM marks m
            JOIN subject_papers sp ON m.subject_paper_id=sp.id
            JOIN subjects s ON sp.subject_id=s.id
            WHERE m.student_id IN ({placeholder}) AND m.term=? AND m.year=?
            ORDER BY s.name
        """, s_ids + [term, year]).fetchall()

        student_marks = {sid: {} for sid in s_ids}
        subject_set   = set()
        for m in marks:
            subject_set.add(m['subject_name'])
            student_marks[m['student_id']].setdefault(m['subject_name'], {})[m['exam_type']] = m['score']

        results = []
        for s in students:
            sid    = s['id']
            grades = {}
            total  = 0
            count  = 0
            for sub in sorted(subject_set):
                ex = student_marks[sid].get(sub, {})
                _, final = calc_final(ex.get('BOT'), ex.get('MT'), ex.get('EOT'))
                g, _, pts = get_grade(final)
                grades[sub] = {'grade': g, 'pts': pts, 'final': final}
                if final is not None:
                    total += pts
                    count += 1
            results.append({
                'name': s['full_name'], 'grades': grades,
                'total_pts': total, 'avg_pts': total / count if count else 99,
            })

        results.sort(key=lambda x: (x['total_pts'], x['name']))
        rank = 1
        for i, res in enumerate(results):
            if i > 0 and res['total_pts'] != results[i-1]['total_pts']:
                rank = i + 1
            res['stream_pos'] = res['class_pos'] = res['rank'] = rank

        sheets.append({'class_name': class_name, 'level': level,
                       'subjects': sorted(subject_set), 'students': results})

    conn.close()
    return render_template('admin/master_results.html',
                           sheets=sheets, term=term, year=year, streams=HOUSES)


@app.route('/admin/results/alevel', methods=['GET', 'POST'])
@admin_required
def admin_alevel_master_results():
    if request.method == 'GET':
        return render_template('admin/master_alevel_results.html',
                               current_year=datetime.now().year,
                               term=1, year=datetime.now().year,
                               sheets=[], streams=HOUSES)

    term          = int(request.form.get('term', 1))
    year          = int(request.form.get('year', datetime.now().year))
    filter_stream = request.form.get('stream', '').strip()
    conn          = get_db()

    # 🔑 Exact order matching your ALEVEL.docx
    ALEVEL_SUBJECT_ORDER = [
        'General Paper', 'Subsidiary Computer Studies', 'Subsidiary Mathematics',
        'Mathematics', 'Physics', 'Biology', 'Chemistry', 'History', 'Geography',
        'Divinity', 'Agriculture', 'Economics', 'Entrepreneurship Education',
        'Literature in English', 'Technical Drawing (Building)',  'Technical Drawing (Mechanical)', 'Fine Art', 'Nutrition & Food Technology', 'Chinese'
    ]
    
    # Principals: Pass on A, B, C, D
    PRINCIPAL_SUBJECTS = [
        'Mathematics', 'Physics', 'Biology', 'Chemistry', 'History', 'Geography',
        'Divinity', 'Agriculture', 'Economics', 'Entrepreneurship Education',
        'Literature in English', 'Technical Drawing (Building)', 'Technical Drawing (Mechanical)', 'Fine Art', 'Nutrition & Food Technology', 'Chinese'
    ]
    
    # Non-Principals (GP + Subsidiaries): Pass on A, B, C, D, E
    SUBS_PASS_CRITERIA = ['General Paper', 'Subsidiary Computer Studies', 'Subsidiary Mathematics']

    classes = conn.execute("""
        SELECT c.id, c.name, c.stream, COUNT(s.id) as student_count
        FROM classes c LEFT JOIN students s ON c.id=s.class_id
        WHERE c.name IN ('S.5','S.6')
        GROUP BY c.id HAVING student_count > 0 ORDER BY c.name, c.stream
    """).fetchall()

    sheets = []
    for c in classes:
        if filter_stream and c['stream'] != filter_stream:
            continue
        class_name = f"{c['name']}/{c['stream']}" if c['stream'] else c['name']

        # 🔑 Explicit alias prevents column collision & guarantees raw DB value
        students = conn.execute(
            "SELECT s.id, u.full_name, s.combination AS db_combo, s.gender "
            "FROM students s JOIN users u ON s.user_id=u.id "
            "WHERE s.class_id=? ORDER BY u.full_name",
            (c['id'],)
        ).fetchall()
        if not students: continue

        s_ids = [s['id'] for s in students]
        placeholder = ','.join(['?'] * len(s_ids))
        marks = conn.execute(f"""
            SELECT m.student_id, s.name as subject_name, m.exam_type, m.score
            FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id
            JOIN subjects s ON sp.subject_id=s.id
            WHERE m.student_id IN ({placeholder}) AND m.term=? AND m.year=?
        """, s_ids + [term, year]).fetchall()

        student_marks = {sid: {} for sid in s_ids}
        for m in marks:
            student_marks.setdefault(m['student_id'], {}).setdefault(m['subject_name'], {})[m['exam_type']] = m['score']

        results = []
        for s in students:
            exams = student_marks.get(s['id'], {})
            subject_grades = {}
            total_pts = 0
            principal_pass = 0
            subsidiary_pass = 0

            for sub_name in ALEVEL_SUBJECT_ORDER:
                sub_exams = exams.get(sub_name, {})
                bot = sub_exams.get('BOT')
                mt  = sub_exams.get('MT')
                eot = sub_exams.get('EOT')
                _, final = calc_final(bot, mt, eot)
                grade, _, pts = get_grade(final)
                
                subject_grades[sub_name] = grade if final is not None else '-'

                if final is not None:
                    total_pts += pts
                    # 🔑 Principals pass on A-D
                    if sub_name in PRINCIPAL_SUBJECTS and grade in ('A', 'B', 'C', 'D'):
                        principal_pass += 1
                    # 🔑 GP & Subsidiaries pass on A-E (cuts across)
                    if sub_name in SUBS_PASS_CRITERIA and grade in ('A', 'B', 'C', 'D', 'E'):
                        subsidiary_pass += 1

            # Extract the exact subsidiary grade the student takes
            sub_ict = subject_grades.get('Subsidiary Computer Studies', '-')
            sub_math = subject_grades.get('Subsidiary Mathematics', '-')
            subsidiary_grade = sub_ict if sub_ict != '-' else sub_math

            results.append({
                'name': s['full_name'],
                'gender': s['gender'] if s['gender'] else ('M' if any(x in s['full_name'].upper() for x in ['MR.','MASTER']) else 'F'),
                # 🔑 STRICT DB BINDING: No fallback masking, exact value shown
                'combination': s['db_combo'] if s['db_combo'] and s['db_combo'].strip() else '—',
                'grades': subject_grades,
                'total_pts': total_pts,
                'principal_pass': principal_pass,
                'subsidiary_grade': subsidiary_grade,
                'subsidiary_pass': subsidiary_pass
            })

        # Sort & Rank
        results.sort(key=lambda x: (x['total_pts'], x['name']))
        rank = 1
        for i, res in enumerate(results):
            if i > 0 and res['total_pts'] != results[i-1]['total_pts']:
                rank = i + 1
            res['rank'] = rank

        sheets.append({
            'class_name': class_name,
            'students': results,
            'stream': c['stream'],
            'subjects': ALEVEL_SUBJECT_ORDER,
        })

    conn.close()
    return render_template('admin/master_alevel_results.html',
                           sheets=sheets, term=term, year=year, streams=HOUSES)
# ─── admin: calendar events ───────────────────────────────────────────────────

@app.route('/admin/events')
@admin_required
def admin_events():
    month  = int(request.args.get('month', datetime.now().month))
    year   = int(request.args.get('year',  datetime.now().year))
    conn   = get_db()
    first  = date(year, month, 1).isoformat()
    last   = date(year, month, cal.monthrange(year, month)[1]).isoformat()
    events = conn.execute(
        "SELECT * FROM events WHERE event_date BETWEEN ? AND ? ORDER BY event_date",
        (first, last)
    ).fetchall()
    conn.close()
    return jsonify([dict(e) for e in events])


@app.route('/admin/events/add', methods=['POST'])
@admin_required
def admin_add_event():
    data  = request.get_json() or request.form
    title = data.get('title', '').strip()
    edate = data.get('event_date', '')
    desc  = data.get('description', '').strip()
    etype = data.get('event_type', 'general')
    if not title or not edate:
        return jsonify({'error': 'title and date required'}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO events (title,event_date,description,event_type,created_by) VALUES (?,?,?,?,?)",
        (title, edate, desc, etype, session['user_id'])
    )
    conn.commit()
    eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'id': eid})


@app.route('/admin/events/<int:eid>', methods=['PUT'])
@admin_required
def admin_edit_event(eid):
    data  = request.get_json() or {}
    title = data.get('title', '').strip()
    edate = data.get('event_date', '')
    desc  = data.get('description', '').strip()
    etype = data.get('event_type', 'general')
    conn  = get_db()
    conn.execute(
        "UPDATE events SET title=?,event_date=?,description=?,event_type=? WHERE id=?",
        (title, edate, desc, etype, eid)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/admin/events/<int:eid>', methods=['DELETE'])
@admin_required
def admin_delete_event(eid):
    conn = get_db()
    conn.execute("DELETE FROM events WHERE id=?", (eid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


# ─── teacher routes ───────────────────────────────────────────────────────────

@app.route('/teacher/dashboard')
@teacher_or_admin
def teacher_dashboard():
    conn    = get_db()
    uid     = session['user_id']
    teacher = conn.execute(
        "SELECT t.*, u.first_name, u.last_name, u.other_names, u.full_name, u.username, u.email, u.phone "
        "FROM teachers t JOIN users u ON t.user_id=u.id WHERE t.user_id=?",
        (uid,)
    ).fetchone()
    if not teacher and session['role'] == 'admin':
        conn.close()
        return redirect(url_for('admin_dashboard'))
    if not teacher:
        conn.close()
        flash('Teacher profile not found.', 'danger')
        return redirect(url_for('home'))

    year        = request.args.get('year', datetime.now().year)
    assignments = conn.execute(
        "SELECT DISTINCT c.id as class_id, c.name as class_name, c.stream, "
        "s.id as subject_id, s.name as subject_name, sp.id as sp_id, "
        "sp.paper_code, sp.paper_number "
        "FROM teacher_assignments ta "
        "JOIN classes c ON ta.class_id=c.id "
        "JOIN subject_papers sp ON ta.subject_paper_id=sp.id "
        "JOIN subjects s ON sp.subject_id=s.id "
        "WHERE ta.teacher_id=? AND ta.year=? ORDER BY c.name, c.stream, s.name",
        (teacher['id'], year)
    ).fetchall()

    today    = date.today().isoformat()
    upcoming = conn.execute(
        "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date LIMIT 5", (today,)
    ).fetchall()
    conn.close()
    return render_template('teacher/dashboard.html',
                           teacher=teacher, assignments=assignments,
                           upcoming=upcoming, year=year,
                           current_year=datetime.now().year)


@app.route('/teacher/marks')
@teacher_or_admin
def teacher_marks():
    conn    = get_db()
    uid     = session['user_id']
    teacher = conn.execute("SELECT * FROM teachers WHERE user_id=?", (uid,)).fetchone()
    if not teacher:
        conn.close()
        flash('Teacher profile not found.', 'danger')
        return redirect(url_for('home'))

    class_id    = request.args.get('class_id')
    sp_id       = request.args.get('sp_id')
    term        = request.args.get('term', '1')
    year        = request.args.get('year', str(datetime.now().year))
    assignments = conn.execute(
        "SELECT DISTINCT c.id as class_id, c.name as class_name, c.stream, "
        "sp.id as sp_id, sp.paper_code, s.name as subject_name "
        "FROM teacher_assignments ta "
        "JOIN classes c ON ta.class_id=c.id "
        "JOIN subject_papers sp ON ta.subject_paper_id=sp.id "
        "JOIN subjects s ON sp.subject_id=s.id "
        "WHERE ta.teacher_id=? AND ta.year=? ORDER BY c.name, c.stream, s.name",
        (teacher['id'], year)
    ).fetchall()

    students       = []
    marks_data     = {}
    subj_comments  = {}
    paper_info     = None

    if class_id and sp_id:
        paper_info = conn.execute(
            "SELECT sp.*, s.name as subject_name, s.level FROM subject_papers sp "
            "JOIN subjects s ON sp.subject_id=s.id WHERE sp.id=?", (sp_id,)
        ).fetchone()

        if paper_info:
            subject_name   = paper_info['subject_name']
            subject_level  = paper_info['level']
            subject_letter = subject_name.strip()[0].upper()
            cls            = conn.execute("SELECT name FROM classes WHERE id=?", (class_id,)).fetchone()
            class_name     = cls['name'] if cls else ''

            all_students = conn.execute(
                "SELECT s.id, u.full_name, s.student_no, s.combination "
                "FROM students s JOIN users u ON s.user_id=u.id "
                "WHERE s.class_id=? ORDER BY u.full_name", (class_id,)
            ).fetchall()

            for s in all_students:
                combo = s['combination'] or ''
                if subject_level == 'A':
                    principals = combo.split('/')[0] if '/' in combo else combo
                    if subject_letter in principals:
                        students.append(s)
                else:
                    if class_name in ('S.1', 'S.2'):
                        students.append(s)
                    elif class_name in ('S.3', 'S.4'):
                        if '(' in combo and ')' in combo:
                            elective_letter = combo.split('(')[1].split(')')[0].strip().upper()
                            if elective_letter == subject_letter:
                                students.append(s)
                        else:
                            students.append(s)
                    else:
                        students.append(s)

            for st in students:
                rows = conn.execute(
                    "SELECT exam_type, score FROM marks "
                    "WHERE student_id=? AND subject_paper_id=? AND term=? AND year=?",
                    (st['id'], sp_id, term, year)
                ).fetchall()
                marks_data[st['id']] = {r['exam_type']: r for r in rows}
                sc = conn.execute(
                    "SELECT comment FROM subject_comments "
                    "WHERE student_id=? AND subject_paper_id=? AND term=? AND year=?",
                    (st['id'], sp_id, term, year)
                ).fetchone()
                subj_comments[st['id']] = sc['comment'] if sc else ''

    conn.close()
    return render_template('teacher/marks.html',
                           teacher=teacher, assignments=assignments,
                           students=students, marks_data=marks_data,
                           subj_comments=subj_comments, paper_info=paper_info,
                           class_id=class_id, sp_id=sp_id, term=term, year=year)

# ── O-Level PNG download ──────────────────────────────────────────────────────
@app.route('/admin/results/olevel/png', methods=['POST'])
@admin_required
def admin_olevel_master_pdf():
    """Generate and stream an A4-landscape PNG of the O-Level master results."""
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        flash('WeasyPrint is not installed. Run: pip install weasyprint', 'danger')
        return redirect(url_for('admin_olevel_master_results'))
 
    term          = int(request.form.get('term', 1))
    year          = int(request.form.get('year', datetime.now().year))
    filter_stream = request.form.get('stream', '').strip()
    conn          = get_db()
 
    OLEVEL_SUBJECT_ORDER = [
        'Physical Education',
        'Information & Communications Technology (ICT)',
        'English Language', 'Mathematics', 'Physics', 'Biology', 'Chemistry',
        'History & Political Education', 'Geography', 'CRE', 'Agriculture',
        'Kiswahili', 'Entrepreneurship Education', 'Literature in English',
        'Chinese', 'Fine Art', 'Nutrition & Food Technology',
    ]
 
    classes = conn.execute("""
        SELECT c.id, c.name, c.stream, COUNT(s.id) as student_count
        FROM classes c LEFT JOIN students s ON c.id=s.class_id
        WHERE c.name IN ('S.1','S.2','S.3','S.4')
        GROUP BY c.id HAVING student_count > 0 ORDER BY c.name, c.stream
    """).fetchall()
 
    sheets = []
    for c in classes:
        if filter_stream and c['stream'] != filter_stream:
            continue
        class_name = f"{c['name']}/{c['stream']}" if c['stream'] else c['name']
        students = conn.execute(
            "SELECT s.id, u.full_name, s.combination AS db_combo, s.gender "
            "FROM students s JOIN users u ON s.user_id=u.id "
            "WHERE s.class_id=? ORDER BY u.full_name", (c['id'],)
        ).fetchall()
        if not students:
            continue
 
        s_ids = [s['id'] for s in students]
        placeholder = ','.join(['?'] * len(s_ids))
        marks = conn.execute(f"""
            SELECT m.student_id, s.name as subject_name, m.exam_type, m.score
            FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id
            JOIN subjects s ON sp.subject_id=s.id
            WHERE m.student_id IN ({placeholder}) AND m.term=? AND m.year=?
        """, s_ids + [term, year]).fetchall()
 
        student_marks = {sid: {} for sid in s_ids}
        for m in marks:
            student_marks.setdefault(m['student_id'], {}).setdefault(
                m['subject_name'], {})[m['exam_type']] = m['score']
 
        results = []
        for s in students:
            exams = student_marks.get(s['id'], {})
            subject_grades = {}
            total_score = valid_count = 0
            for sub_name in OLEVEL_SUBJECT_ORDER:
                sub_exams = exams.get(sub_name, {})
                _, final = calc_final(sub_exams.get('BOT'), sub_exams.get('MT'), sub_exams.get('EOT'))
                grade, _, _ = get_grade(final)
                subject_grades[sub_name] = grade if final is not None else '-'
                if final is not None:
                    total_score += final
                    valid_count += 1
 
            avg_score = round(total_score / valid_count, 2) if valid_count > 0 else 0
            descriptor = ('EXCEPTIONAL' if avg_score >= 80 else
                          'OUTSTANDING'  if avg_score >= 70 else
                          'SATISFACTORY' if avg_score >= 60 else
                          'BASIC'        if avg_score >= 50 else
                          'ELEMENTARY'   if avg_score > 0   else 'NOT TAKEN')
 
            results.append({
                'name': s['full_name'],
                'gender': s['gender'] or 'M',
                'grades': subject_grades,
                'total_score': round(total_score, 2),
                'average_score': avg_score,
                'descriptor': descriptor,
                'stream_rank': 0, 'class_rank': 0,
            })
 
        results.sort(key=lambda x: (-x['average_score'], x['name']))
        rank = 1
        for i, res in enumerate(results):
            if i > 0 and res['average_score'] != results[i-1]['average_score']:
                rank = i + 1
            res['stream_rank'] = res['class_rank'] = rank
 
        sheets.append({'class_name': class_name, 'students': results,
                       'subjects': OLEVEL_SUBJECT_ORDER})
 
    conn.close()
 
    # Render a self-contained HTML page for WeasyPrint (no sidebar/topbar)
    html_content = _render_olevel_pdf_html(sheets, term, year)
    html_obj = HTML(string=html_content)
    if hasattr(html_obj, 'write_png'):
        png_bytes = html_obj.write_png(stylesheets=[CSS(string=_pdf_base_css())])
    else:
        document = html_obj.render(stylesheets=[CSS(string=_pdf_base_css())])
        png_bytes = document.write_png()
 
    safe_stream = filter_stream.replace('/', '-') if filter_stream else 'All'
    filename = f"OLevel_Results_T{term}_{year}_{safe_stream}.png"
 
    return Response(
        png_bytes,
        mimetype='image/png',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )
 
 
# ── A-Level PNG download ──────────────────────────────────────────────────────

@app.route('/admin/results/alevel/png', methods=['POST'])
@admin_required
def admin_alevel_master_pdf():
    """Generate and stream an A4-landscape PNG of the A-Level master results."""
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        flash('WeasyPrint is not installed. Run: pip install weasyprint', 'danger')
        return redirect(url_for('admin_alevel_master_results'))
 
    term          = int(request.form.get('term', 1))
    year          = int(request.form.get('year', datetime.now().year))
    filter_stream = request.form.get('stream', '').strip()
    conn          = get_db()
 
    ALEVEL_SUBJECT_ORDER = [
        'General Paper', 'Subsidiary Computer Studies', 'Subsidiary Mathematics',
        'Mathematics', 'Physics', 'Biology', 'Chemistry', 'History', 'Geography',
        'Divinity', 'Agriculture', 'Economics', 'Entrepreneurship Education',
        'Literature in English', 'Technical Drawing (Building)', 'Fine Art',
        'Nutrition & Food Technology', 'Chinese', 'Technical Drawing (Mechanical)',
    ]
    PRINCIPAL_SUBJECTS = [
        'Mathematics', 'Physics', 'Biology', 'Chemistry', 'History', 'Geography',
        'Divinity', 'Agriculture', 'Economics', 'Entrepreneurship Education',
        'Literature in English', 'Technical Drawing  (Building)', 'Fine Art', 'Technical Drawing (Mechanical)',
        'Nutrition & Food Technology', 'Chinese',
    ]
    SUBS_PASS_CRITERIA = ['General Paper', 'Subsidiary Computer Studies', 'Subsidiary Mathematics']
 
    classes = conn.execute("""
        SELECT c.id, c.name, c.stream, COUNT(s.id) as student_count
        FROM classes c LEFT JOIN students s ON c.id=s.class_id
        WHERE c.name IN ('S.5','S.6')
        GROUP BY c.id HAVING student_count > 0 ORDER BY c.name, c.stream
    """).fetchall()
 
    sheets = []
    for c in classes:
        if filter_stream and c['stream'] != filter_stream:
            continue
        class_name = f"{c['name']}/{c['stream']}" if c['stream'] else c['name']
        students = conn.execute(
            "SELECT s.id, u.full_name, s.combination AS db_combo, s.gender "
            "FROM students s JOIN users u ON s.user_id=u.id "
            "WHERE s.class_id=? ORDER BY u.full_name", (c['id'],)
        ).fetchall()
        if not students:
            continue
 
        s_ids = [s['id'] for s in students]
        placeholder = ','.join(['?'] * len(s_ids))
        marks = conn.execute(f"""
            SELECT m.student_id, s.name as subject_name, m.exam_type, m.score
            FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id
            JOIN subjects s ON sp.subject_id=s.id
            WHERE m.student_id IN ({placeholder}) AND m.term=? AND m.year=?
        """, s_ids + [term, year]).fetchall()
 
        student_marks = {sid: {} for sid in s_ids}
        for m in marks:
            student_marks.setdefault(m['student_id'], {}).setdefault(
                m['subject_name'], {})[m['exam_type']] = m['score']
 
        results = []
        for s in students:
            exams = student_marks.get(s['id'], {})
            subject_grades = {}
            total_pts = principal_pass = subsidiary_pass = 0
            for sub_name in ALEVEL_SUBJECT_ORDER:
                sub_exams = exams.get(sub_name, {})
                _, final = calc_final(sub_exams.get('BOT'), sub_exams.get('MT'), sub_exams.get('EOT'))
                grade, _, pts = get_grade(final)
                subject_grades[sub_name] = grade if final is not None else '-'
                if final is not None:
                    total_pts += pts
                    if sub_name in PRINCIPAL_SUBJECTS and grade in ('A', 'B', 'C', 'D'):
                        principal_pass += 1
                    if sub_name in SUBS_PASS_CRITERIA and grade in ('A', 'B', 'C', 'D', 'E'):
                        subsidiary_pass += 1
 
            sub_ict  = subject_grades.get('Subsidiary Computer Studies', '-')
            sub_math = subject_grades.get('Subsidiary Mathematics', '-')
            subsidiary_grade = sub_ict if sub_ict != '-' else sub_math
 
            results.append({
                'name': s['full_name'],
                'gender': s['gender'] or 'M',
                'combination': s['db_combo'] or '—',
                'grades': subject_grades,
                'total_pts': total_pts,
                'principal_pass': principal_pass,
                'subsidiary_grade': subsidiary_grade,
                'rank': 0,
            })
 
        results.sort(key=lambda x: (x['total_pts'], x['name']))
        rank = 1
        for i, res in enumerate(results):
            if i > 0 and res['total_pts'] != results[i-1]['total_pts']:
                rank = i + 1
            res['rank'] = rank
 
        sheets.append({'class_name': class_name, 'students': results,
                       'subjects': ALEVEL_SUBJECT_ORDER})
 
    conn.close()
 
    html_content = _render_alevel_pdf_html(sheets, term, year)
    html_obj = HTML(string=html_content)
    if hasattr(html_obj, 'write_png'):
        png_bytes = html_obj.write_png(stylesheets=[CSS(string=_pdf_base_css())])
    else:
        document = html_obj.render(stylesheets=[CSS(string=_pdf_base_css())])
        png_bytes = document.write_png()
 
    safe_stream = filter_stream.replace('/', '-') if filter_stream else 'All'
    filename = f"ALevel_Results_T{term}_{year}_{safe_stream}.png"
 
    return Response(
        png_bytes,
        mimetype='image/png',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )
 
 
# ── PDF HTML rendering helpers ────────────────────────────────────────────────
# Place these as module-level helper functions (not routes) anywhere in app.py
 
def _pdf_base_css():
    """Base CSS injected into every WeasyPrint render."""
    return """
        @page {
            size: A4 landscape;
            margin: 8mm;
        }
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 7pt;
            color: #1a2340;
            margin: 0;
            padding: 0;
            background: #fff;
        }
        .sheet { margin-bottom: 0; page-break-after: always; }
        .sheet:last-child { page-break-after: auto; }
 
        /* School header */
        .sheet-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            padding: 6px 10px;
            border-bottom: 3px solid #d4af37;
            margin-bottom: 4px;
        }
        .school-name { font-size: 13pt; font-weight: 800; color: #001a4d; }
        .school-sub  { font-size: 7pt; color: #666; margin-top: 2px; }
        .class-name  { font-size: 10pt; font-weight: 700; color: #001a4d; text-align: right; }
        .class-sub   { font-size: 7pt; color: #666; text-align: right; }
 
        /* Table */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 6.5pt;
            table-layout: fixed;
        }
        th, td {
            border: 1px solid #ccc;
            padding: 2px 2px;
            text-align: center;
            vertical-align: middle;
            overflow: hidden;
        }
        td.name-cell {
            text-align: left;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
 
        /* 🔑 FIXED FOR WEASYPRINT: Vertical headers without transform */
       /* Vertical subject headers */
th.rotate-header {
    width: 28px;
    min-width: 28px;
    max-width: 28px;
    height: 140px;
    padding: 0;
    margin: 0;
    position: relative;
    text-align: center;
    vertical-align: bottom;
    background: #001a4d;
    color: white;
}

th.rotate-header .rotate-label {
    display: block;
    position: absolute;
    bottom: 40px;
    left: 50%;
    transform: translateX(-50%) rotate(-90deg);
    transform-origin: center;
    width: 140px;
    white-space: nowrap;
    font-size: 5.8pt;
    font-weight: 700;
    line-height: 1.1;
    padding: 0;
}
 
        /* Header row colours */
        thead tr { background: #001a4d; color: #fff; }
        th { color: #fff; background: #001a4d; }
        th.summary { background: #003087; }
 
        /* Alternating rows */
        tbody tr:nth-child(even) { background: #f9fafc; }
        tbody tr:nth-child(odd)  { background: #fff; }
 
        /* Grade colours */
        .g-A { color: #008632; font-weight: 700; }
        .g-B { color: #0050a0; font-weight: 700; }
        .g-C { color: #996600; font-weight: 700; }
        .g-D { color: #cc6600; font-weight: 700; }
        .g-E { color: #b41e1e; font-weight: 700; }
        .g-dash { color: #ccc; }
 
        /* Summary cells */
        .summary-cell { background: #f0f4fb; font-weight: 700; }
 
        /* Footer */
        .sheet-footer {
            font-size: 6pt;
            color: #666;
            display: flex;
            justify-content: space-between;
            padding: 3px 6px;
            border-top: 1px solid #e0e0e0;
            margin-top: 2px;
        }
    """
 
 
def _grade_class(g):
    """Return CSS class for a grade letter."""
    return f'g-{g}' if g in ('A', 'B', 'C', 'D', 'E') else 'g-dash'
 
 
def _abbrev_subject(name):
    """Abbreviate long subject names for the rotating column header."""
    mapping = {
        'Physical Education': 'PHYSICAL ED.',
        'Information & Communications Technology (ICT)': 'ICT',
        'English Language': 'ENGLISH',
        'History & Political Education': 'HISTORY & POL.',
        'Christian Religious Education': 'CRE',
        'Entrepreneurship Education': 'ENTREPRENEUR',
        'Literature in English': 'LITERATURE',
        'Nutrition & Food Technology': 'FOOD & NUTRITION',
        'Subsidiary Computer Studies': 'SUB ICT',
        'Subsidiary Mathematics': 'SUB MATH',
        'General Paper': 'GEN. PAPER',
    }
    return mapping.get(name, name.upper())
 
 
def _render_olevel_pdf_html(sheets, term, year):
    """Build the full standalone HTML string for O-Level PDF."""
    rows_html = []
    for sheet in sheets:
        subs = sheet['subjects']
        # Build subject header row
        sub_headers = ''.join(
            f'<th class="rotate-header"><span class="rotate-label">{_abbrev_subject(s)}</span></th>' for s in subs
        )
        student_rows = []
        for i, s in enumerate(sheet['students'], 1):
            grade_cells = ''
            for sub in subs:
                g = s['grades'].get(sub, '-')
                if g != '-':
                    grade_cells += f'<td><span class="{_grade_class(g)}">{g}</span></td>'
                else:
                    grade_cells += '<td><span class="g-dash">—</span></td>'
            student_rows.append(f"""
                <tr>
                  <td>{i}</td>
                  <td class="name-cell">{s['name']}</td>
                  <td>{s['gender']}</td>
                  {grade_cells}
                  <td class="summary-cell">{s['total_score']}</td>
                  <td>{s['average_score']}</td>
                  <td style="font-size:5.5pt">{s['descriptor']}</td>
                  <td style="color:#001a4d;font-weight:700">{s['stream_rank']}</td>
                  <td style="color:#001a4d;font-weight:700">{s['class_rank']}</td>
                </tr>""")
 
        rows_html.append(f"""
        <div class="sheet">
          <div class="sheet-header">
            <div>
              <div class="school-name">HOLY CROSS LAKE VIEW S.S.S</div>
              <div class="school-sub">Sorted by Averages, per stream</div>
            </div>
            <div>
              <div class="class-name">{sheet['class_name']}</div>
              <div class="class-sub">Term {term}, {year} | {len(sheet['students'])} Students</div>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th style="width:18px">No.</th>
                <th style="width:90px;text-align:left">Student Name</th>
                <th style="width:22px">SEX</th>
                {sub_headers}
                <th class="summary" style="width:32px">TOTAL</th>
                <th class="summary" style="width:36px">AVG</th>
                <th class="summary" style="width:55px">DESCRIPTOR</th>
                <th class="summary" style="width:28px">STR RNK</th>
                <th class="summary" style="width:28px">CLS RNK</th>
              </tr>
            </thead>
            <tbody>{''.join(student_rows)}</tbody>
          </table>
          <div class="sheet-footer">
            <span><b>Note:</b> A(80-100) | B(70-79) | C(60-69) | D(50-59) | E(0-49)</span>
            <span>Academics Office | CKS-Tech | D@in Corp. | Joxe_verse</span>
          </div>
        </div>
        """)
 
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body>{''.join(rows_html)}</body></html>"""
 
 
def _render_alevel_pdf_html(sheets, term, year):
    """Build the full standalone HTML string for A-Level PDF."""
    rows_html = []
    for sheet in sheets:
        subs = sheet['subjects']
        sub_headers = ''.join(
            f'<th class="rotate-header"><span class="rotate-label">{_abbrev_subject(s)}</span></th>' for s in subs
        )
        student_rows = []
        for i, s in enumerate(sheet['students'], 1):
            grade_cells = ''
            for sub in subs:
                g = s['grades'].get(sub, '-')
                if g != '-':
                    grade_cells += f'<td><span class="{_grade_class(g)}">{g}</span></td>'
                else:
                    grade_cells += '<td><span class="g-dash">—</span></td>'
            student_rows.append(f"""
                <tr>
                  <td>{i}</td>
                  <td class="name-cell">{s['name']}</td>
                  <td>{s['gender']}</td>
                  <td style="font-size:5.5pt;font-weight:600">{s['combination']}</td>
                  {grade_cells}
                  <td class="summary-cell">{s['total_pts']}</td>
                  <td>{s['principal_pass']}</td>
                  <td style="color:#001a4d;font-weight:700">{s['subsidiary_grade']}</td>
                  <td style="color:#001a4d;font-weight:700">{s['rank']}</td>
                </tr>""")
 
        rows_html.append(f"""
        <div class="sheet">
          <div class="sheet-header">
            <div>
              <div class="school-name">HOLY CROSS LAKE VIEW S.S.S</div>
              <div class="school-sub">Sorted by Points, per stream</div>
            </div>
            <div>
              <div class="class-name">{sheet['class_name']}</div>
              <div class="class-sub">Term {term}, {year} | {len(sheet['students'])} Students</div>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th style="width:18px">No.</th>
                <th style="width:90px;text-align:left">Student Name</th>
                <th style="width:22px">SEX</th>
                <th style="width:45px">COMBO</th>
                {sub_headers}
                <th class="summary" style="width:28px">PTS</th>
                <th class="summary" style="width:36px">PRINC PASS</th>
                <th class="summary" style="width:28px">SUB PASS</th>
                <th class="summary" style="width:28px">CLASS POS.</th>
              </tr>
            </thead>
            <tbody>{''.join(student_rows)}</tbody>
          </table>
          <div class="sheet-footer">
            <span><b>Note:</b> A(80-100,5pts) | B(70-79,4pts) | C(60-69,3pts) | D(50-59,2pts) | E(0-49,1pt). Lower aggregate = better.</span>
            <span>Academics Office | CKS-Tech | D@in Corp. | Joxe_verse</span>
          </div>
        </div>
        """)
 
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body>{''.join(rows_html)}</body></html>"""
 


@app.route('/teacher/marks/save', methods=['POST'])
@teacher_or_admin
def teacher_save_marks():
    conn    = get_db()
    uid     = session['user_id']
    teacher = conn.execute("SELECT * FROM teachers WHERE user_id=?", (uid,)).fetchone()
    if not teacher:
        conn.close()
        return jsonify({'error': 'teacher not found'}), 403

    sp_id       = request.form.get('sp_id')
    term        = request.form.get('term')
    year        = request.form.get('year')
    student_ids = request.form.getlist('student_id')

    for sid in student_ids:
        for etype in ('BOT', 'MT', 'EOT'):
            score = request.form.get(f'score_{sid}_{etype}', '').strip()
            if score:
                try:
                    score_val = max(0.0, min(100.0, float(score)))
                    conn.execute(
                        "INSERT INTO marks (student_id,subject_paper_id,term,year,exam_type,"
                        "score,updated_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP) "
                        "ON CONFLICT(student_id,subject_paper_id,term,year,exam_type) "
                        "DO UPDATE SET score=excluded.score,updated_at=CURRENT_TIMESTAMP",
                        (sid, sp_id, term, year, etype, score_val)
                    )
                except ValueError:
                    pass
        comment = request.form.get(f'comment_{sid}', '').strip()
        conn.execute(
            "INSERT INTO subject_comments "
            "(student_id,subject_paper_id,term,year,comment,teacher_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(student_id,subject_paper_id,term,year) "
            "DO UPDATE SET comment=excluded.comment,teacher_id=excluded.teacher_id",
            (sid, sp_id, term, year, comment, teacher['id'])
        )

    conn.commit()
    conn.close()
    flash('Marks saved successfully.', 'success')
    class_id = request.form.get('class_id')
    return redirect(url_for('teacher_marks', class_id=class_id, sp_id=sp_id, term=term, year=year))


# ─── student routes ───────────────────────────────────────────────────────────

@app.route('/student/dashboard')
@login_required
def student_dashboard():
    if session['role'] not in ('student', 'admin'):
        return redirect(url_for('home'))
    conn    = get_db()
    uid     = session['user_id']
    student = conn.execute(
        "SELECT s.*, u.first_name, u.last_name, u.other_names, u.full_name, u.profile_pic, c.name as class_name, c.stream "
        "FROM students s JOIN users u ON s.user_id=u.id "
        "LEFT JOIN classes c ON s.class_id=c.id WHERE s.user_id=?", (uid,)
    ).fetchone()
    if not student:
        conn.close()
        return render_template('student/no_profile.html')

    reports = conn.execute(
        "SELECT * FROM reports WHERE student_id=? ORDER BY year DESC, term DESC, report_type LIMIT 10",
        (student['id'],)
    ).fetchall()
    latest  = conn.execute(
        "SELECT term, year FROM marks WHERE student_id=? ORDER BY year DESC, term DESC LIMIT 1",
        (student['id'],)
    ).fetchone()
    summary = []
    if latest:
        summary = conn.execute(
            "SELECT sp.paper_code, s.name as subject_name, m.exam_type, m.score "
            "FROM marks m JOIN subject_papers sp ON m.subject_paper_id=sp.id "
            "JOIN subjects s ON sp.subject_id=s.id "
            "WHERE m.student_id=? AND m.term=? AND m.year=? "
            "ORDER BY s.name, sp.paper_number, m.exam_type",
            (student['id'], latest['term'], latest['year'])
        ).fetchall()

    today    = date.today().isoformat()
    upcoming = conn.execute(
        "SELECT * FROM events WHERE event_date >= ? ORDER BY event_date LIMIT 5", (today,)
    ).fetchall()
    conn.close()
    return render_template('student/dashboard.html',
                           student=student, reports=reports,
                           summary=summary, latest=latest, upcoming=upcoming)


@app.route('/student/reports')
@login_required
def student_reports():
    if session['role'] not in ('student', 'admin'):
        return redirect(url_for('home'))
    conn    = get_db()
    uid     = session['user_id']
    student = conn.execute("SELECT * FROM students WHERE user_id=?", (uid,)).fetchone()
    if not student:
        conn.close()
        return render_template('student/no_profile.html')
    reports = conn.execute(
        "SELECT * FROM reports WHERE student_id=? ORDER BY year DESC, term DESC, report_type",
        (student['id'],)
    ).fetchall()
    conn.close()
    return render_template('student/reports.html', reports=reports, student=student)


@app.route('/reports/file/<path:filename>')
@login_required
def serve_report(filename):
    return send_from_directory(REPORTS_FOLDER, filename)


@app.route('/reports/download/<path:filename>')
@login_required
def download_report(filename):
    return send_from_directory(REPORTS_FOLDER, filename, as_attachment=True)


# ─── profile & settings ───────────────────────────────────────────────────────

@app.route('/profile')
@login_required
def profile():
    conn  = get_db()
    user  = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    extra = None
    if session['role'] == 'student':
        extra = conn.execute(
            "SELECT s.*, c.name as class_name, c.stream FROM students s "
            "LEFT JOIN classes c ON s.class_id=c.id WHERE s.user_id=?",
            (session['user_id'],)
        ).fetchone()
    elif session['role'] == 'teacher':
        extra = conn.execute(
            "SELECT * FROM teachers WHERE user_id=?", (session['user_id'],)
        ).fetchone()
    conn.close()
    return render_template('shared/profile.html', user=user, extra=extra)


@app.route('/profile/update', methods=['POST'])
@login_required
def profile_update():
    uid     = session['user_id']
    email   = request.form.get('email', '').strip()
    phone   = request.form.get('phone', '').strip()
    new_pw  = request.form.get('new_password', '').strip()
    curr_pw = request.form.get('current_password', '').strip()
    conn    = get_db()
    user    = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if new_pw:
        if not check_pw(curr_pw, user['password']):
            flash('Current password is incorrect.', 'danger')
            conn.close()
            return redirect(url_for('profile'))
        conn.execute("UPDATE users SET email=?,phone=?,password=? WHERE id=?",
                     (email, phone, hash_pw(new_pw), uid))
    else:
        conn.execute("UPDATE users SET email=?,phone=? WHERE id=?", (email, phone, uid))
    conn.commit()
    conn.close()
    flash('Profile updated.', 'success')
    return redirect(url_for('profile'))


@app.route('/settings')
@login_required
def settings():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return render_template('shared/settings.html', user=user)


# ─── error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='Page not found'), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403, message='Access denied'), 403


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, message='Internal server error'), 500


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    if '--no-watch' not in sys.argv and os.environ.get('DISABLE_RELOAD_SERVER', '0') != '1':
        subprocess.run([sys.executable, 'reload_server.py'] + sys.argv[1:])
        sys.exit(0)
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug)