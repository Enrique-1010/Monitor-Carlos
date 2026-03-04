import os, json, sqlite3, hashlib
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.join("data", "users.db")


# ======================
# Conexión / utilidades
# ======================

def _conn():
    """
    Devuelve una conexión SQLite con:
    - carpeta data/ creada si no existe
    - timeout ampliado para reducir "database is locked"
    - check_same_thread=False para uso con Dash
    - PRAGMAs para mejorar concurrencia (WAL) en despliegue web
    """
    os.makedirs("data", exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)

    try:
        con.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass

    # Concurrencia más estable (recomendado en apps web con SQLite)
    try:
        con.execute("PRAGMA journal_mode = WAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout = 5000;")  # 5s
    except Exception:
        pass

    return con


@contextmanager
def _get_conn():
    """
    Context manager que garantiza:
    - commit automático si todo va bien
    - cierre de la conexión SIEMPRE (también si hay excepción)
    """
    con = _conn()
    try:
        yield con
        con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass


def _dicts(cur):
    cols = [c[0] for c in cur.description]
    for row in cur.fetchall():
        yield {k: v for k, v in zip(cols, row)}


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cur = con.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]  # (cid, name, type, notnull, dflt_value, pk)
        return column in cols
    except Exception:
        return False


# ======================
# Migraciones versionadas
# ======================

def _ensure_schema_migrations(con: sqlite3.Connection):
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY,
            applied_at TEXT
        )"""
    )


def _get_db_version(con: sqlite3.Connection) -> int:
    try:
        cur = con.cursor()
        cur.execute("SELECT MAX(version) FROM schema_migrations")
        v = cur.fetchone()[0]
        return int(v) if v is not None else 0
    except Exception:
        return 0


def _set_db_version(con: sqlite3.Connection, version: int):
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
        (int(version), datetime.utcnow().isoformat()),
    )


def migrate_db():
    """
    Ejecuta migraciones incrementales para poder actualizar en producción sin romper la DB.

    Nota: el proyecto ya tenía 'ALTER TABLE ...' sueltos. Aquí lo volvemos más robusto:
    - Cada migración se ejecuta una sola vez.
    - Aun así, cada paso chequea columnas antes de hacer ALTER.
    """
    with _get_conn() as con:
        _ensure_schema_migrations(con)
        current = _get_db_version(con)

        # --------------------------
        # Migración 10: columnas legacy
        # --------------------------
        if current < 10:
            cur = con.cursor()
            # users.coach_id (legacy)
            if not _has_column(con, "users", "coach_id"):
                try:
                    cur.execute("ALTER TABLE users ADD COLUMN coach_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # ecg_files.created_at (ya existía como migración suave)
            if not _has_column(con, "ecg_files", "created_at"):
                try:
                    cur.execute("ALTER TABLE ecg_files ADD COLUMN created_at TEXT")
                except sqlite3.OperationalError:
                    pass

            _set_db_version(con, 10)

        # --------------------------
        # Migración 20: sesiones + session_id en tablas existentes
        # --------------------------
        if current < 20:
            cur = con.cursor()

            # Tabla sessions (nueva)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    athlete_id INTEGER NOT NULL,
                    created_by INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    sport TEXT,
                    notes TEXT,
                    status TEXT,
                    created_at TEXT,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
                )"""
            )

            # session_id en ecg_files
            if not _has_column(con, "ecg_files", "session_id"):
                try:
                    cur.execute("ALTER TABLE ecg_files ADD COLUMN session_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # session_id en questionnaires
            if not _has_column(con, "questionnaires", "session_id"):
                try:
                    cur.execute("ALTER TABLE questionnaires ADD COLUMN session_id INTEGER")
                except sqlite3.OperationalError:
                    pass

            # session_id en métricas IMU/EMG/RESP (recomendado para informes por sesión)
            for table in ("imu_metrics", "emg_metrics", "resp_metrics"):
                if not _has_column(con, table, "session_id"):
                    try:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN session_id INTEGER")
                    except sqlite3.OperationalError:
                        pass

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_athlete ON sessions(athlete_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_ecg_files_user ON ecg_files(user_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_questionnaires_user ON questionnaires(user_id)")
            except Exception:
                pass

            _set_db_version(con, 20)

        # --------------------------
        # Migración 30: adopción + equipos
        # --------------------------
        if current < 30:
            cur = con.cursor()

            # Coach adopta deportistas (roster)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS coach_athletes(
                    coach_id INTEGER NOT NULL,
                    athlete_id INTEGER NOT NULL,
                    created_at TEXT,
                    PRIMARY KEY (coach_id, athlete_id),
                    FOREIGN KEY (coach_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # Equipos
            cur.execute(
                """CREATE TABLE IF NOT EXISTS teams(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    coach_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    sport TEXT,
                    created_at TEXT,
                    FOREIGN KEY (coach_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS team_members(
                    team_id INTEGER NOT NULL,
                    athlete_id INTEGER NOT NULL,
                    role_label TEXT,
                    created_at TEXT,
                    PRIMARY KEY (team_id, athlete_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
                    FOREIGN KEY (athlete_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_coach_athletes_coach ON coach_athletes(coach_id)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id)")
            except Exception:
                pass

            _set_db_version(con, 30)

        # --------------------------
        # Migración 40: peso + nutrición (persistencia)
        # --------------------------
        if current < 40:
            cur = con.cursor()

            # Registros de peso (por usuario)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS weights(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    target_kg REAL,
                    note TEXT,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # Registros de nutrición (por usuario)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS nutrition_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    adherence_pct REAL NOT NULL,
                    kcal REAL,
                    note TEXT,
                    created_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )"""
            )

            # índices útiles
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_weights_user_date ON weights(user_id, date)")
            except Exception:
                pass
            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nutrition_user_date ON nutrition_logs(user_id, date)")
            except Exception:
                pass

            _set_db_version(con, 40)


# ======================
# Inicialización DB
# ======================

def init_db():
    """
    Inicializa la base de datos con las tablas necesarias.
    No borra nada si ya existen: solo crea lo que falta.

    Importante:
    - Mantiene compatibilidad con el código actual.
    - Además ejecuta migrate_db() para nuevas features (sesiones/equipos/adopción).
    """
    with _get_conn() as con:
        cur = con.cursor()

        # ---------- Usuarios ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            role TEXT,
            sport TEXT,
            password_hash BLOB,
            created_at TEXT
        )"""
        )
        # Aseguramos que exista la columna coach_id (relación coach -> deportistas) (legacy)
        if not _has_column(con, "users", "coach_id"):
            try:
                cur.execute("ALTER TABLE users ADD COLUMN coach_id INTEGER")
            except sqlite3.OperationalError:
                pass

        # ---------- Sensores por usuario ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS user_sensors(
            user_id INTEGER,
            sensor_code TEXT,
            PRIMARY KEY(user_id, sensor_code)
        )"""
        )

        # ---------- Archivos ECG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ecg_files(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            fs INTEGER
        )"""
        )
        # Migración suave: añadir created_at si no existe
        if not _has_column(con, "ecg_files", "created_at"):
            try:
                cur.execute("ALTER TABLE ecg_files ADD COLUMN created_at TEXT")
            except sqlite3.OperationalError:
                pass

        # ---------- Métricas ECG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS ecg_metrics(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ecg_file_id INTEGER NOT NULL,
            bpm REAL,
            sdnn REAL,
            rmssd REAL,
            n_peaks INTEGER,
            created_at TEXT
        )"""
        )

        # ---------- Cuestionarios ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS questionnaires(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            ts TEXT,
            answers_json TEXT,
            wellness_score REAL,
            rpe REAL,
            duration_min REAL
        )"""
        )

        # ---------- Métricas IMU (golpes) ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS imu_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            n_hits INTEGER,
            hits_per_min REAL,
            mean_int_g REAL,
            max_int_g REAL
        )"""
        )

        # ---------- Métricas EMG ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS emg_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            rms REAL,
            peak REAL,
            fatigue REAL
        )"""
        )

        # ---------- Métricas banda respiratoria ----------
        cur.execute(
            """CREATE TABLE IF NOT EXISTS resp_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            ts TEXT DEFAULT (datetime('now')),
            n_breaths INTEGER,
            br_min REAL,
            mean_period REAL
        )"""
        )

    # Ejecuta migraciones nuevas (idempotente y versionado)
    try:
        migrate_db()
    except Exception:
        # No matamos la app si una migración falla; pero en dev lo verás en consola.
        pass


# ======================
# Users / Auth
# ======================

def _hash_pw(pw: str):
    """
    Hash de password. Intenta usar bcrypt y si no, SHA256 como fallback.
    """
    try:
        import bcrypt
        return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    except Exception:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8")


def _check_pw(pw: str, hashed: bytes):
    try:
        import bcrypt
        return bcrypt.checkpw(pw.encode("utf-8"), hashed)
    except Exception:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest().encode("utf-8") == hashed


def create_user(name, email, pw, role, sport, coach_id=None):
    """
    Crea usuario completo con email y password hasheado.
    Pensado para login real (deportistas y coaches).
    """
    with _get_conn() as con:
        cur = con.cursor()
        hashed = _hash_pw(pw)
        if coach_id is None:
            cur.execute(
                "INSERT INTO users(name,email,role,sport,password_hash,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (name, email, role, sport, hashed, datetime.utcnow().isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO users(name,email,role,sport,password_hash,created_at,coach_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (name, email, role, sport, hashed, datetime.utcnow().isoformat(), coach_id),
            )
        return cur.lastrowid


def get_user_by_email(email: str):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id "
            "FROM users WHERE email=?",
            (email,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id"]
    return {k: v for k, v in zip(cols, row)}


def get_user_by_id(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,password_hash,created_at,coach_id "
            "FROM users WHERE id=?",
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "password_hash", "created_at", "coach_id"]
    return {k: v for k, v in zip(cols, row)}


def list_users():
    """
    Lista TODOS los usuarios (coaches y deportistas).
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT id,name,role,sport,created_at,coach_id FROM users ORDER BY id DESC")
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "sport": r[3],
                "created_at": r[4],
                "coach_id": r[5],
            }
        )
    return out


def list_coaches():
    """
    Lista sólo usuarios con rol 'coach'.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,email,role,sport,created_at "
            "FROM users WHERE role='coach' ORDER BY id DESC"
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "email": r[2],
                "role": r[3],
                "sport": r[4],
                "created_at": r[5],
            }
        )
    return out


def list_athletes_for_coach(coach_id: int):
    """
    LEGACY: Lista deportistas asignados a un coach concreto vía users.coach_id.
    (Se mantiene para no romper la app mientras migras a adopción/equipos.)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,name,role,sport,created_at,coach_id "
            "FROM users WHERE role='deportista' AND coach_id=? ORDER BY id DESC",
            (coach_id,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "sport": r[3],
                "created_at": r[4],
                "coach_id": r[5],
            }
        )
    return out


def get_user_coach(user_id: int):
    """
    Devuelve los datos del coach asociado a un deportista (o None si no tiene).
    (Legacy, depende de users.coach_id)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """SELECT c.id, c.name, c.email, c.role, c.sport, c.created_at
               FROM users u
               JOIN users c ON u.coach_id = c.id
               WHERE u.id=?""",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "name", "email", "role", "sport", "created_at"]
    return {k: v for k, v in zip(cols, row)}


def add_user(name, sport=None, role="deportista", coach_id=None):
    """
    LEGACY: alta rápida de usuario SIN email ni password.
    Se mantiene por compatibilidad con código antiguo.
    """
    with _get_conn() as con:
        cur = con.cursor()
        if coach_id is None:
            cur.execute(
                "INSERT INTO users(name,role,sport,created_at) VALUES(?,?,?,?)",
                (name, role or "deportista", sport, datetime.utcnow().isoformat()),
            )
        else:
            cur.execute(
                "INSERT INTO users(name,role,sport,created_at,coach_id) VALUES(?,?,?,?,?)",
                (name, role or "deportista", sport, datetime.utcnow().isoformat(), coach_id),
            )


def coach_create_athlete_with_login(coach_id: int, name: str, email: str, pw: str, sport: str):
    """
    LEGACY: Utilidad para panel del coach (crea deportista con login y lo asigna por coach_id).
    Se mantiene por compatibilidad, pero se recomienda migrar a adopción.
    """
    return create_user(
        name=name,
        email=email,
        pw=pw,
        role="deportista",
        sport=sport,
        coach_id=coach_id,
    )


def delete_user(uid: int):
    """
    Borra usuario y todo lo asociado (sensores, ECG, métricas, cuestionarios).
    """
    with _get_conn() as con:
        cur = con.cursor()

        cur.execute(
            "DELETE FROM ecg_metrics WHERE ecg_file_id IN "
            "(SELECT id FROM ecg_files WHERE user_id=?)",
            (uid,),
        )
        cur.execute("DELETE FROM ecg_files WHERE user_id=?", (uid,))

        cur.execute("DELETE FROM imu_metrics WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM emg_metrics WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM resp_metrics WHERE user_id=?", (uid,))

        cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
        cur.execute("DELETE FROM questionnaires WHERE user_id=?", (uid,))

        # sesiones (si existen)
        try:
            cur.execute("DELETE FROM sessions WHERE athlete_id=?", (uid,))
        except Exception:
            pass

        # peso/nutrición (si existen; también están en FK CASCADE)
        try:
            cur.execute("DELETE FROM weights WHERE user_id=?", (uid,))
        except Exception:
            pass
        try:
            cur.execute("DELETE FROM nutrition_logs WHERE user_id=?", (uid,))
        except Exception:
            pass



        cur.execute("DELETE FROM users WHERE id=?", (uid,))


def delete_user_as_coach(coach_id: int, uid: int):
    """
    BORRADO SEGURO para coaches:
    - Solo permite borrar si el usuario existe
    - y si es deportista
    - y si su coach_id coincide con el coach que lo solicita.
    Devuelve True si borró, False si no estaba permitido.
    """
    u = get_user_by_id(int(uid))
    if not u:
        return False
    if (u.get("role") or "") != "deportista":
        return False
    try:
        if int(u.get("coach_id") or -1) != int(coach_id):
            return False
    except Exception:
        return False

    delete_user(int(uid))
    return True


# ======================
# Adopción coach <-> deportistas (nuevo)
# ======================

def search_athletes(text: str = "", sport: str = None, limit: int = 50):
    """
    Busca deportistas por:
    - nombre (LIKE) opcional
    - deporte exacto opcional

    Cambio mínimo (compatibilidad con app_updated.py):
    - Antes requería 'text' obligatorio.
    - Ahora permite buscar SOLO por deporte (text vacío), para que el coach filtre por deporte.
    - Si no hay ni text ni sport, devuelve [] (para no listar toda la BD sin querer).
    """
    text = (text or "").strip()
    sport = (sport or "").strip() if sport is not None else None
    sport = sport if sport else None

    if not text and not sport:
        return []

    like = f"%{text}%" if text else None

    with _get_conn() as con:
        cur = con.cursor()
        if text and sport:
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (name LIKE ?) AND (sport = ?)
                ORDER BY name
                LIMIT ?
                """,
                (like, sport, int(limit)),
            )
        elif text and not sport:
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (name LIKE ?)
                ORDER BY name
                LIMIT ?
                """,
                (like, int(limit)),
            )
        else:
            # sport y sin text
            cur.execute(
                """
                SELECT id, name, role, sport, created_at, coach_id
                FROM users
                WHERE role='deportista' AND (sport = ?)
                ORDER BY name
                LIMIT ?
                """,
                (sport, int(limit)),
            )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "sport": r[3],
            "created_at": r[4],
            "coach_id": r[5],
        }
        for r in rows
    ]


def adopt_athlete(coach_id: int, athlete_id: int):
    """
    Añade (coach, atleta) a coach_athletes. No altera users.coach_id por defecto.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES(?,?,?)",
            (int(coach_id), int(athlete_id), datetime.utcnow().isoformat()),
        )


def adopt_athlete_set_primary_if_empty(coach_id: int, athlete_id: int):
    """
    Adopta al atleta y, si NO tiene coach_id (legacy), lo setea para que:
    - 'Contactar a mi coach' siga teniendo sentido para el deportista.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO coach_athletes(coach_id, athlete_id, created_at) VALUES(?,?,?)",
            (int(coach_id), int(athlete_id), datetime.utcnow().isoformat()),
        )
        # si coach_id está vacío, lo seteamos
        try:
            cur.execute(
                """
                UPDATE users
                SET coach_id=?
                WHERE id=? AND (coach_id IS NULL OR coach_id='')
                """,
                (int(coach_id), int(athlete_id)),
            )
        except Exception:
            pass


def remove_adopted_athlete(coach_id: int, athlete_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM coach_athletes WHERE coach_id=? AND athlete_id=?",
            (int(coach_id), int(athlete_id)),
        )


def list_my_athletes(coach_id: int):
    """
    Devuelve deportistas adoptados por un coach (JOIN coach_athletes + users).
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT u.id, u.name, u.role, u.sport, u.created_at, u.coach_id
            FROM coach_athletes ca
            JOIN users u ON u.id = ca.athlete_id
            WHERE ca.coach_id = ?
            ORDER BY u.name
            """,
            (int(coach_id),),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "sport": r[3],
            "created_at": r[4],
            "coach_id": r[5],
        }
        for r in rows
    ]


def list_roster_for_coach(coach_id: int):
    """
    Roster unificado para app_updated.py.
    Une:
      - adopción (coach_athletes -> list_my_athletes)
      - legacy (users.coach_id -> list_athletes_for_coach)
    sin duplicados.
    """
    out = []
    seen = set()

    try:
        a1 = list_my_athletes(int(coach_id)) or []
    except Exception:
        a1 = []

    try:
        a2 = list_athletes_for_coach(int(coach_id)) or []
    except Exception:
        a2 = []

    for a in (a1 + a2):
        aid = a.get("id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        out.append(a)

    return out


# ======================
# Equipos (nuevo)
# ======================

def create_team(coach_id: int, name: str, sport: str = None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Nombre de equipo requerido")
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO teams(coach_id, name, sport, created_at) VALUES(?,?,?,?)",
            (int(coach_id), name, sport, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_teams(coach_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id, coach_id, name, sport, created_at FROM teams WHERE coach_id=? ORDER BY id DESC",
            (int(coach_id),),
        )
        rows = cur.fetchall()
    return [
        {"id": r[0], "coach_id": r[1], "name": r[2], "sport": r[3], "created_at": r[4]}
        for r in rows
    ]


def add_team_member(team_id: int, athlete_id: int, role_label: str = None):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO team_members(team_id, athlete_id, role_label, created_at)
            VALUES(?,?,?,?)
            """,
            (int(team_id), int(athlete_id), (role_label or "").strip() or None, datetime.utcnow().isoformat()),
        )


def remove_team_member(team_id: int, athlete_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM team_members WHERE team_id=? AND athlete_id=?",
            (int(team_id), int(athlete_id)),
        )


def list_team_members(team_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT u.id, u.name, u.sport, tm.role_label, tm.created_at
            FROM team_members tm
            JOIN users u ON u.id = tm.athlete_id
            WHERE tm.team_id = ?
            ORDER BY u.name
            """,
            (int(team_id),),
        )
        rows = cur.fetchall()
    return [
        {"athlete_id": r[0], "name": r[1], "sport": r[2], "role_label": r[3], "added_at": r[4]}
        for r in rows
    ]


# ======================
# Sesiones (nuevo)
# ======================

def create_session(athlete_id: int, created_by: int = None, sport: str = None, notes: str = None):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO sessions(athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                int(athlete_id),
                int(created_by) if created_by is not None else None,
                datetime.utcnow().isoformat(),
                None,
                sport,
                (notes or "").strip() or None,
                "open",
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def close_session(session_id: int, ts_end: str = None):
    end = ts_end or datetime.utcnow().isoformat()
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE sessions SET ts_end=?, status='closed' WHERE id=?",
            (end, int(session_id)),
        )


def get_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE id=?
            """,
            (int(session_id),),
        )
        row = cur.fetchone()
    if not row:
        return None
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return {k: v for k, v in zip(cols, row)}


def list_sessions(athlete_id: int, limit: int = 50):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE athlete_id=?
            ORDER BY datetime(ts_start) DESC
            LIMIT ?
            """,
            (int(athlete_id), int(limit)),
        )
        rows = cur.fetchall()
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return [{k: v for k, v in zip(cols, r)} for r in rows]


def get_previous_session(athlete_id: int, session_id: int):
    """
    Devuelve la sesión inmediatamente anterior a 'session_id' (por ts_start).
    Útil para comparativas en informes.
    """
    s = get_session(session_id)
    if not s:
        return None
    ts = s.get("ts_start")
    if not ts:
        return None

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, athlete_id, created_by, ts_start, ts_end, sport, notes, status, created_at
            FROM sessions
            WHERE athlete_id=? AND datetime(ts_start) < datetime(?)
            ORDER BY datetime(ts_start) DESC
            LIMIT 1
            """,
            (int(athlete_id), ts),
        )
        row = cur.fetchone()

    if not row:
        return None
    cols = ["id", "athlete_id", "created_by", "ts_start", "ts_end", "sport", "notes", "status", "created_at"]
    return {k: v for k, v in zip(cols, row)}


# ======================
# Asignación coach <-> deportistas (legacy)
# ======================

def list_unassigned_athletes():
    """
    LEGACY: Devuelve deportistas que todavía NO tienen coach_id asignado.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, name, sport, created_at
            FROM users
            WHERE role='deportista'
              AND (coach_id IS NULL OR coach_id = '')
            ORDER BY name
            """
        )
        rows = cur.fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "name": r[1],
            "sport": r[2],
            "created_at": r[3],
        })
    return out


def assign_athlete_to_coach(user_id: int, coach_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET coach_id=? WHERE id=?",
            (int(coach_id), int(user_id))
        )


def remove_athlete_from_coach(user_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "UPDATE users SET coach_id=NULL WHERE id=?",
            (int(user_id),)
        )


def set_athlete_coach(athlete_id: int, coach_id: int):
    assign_athlete_to_coach(athlete_id, coach_id)


# ======================
# User sensors
# ======================

def get_user_sensors(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT sensor_code FROM user_sensors WHERE user_id=?", (uid,))
        rows = cur.fetchall()
    return [r[0] for r in rows]


def set_user_sensors(uid: int, codes):
    codes = codes or []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM user_sensors WHERE user_id=?", (uid,))
        for c in codes:
            cur.execute(
                "INSERT INTO user_sensors(user_id,sensor_code) VALUES(?,?)",
                (uid, c),
            )


# ======================
# ECG files
# ======================

def add_ecg_file(uid: int, filename: str, fs: int, session_id: int = None):
    """
    Añade archivo ECG. Mantiene compatibilidad (session_id opcional).
    """
    with _get_conn() as con:
        cur = con.cursor()
        # si la columna session_id existe, la usamos
        if _has_column(con, "ecg_files", "session_id"):
            cur.execute(
                "INSERT INTO ecg_files(user_id,filename,fs,created_at,session_id) "
                "VALUES(?,?,?,?,?)",
                (uid, filename, fs, datetime.utcnow().isoformat(), session_id),
            )
        else:
            cur.execute(
                "INSERT INTO ecg_files(user_id,filename,fs,created_at) "
                "VALUES(?,?,?,?)",
                (uid, filename, fs, datetime.utcnow().isoformat()),
            )
        return cur.lastrowid


def list_ecg_files(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "ecg_files", "session_id"):
            cur.execute(
                "SELECT id,user_id,filename,fs,created_at,session_id "
                "FROM ecg_files WHERE user_id=? ORDER BY id DESC",
                (uid,),
            )
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "filename": r[2],
                        "fs": r[3],
                        "created_at": r[4],
                        "session_id": r[5],
                    }
                )
            return out
        else:
            cur.execute(
                "SELECT id,user_id,filename,fs,created_at "
                "FROM ecg_files WHERE user_id=? ORDER BY id DESC",
                (uid,),
            )
            rows = cur.fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "filename": r[2],
                        "fs": r[3],
                        "created_at": r[4],
                    }
                )
            return out


def list_ecg_files_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "ecg_files", "session_id"):
            return []
        cur.execute(
            "SELECT id,user_id,filename,fs,created_at,session_id FROM ecg_files WHERE session_id=? ORDER BY id DESC",
            (int(session_id),),
        )
        return list(_dicts(cur))


# ======================
# ECG metrics (HRV)
# ======================

def save_ecg_metrics(ecg_id: int, bpm: float, sdnn: float, rmssd: float, peaks_count: int):
    """
    Guarda una fila de métricas. Se mantiene igual para no romper histórico existente.
    (Si luego quieres evitar spam por sliders, usa save_ecg_metrics_latest().)
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO ecg_metrics(ecg_file_id,bpm,sdnn,rmssd,n_peaks,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (ecg_id, bpm, sdnn, rmssd, peaks_count, datetime.utcnow().isoformat()),
        )


def save_ecg_metrics_latest(ecg_id: int, bpm: float, sdnn: float, rmssd: float, peaks_count: int):
    """
    Variante para UI (sliders): mantiene SOLO la última métrica por archivo ECG.
    """
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM ecg_metrics WHERE ecg_file_id=?", (int(ecg_id),))
        cur.execute(
            "INSERT INTO ecg_metrics(ecg_file_id,bpm,sdnn,rmssd,n_peaks,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (ecg_id, bpm, sdnn, rmssd, peaks_count, datetime.utcnow().isoformat()),
        )


def get_last_ecg_metrics(uid: int):
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """SELECT m.bpm,m.sdnn,m.rmssd
               FROM ecg_metrics m
               JOIN ecg_files f ON f.id=m.ecg_file_id
               WHERE f.user_id=?
               ORDER BY m.id DESC LIMIT 1""",
            (uid,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"bpm": row[0], "sdnn": row[1], "rmssd": row[2]}


# ======================
# Questionnaires
# ======================

def save_questionnaire(uid: int, answers: dict,
                       wellness: float, rpe: float = None, duration: float = None,
                       session_id: int = None):
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "questionnaires", "session_id"):
            cur.execute(
                "INSERT INTO questionnaires(user_id,ts,answers_json,wellness_score,rpe,duration_min,session_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (uid, datetime.utcnow().isoformat(), json.dumps(answers),
                 wellness, rpe, duration, session_id),
            )
        else:
            cur.execute(
                "INSERT INTO questionnaires(user_id,ts,answers_json,wellness_score,rpe,duration_min) "
                "VALUES(?,?,?,?,?,?)",
                (uid, datetime.utcnow().isoformat(), json.dumps(answers),
                 wellness, rpe, duration),
            )


def list_questionnaires(uid: int):
    # ✅ FIX: fetchall dentro del with
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min "
            "FROM questionnaires WHERE user_id=? ORDER BY id DESC",
            (uid,),
        )
        rows = cur.fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "user_id": r[1],
                "ts": r[2],
                "answers_json": r[3],
                "wellness_score": r[4],
                "rpe": r[5],
                "duration_min": r[6],
            }
        )
    return out


def list_questionnaires_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "questionnaires", "session_id"):
            return []
        cur.execute(
            """
            SELECT id,user_id,ts,answers_json,wellness_score,rpe,duration_min,session_id
            FROM questionnaires
            WHERE session_id=?
            ORDER BY id DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


# ======================
# Peso / Nutrición (nuevo)
# ======================

def add_weight_entry(user_id: int, date: str, weight_kg: float, target_kg: float = None, note: str = None):
    """
    Guarda un registro de peso persistente (DB).
    - date: "YYYY-MM-DD"
    """
    if not user_id:
        raise ValueError("user_id requerido")
    if date is None:
        date = datetime.utcnow().date().isoformat()

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO weights(user_id, date, weight_kg, target_kg, note, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(user_id),
                str(date),
                float(weight_kg),
                float(target_kg) if target_kg is not None else None,
                (note or "").strip() or None,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_weight_entries(user_id: int, limit: int = 200):
    """
    Devuelve registros de peso del usuario (más recientes primero por fecha/id).
    """
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, user_id, date, weight_kg, target_kg, note, created_at
            FROM weights
            WHERE user_id=?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "user_id": r[1],
            "date": r[2],
            "weight": r[3],
            "target": r[4],
            "note": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_latest_weight_entry(user_id: int):
    rows = list_weight_entries(int(user_id), limit=1)
    return rows[0] if rows else None


def delete_weight_entry(entry_id: int, user_id: int = None):
    """
    Elimina un registro de peso (opcionalmente validando user_id).
    """
    if not entry_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if user_id is None:
            cur.execute("DELETE FROM weights WHERE id=?", (int(entry_id),))
        else:
            cur.execute("DELETE FROM weights WHERE id=? AND user_id=?", (int(entry_id), int(user_id)))


def add_nutrition_entry(user_id: int, date: str, adherence_pct: float, kcal: float = None, note: str = None):
    """
    Guarda un registro de nutrición persistente (DB).
    - adherence_pct: 0..100
    - date: "YYYY-MM-DD"
    """
    if not user_id:
        raise ValueError("user_id requerido")
    if date is None:
        date = datetime.utcnow().date().isoformat()

    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO nutrition_logs(user_id, date, adherence_pct, kcal, note, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                int(user_id),
                str(date),
                float(adherence_pct),
                float(kcal) if kcal is not None else None,
                (note or "").strip() or None,
                datetime.utcnow().isoformat(),
            ),
        )
        return cur.lastrowid


def list_nutrition_entries(user_id: int, limit: int = 200):
    """
    Devuelve registros de nutrición del usuario (más recientes primero por fecha/id).
    """
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, user_id, date, adherence_pct, kcal, note, created_at
            FROM nutrition_logs
            WHERE user_id=?
            ORDER BY date DESC, id DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "user_id": r[1],
            "date": r[2],
            "adherence": r[3],
            "kcal": r[4],
            "note": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def get_latest_nutrition_entry(user_id: int):
    rows = list_nutrition_entries(int(user_id), limit=1)
    return rows[0] if rows else None


def delete_nutrition_entry(entry_id: int, user_id: int = None):
    """
    Elimina un registro de nutrición (opcionalmente validando user_id).
    """
    if not entry_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if user_id is None:
            cur.execute("DELETE FROM nutrition_logs WHERE id=?", (int(entry_id),))
        else:
            cur.execute("DELETE FROM nutrition_logs WHERE id=? AND user_id=?", (int(entry_id), int(user_id)))


# ======================
# Métricas IMU / EMG / RESP
# ======================

def save_imu_metrics(user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "imu_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO imu_metrics (user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_hits),
                 float(hits_per_min), float(mean_int_g), float(max_int_g), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO imu_metrics (user_id, filename, n_hits, hits_per_min, mean_int_g, max_int_g)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_hits),
                 float(hits_per_min), float(mean_int_g), float(max_int_g)),
            )


def list_imu_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g
            FROM imu_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "n_hits": r[3],
            "hits_per_min": r[4],
            "mean_int_g": r[5],
            "max_int_g": r[6],
        }
        for r in rows
    ]


def list_imu_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "imu_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, n_hits, hits_per_min, mean_int_g, max_int_g, session_id
            FROM imu_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


def save_emg_metrics(user_id, filename, rms, peak, fatigue, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "emg_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO emg_metrics (user_id, filename, rms, peak, fatigue, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, float(rms), float(peak), float(fatigue), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO emg_metrics (user_id, filename, rms, peak, fatigue)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, float(rms), float(peak), float(fatigue)),
            )


def list_emg_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, rms, peak, fatigue
            FROM emg_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "rms": r[3],
            "peak": r[4],
            "fatigue": r[5],
        }
        for r in rows
    ]


def list_emg_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "emg_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, rms, peak, fatigue, session_id
            FROM emg_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))


def save_resp_metrics(user_id, filename, n_breaths, br_min, mean_period, session_id: int = None):
    if not user_id:
        return
    with _get_conn() as con:
        cur = con.cursor()
        if _has_column(con, "resp_metrics", "session_id"):
            cur.execute(
                """
                INSERT INTO resp_metrics (user_id, filename, n_breaths, br_min, mean_period, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_breaths),
                 float(br_min), float(mean_period), session_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO resp_metrics (user_id, filename, n_breaths, br_min, mean_period)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(user_id), filename, int(n_breaths),
                 float(br_min), float(mean_period)),
            )


def list_resp_metrics(user_id):
    if not user_id:
        return []
    with _get_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            SELECT id, filename, ts, n_breaths, br_min, mean_period
            FROM resp_metrics
            WHERE user_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(user_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "ts": r[2],
            "n_breaths": r[3],
            "br_min": r[4],
            "mean_period": r[5],
        }
        for r in rows
    ]


def list_resp_metrics_by_session(session_id: int):
    with _get_conn() as con:
        cur = con.cursor()
        if not _has_column(con, "resp_metrics", "session_id"):
            return []
        cur.execute(
            """
            SELECT id, user_id, filename, ts, n_breaths, br_min, mean_period, session_id
            FROM resp_metrics
            WHERE session_id = ?
            ORDER BY datetime(ts) DESC
            """,
            (int(session_id),),
        )
        return list(_dicts(cur))