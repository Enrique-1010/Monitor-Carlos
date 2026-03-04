import os, io, base64, json, csv, webbrowser, importlib, traceback
from threading import Timer
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go

from flask import Flask, session
import dash
from dash import Dash, html, dcc, Input, Output, State, callback_context
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate

import db
import sensors as S
import questionnaires as Q

import pages.wellbeing as wellbeing_page
# ====== NUEVAS VISTAS (clases) ======
from views.signals_view import SignalsView
from views.sensors_view import SensorsView
from views.compare_view import CompareView

# ====== Flask + Dash ======
server = Flask(__name__)
server.secret_key = os.environ.get("POWERSYNC_SECRET", "dev-secret-change-me")

app = dash.Dash(
    __name__,
    server=server,
    title="PowerSync",
    suppress_callback_exceptions=True
)

# ====== DB init ======
db.init_db()

# ====== Instancias de vistas nuevas ======
signals_view = SignalsView(app, db, S)
sensors_view = SensorsView(app, db, S)
compare_view = CompareView(app, db, S)

# ====== Layout (CS-003 — Sidebar Pro • Desktop) ======
SIDEBAR_W = 272  # px (match assets/10_theme.css)
PAGE_COLLAPSED_MARGIN = 40  # px (cuando el sidebar está colapsado)


def h2(txt):
    return html.H2(txt, style={"margin": "6px 0 12px"})


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v




def _coach_roster(coach_id: int):
    """
    Roster unificado del coach.
    - Incluye adopciones (si tu db.py ya soporta roster).
    - Incluye legacy (users.coach_id) para no romper lo previo.
    """
    if not coach_id:
        return []

    out = []
    seen = set()

    # Prioriza roster/adopción si existe
    for fn in ("list_roster_for_coach", "list_my_athletes", "list_athletes_for_coach"):
        if hasattr(db, fn):
            try:
                rows = getattr(db, fn)(int(coach_id)) or []
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    rid = r.get("id")
                    if rid is None or rid in seen:
                        continue
                    seen.add(rid)
                    out.append(r)
            except Exception:
                pass

    return out

# ====== Sidebar (CS-003 — Pro) ======
def _is_active(pathname: str, href: str) -> bool:
    p = pathname or ""
    if href == "/":
        return p in ("/", "/inicio", "/home", "")
    return p == href


def _nav_link(label: str, href: str, icon: str, pathname: str):
    cls = "nav-link" + (" active" if _is_active(pathname, href) else "")
    return dcc.Link(
        [
            html.Img(src=f"/assets/icons/{icon}", className="nav-ico"),
            html.Span(label, className="nav-label"),
        ],
        href=href,
        className=cls,
    )


def _nav_section(title: str, items):
    return html.Div(
        [html.Div(title, className="nav-section-title")] + items,
        className="nav-section",
    )


def _sidebar_links(pathname: str):
    logged = bool(session.get("user_id"))
    role = _to_str(session.get("role")) or "no autenticado"
    name = _to_str(session.get("name")) if session.get("name") else None

    # Normaliza strings en sesión (evita bytes/encoding raros)
    session["role"] = role
    if name is not None:
        session["name"] = name

    # Meta del usuario (arriba del menú)
    if logged:
        meta = html.Div(
            [
                html.Div(
                    [
                        html.Div(name or "Usuario", className="sidebar-user__name"),
                        html.Div("Sesión activa", className="sidebar-user__sub"),
                    ],
                    className="sidebar-user__text",
                ),
                html.Span(role, className="badge-role"),
            ],
            className="sidebar-user",
        )
    else:
        meta = html.Div(
            [
                html.Div(
                    [
                        html.Div("PowerSync", className="sidebar-user__name"),
                        html.Div("Inicia sesión para continuar", className="sidebar-user__sub"),
                    ],
                    className="sidebar-user__text",
                ),
                html.Span("offline", className="badge-role"),
            ],
            className="sidebar-user",
        )

    sections = []
    bottom = []

    if not logged:
        sections = [
            _nav_section(
                "Cuenta",
                [
                    _nav_link("Iniciar sesión", "/login", "profile.svg", pathname),
                    _nav_link("Registrarse", "/registro", "profile.svg", pathname),
                ],
            )
        ]
    else:
        if role == "deportista":
            sections = [
                _nav_section(
                    "Dashboard",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Resumen de hoy", "/sesion", "session.svg", pathname),
                        _nav_link("Perfil", "/dashboard", "profile.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Rendimiento",
                    [
                        _nav_link("Señales y métricas", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar", "/comparar", "compare.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Bienestar",
                    [
                        _nav_link("Check-in", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Tendencias", "/historico", "history.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Seguimiento",
                    [
                        _nav_link("Sensores", "/sensores", "sensors.svg", pathname),
                        _nav_link("Peso", "/peso", "weight.svg", pathname),
                        _nav_link("Nutrición", "/nutricion", "nutrition.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Equipo",
                    [
                        _nav_link("Mi equipo", "/usuarios", "team.svg", pathname),
                        _nav_link("Contacto con coach", "/contacto", "team.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Información",
                    [
                        _nav_link("Sobre PowerSync", "/sobre", "profile.svg", pathname),
                        _nav_link("Invitar", "/invita", "team.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]

        elif role == "coach":
            sections = [
                _nav_section(
                    "Dashboard",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Perfil", "/dashboard", "profile.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Equipo",
                    [
                        _nav_link("Equipo", "/usuarios", "team.svg", pathname),
                        _nav_link("Perfil de atleta", "/deportista", "profile.svg", pathname),
                        _nav_link("Sensores", "/sensores", "sensors.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Rendimiento",
                    [
                        _nav_link("Señales y métricas", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar", "/comparar", "compare.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Bienestar",
                    [
                        _nav_link("Bienestar del equipo", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Tendencias", "/historico", "history.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Comunicación",
                    [
                        _nav_link("Comunicados", "/anuncios", "signals.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Información",
                    [
                        _nav_link("Sobre PowerSync", "/sobre", "profile.svg", pathname),
                        _nav_link("Invitar atletas", "/invita", "team.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]
        else:
            # admin (u otro rol)
            sections = [
                _nav_section(
                    "Admin",
                    [
                        _nav_link("Panel", "/", "session.svg", pathname),
                        _nav_link("Perfil / Ajustes", "/dashboard", "profile.svg", pathname),
                        _nav_link("Usuarios", "/usuarios", "team.svg", pathname),
                    ],
                ),
                _nav_section(
                    "Monitoreo",
                    [
                        _nav_link("Sensores", "/sensores", "sensors.svg", pathname),
                        _nav_link("Señales (ECG)", "/ecg", "signals.svg", pathname),
                        _nav_link("Comparar", "/comparar", "compare.svg", pathname),
                        _nav_link("Bienestar", "/cuestionario", "wellbeing.svg", pathname),
                        _nav_link("Tendencias", "/historico", "history.svg", pathname),
                    ],
                ),
            ]
            bottom = [_nav_link("Salir", "/logout", "profile.svg", pathname)]

    return html.Div(
        [
            meta,
            html.Div(sections, className="nav-scroll"),
            html.Div(bottom, className="nav-bottom"),
        ],
        className="nav-body",
    )


sidebar = html.Div(
    id="sidebar",
    children=[
        html.Div(
            className="sidebar-brand",
            children=[
                html.Div(
                    className="sidebar-brand__mark",
                    children=[
                        html.Img(src="/assets/logo_powersync.svg", className="sidebar-brand__logo"),
                    ],
                ),
                html.Div(
                    className="sidebar-brand__text",
                    children=[
                        html.Span("PowerSync", className="sidebar-brand__name"),
                        html.Span("Desktop Performance Monitor", className="sidebar-brand__tag"),
                    ],
                ),
            ],
        ),
        html.Div(id="sidebar-links"),
    ],
)

content = html.Div(id="page-content", className="page-shell")

# === LAYOUT ===
app.layout = html.Div([
    dcc.Location(id="url"),
    sidebar,
    content,
    dcc.Download(id="dl-png"),
    dcc.Download(id="dl-peaks"),
    dcc.Store(id="dl-png-clicks", data=0),
    dcc.Store(id="ui-sidebar-collapsed", data=False),
    html.Button("«", id="btn-toggle-sidebar", n_clicks=0, className="sidebar-toggle")
])


@app.callback(Output("sidebar-links", "children"), Input("url", "pathname"))
def _render_sidebar(pathname):
    return _sidebar_links(pathname)


# ====== IMPORT páginas externas ======
def _safe_import(modname: str):
    try:
        mod = importlib.import_module(modname)
        return mod, None
    except Exception:
        return None, traceback.format_exc()


page_login, err_login = _safe_import("pages.auth_login")
page_register, err_register = _safe_import("pages.auth_register")
page_dashboard, err_dashboard = _safe_import("pages.dashboard")
page_logout, err_logout = _safe_import("pages.logout")


# =========================
#        VISTAS
# =========================

# ---- USUARIOS ----
def view_usuarios():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    user_id = session.get("user_id")

    sports_base = ["Taekwondo", "Judo", "Kickboxing", "Box", "Muay Thai", "MMA", "Karate", "Sambo"]
    sports_opts = [{"label": s, "value": s} for s in sports_base] + [
        {"label": "Otra (especificar)", "value": "OTRA"}
    ]
    sports_opts_search = [{"label": "Cualquiera", "value": ""}] + sports_opts

    # =========================
    # COACH: roster + equipos
    # =========================
    if role == "coach" and user_id:
        coach_id = int(user_id)

        roster = _coach_roster(coach_id)
        roster_opts = [{"label": f"{a['name']} ({a.get('sport') or '-'})", "value": a["id"]} for a in roster]

        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]

        tbl_search = DataTable(
            id="tbl-search-athletes",
            data=[],
            columns=[
                {"name": "ID", "id": "id"},
                {"name": "Nombre", "id": "name"},
                {"name": "Deporte", "id": "sport"},
                {"name": "Alta", "id": "created_at"},
            ],
            page_size=6,
            row_selectable="single",
            style_table={"overflowX": "auto"},
            style_cell={"background": "#151a21", "color": "#E7ECF3", "border": "1px solid #232a36"},
            sort_action="native",
            filter_action="native",
        )

        tbl_roster = DataTable(
            id="tbl-roster",
            data=roster,
            columns=[
                {"name": "ID", "id": "id"},
                {"name": "Nombre", "id": "name"},
                {"name": "Deporte", "id": "sport"},
                {"name": "Alta", "id": "created_at"},
            ],
            page_size=8,
            row_selectable="single",
            style_table={"overflowX": "auto"},
            style_cell={"background": "#151a21", "color": "#E7ECF3", "border": "1px solid #232a36"},
            sort_action="native",
            filter_action="native",
        )

        tbl_team_members = DataTable(
            id="tbl-team-members",
            data=[],
            columns=[
                {"name": "ID", "id": "athlete_id"},
                {"name": "Nombre", "id": "name"},
                {"name": "Deporte", "id": "sport"},
                {"name": "Rol", "id": "role_label"},
                {"name": "Añadido", "id": "added_at"},
            ],
            page_size=8,
            row_selectable="single",
            style_table={"overflowX": "auto"},
            style_cell={"background": "#151a21", "color": "#E7ECF3", "border": "1px solid #232a36"},
            sort_action="native",
            filter_action="native",
        )

        roster_tab = html.Div([
            h2("Mis deportistas (Roster)"),
            html.Small(
                "Ya no se crean usuarios rápidos. Busca deportistas existentes en la base de datos "
                "y agrégalos a tu roster para gestionarlos.",
                style={"opacity": 0.8}
            ),
            html.Hr(),

            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 220px 1fr 140px", "gap": "8px"},
                children=[
                    dcc.Input(id="coach-search-text", type="text", placeholder="Buscar por nombre..."),
                    dcc.Dropdown(id="coach-search-sport", options=sports_opts_search, value="", placeholder="Deporte"),
                    html.Div(id="coach-sport-custom-box", style={"display": "none"}, children=[
                        dcc.Input(id="coach-search-sport-custom", type="text", placeholder="Especifica deporte exacto")
                    ]),
                    html.Button("Buscar", id="btn-coach-search", n_clicks=0, className="btn btn-primary"),
                ],
            ),
            html.Div(id="coach-search-msg", style={"marginTop": "8px", "opacity": 0.9}),
            html.Div(style={"marginTop": "10px"}, children=[tbl_search]),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Button("Agregar al roster", id="btn-roster-add", n_clicks=0, className="btn btn-success")
            ]),
            html.Hr(style={"marginTop": "18px", "marginBottom": "18px"}),

            html.H4("Mi roster actual"),
            html.Small("Este roster es el que se usa en Perfil deportista, Anuncios, Cuestionario e Histórico.", style={"opacity": 0.8}),
            html.Div(style={"marginTop": "10px"}, children=[tbl_roster]),
            html.Div(style={"marginTop": "10px"}, children=[
                html.Button("Quitar del roster", id="btn-roster-remove", n_clicks=0, className="btn btn-danger")
            ]),
            html.Div(id="coach-roster-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
        ])

        teams_tab = html.Div([
            h2("Equipos"),
            html.Small("Crea equipos y asigna miembros desde tu roster.", className="text-muted"),
            html.Hr(),

            html.H4("Crear equipo"),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 220px 1fr 140px", "gap": "8px"},
                children=[
                    dcc.Input(id="team-name", type="text", placeholder="Nombre del equipo (ej. Elite)"),
                    dcc.Dropdown(id="team-sport", options=sports_opts_search, value="", placeholder="Deporte (opcional)"),
                    html.Div(id="team-sport-custom-box", style={"display": "none"}, children=[
                        dcc.Input(id="team-sport-custom", type="text", placeholder="Especifica deporte exacto")
                    ]),
                    html.Button("Crear", id="btn-team-create", n_clicks=0, className="btn btn-primary"),
                ],
            ),
            html.Div(id="team-create-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),

            html.Hr(style={"marginTop": "18px", "marginBottom": "18px"}),

            html.Div(
                style={"display": "grid", "gridTemplateColumns": "320px 1fr", "gap": "12px", "alignItems": "start"},
                children=[
                    html.Div([
                        html.Label("Equipo"),
                        dcc.Dropdown(id="team-select", options=team_opts, placeholder="Selecciona un equipo"),
                        html.Br(),
                        html.Label("Deportista (del roster)"),
                        dcc.Dropdown(id="team-add-athlete", options=roster_opts, placeholder="Selecciona deportista"),
                        html.Div(style={"display": "flex", "gap": "10px", "marginTop": "10px"}, children=[
                            html.Button("Agregar", id="btn-team-add-member", n_clicks=0, className="btn btn-success"),
                            html.Button("Quitar seleccionado", id="btn-team-remove-member", n_clicks=0, className="btn btn-danger"),
                        ]),
                        html.Div(id="team-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
                        html.Small(
                            "Tip: primero agrega deportistas a tu roster, luego asígnalos a equipos.",
                            style={"opacity": 0.75, "display": "block", "marginTop": "10px"}
                        )
                    ]),
                    html.Div([
                        html.H5("Miembros del equipo"),
                        tbl_team_members
                    ])
                ]
            )
        ])

        return html.Div([
            dcc.Tabs(
                id="tabs-coach-users",
                value="tab-roster",
                children=[
                    dcc.Tab(label="Roster", value="tab-roster", children=[roster_tab]),
                    dcc.Tab(label="Equipos", value="tab-teams", children=[teams_tab]),
                ]
            )
        ])

    # =========================
    # DEPORTISTA: ver su coach
    # =========================
    if role == "deportista" and user_id:
        coach = db.get_user_coach(int(user_id))
        users = []
        if coach:
            users.append({
                "id": coach["id"],
                "name": coach["name"],
                "role": coach["role"],
                "sport": coach.get("sport"),
                "created_at": coach["created_at"],
            })

        table = DataTable(
            id="tbl-users",
            data=users,
            columns=[
                {"name": "ID", "id": "id"},
                {"name": "Nombre", "id": "name"},
                {"name": "Rol", "id": "role"},
                {"name": "Deporte", "id": "sport"},
                {"name": "Alta", "id": "created_at"}
            ],
            page_size=8, style_table={"overflowX": "auto"},
            style_cell={"background": "#151a21", "color": "#E7ECF3", "border": "1px solid #232a36"},
            sort_action="native", filter_action="native"
        )

        return html.Div([
            h2("Equipo / Coach"),
            html.Small(
                "Solo tu coach o el staff pueden gestionar equipos y roster. Aquí solo ves a tu coach/equipo.",
                style={"opacity": 0.8}
            ),
            table,
        ])

    # =========================
    # ADMIN: gestión de usuarios
    # =========================
    users = db.list_users()

    table = DataTable(
        id="tbl-users",
        data=users,
        columns=[
            {"name": "ID", "id": "id"},
            {"name": "Nombre", "id": "name"},
            {"name": "Rol", "id": "role"},
            {"name": "Deporte", "id": "sport"},
            {"name": "Alta", "id": "created_at"}
        ],
        page_size=8, style_table={"overflowX": "auto"},
        style_cell={"background": "#151a21", "color": "#E7ECF3", "border": "1px solid #232a36"},
        sort_action="native", filter_action="native"
    )

    add_controls = html.Div(
        style={
            "display": "grid",
            "gridTemplateColumns": "1fr 220px 1fr 140px",
            "gap": "8px",
            "marginBottom": "10px"
        },
        children=[
            dcc.Input(id="in-name", type="text", placeholder="Nombre completo"),
            dcc.Dropdown(id="in-sport", options=sports_opts, placeholder="deporte"),
            html.Div(id="sport-custom-box", style={"display": "none"}, children=[
                dcc.Input(id="in-sport-custom", type="text",
                          placeholder="Especifica deporte/arte marcial")
            ]),
            html.Button("Añadir", id="btn-add", n_clicks=0, className="btn btn-primary"),
        ]
    )

    delete_controls = html.Div(
        style={
            "display": "grid",
            "gridTemplateColumns": "1fr 140px",
            "gap": "8px",
            "marginBottom": "10px"
        },
        children=[
            dcc.Dropdown(
                id="in-del-user",
                options=[{"label": f"{u['name']} ({u.get('role', '?')})", "value": u["id"]} for u in users],
                placeholder="Selecciona usuario"
            ),
            html.Button("Eliminar", id="btn-del", n_clicks=0, className="btn btn-danger")
        ]
    )

    children = [h2("Gestión de usuarios (Admin)")]

    children.append(html.Small(
        "Como admin puedes dar de alta o eliminar usuarios (legacy). "
        "Los coaches gestionan roster y equipos desde esta sección.",
        style={"opacity": 0.8}
    ))
    children.append(add_controls)
    children.append(delete_controls)

    children.append(table)
    children.append(html.Div(id="users-msg", style={"marginTop": "8px", "color": "#FFB4B4"}))

    return html.Div(children)

@app.callback(Output("sport-custom-box", "style"), Input("in-sport", "value"))
def toggle_custom_sport(selected):
    return {} if selected == "OTRA" else {"display": "none"}


@app.callback(
    Output("tbl-users", "data", allow_duplicate=True),
    Output("in-del-user", "options", allow_duplicate=True),
    Output("users-msg", "children"),
    Input("btn-add", "n_clicks"),
    Input("btn-del", "n_clicks"),
    State("in-name", "value"),
    State("in-sport", "value"),
    State("in-sport-custom", "value"),
    State("in-del-user", "value"),
    prevent_initial_call=True
)
def user_actions(n_add, n_del, name, sport, sport_custom, del_user_id):
    role = _to_str(session.get("role")) or "no autenticado"

    # A partir de ahora, SOLO admin puede crear/eliminar usuarios (evitamos "usuarios rápidos" del coach).
    if role != "admin":
        users = []
        options = []
        return users, options, "No tienes permisos para modificar usuarios."

    trig = [t["prop_id"] for t in callback_context.triggered][0]
    msg = ""

    if "btn-add" in trig:
        if not name:
            msg = "Nombre requerido."
        else:
            if sport == "OTRA":
                if not (sport_custom and sport_custom.strip()):
                    msg = "Especifica el deporte en el campo 'Otro'."
                else:
                    db.add_user(name, sport_custom.strip(), role="deportista", coach_id=None)
                    msg = "Usuario añadido."
            else:
                db.add_user(name, sport, role="deportista", coach_id=None)
                msg = "Usuario añadido."

    elif "btn-del" in trig:
        if not del_user_id:
            msg = "Selecciona usuario a eliminar."
        else:
            db.delete_user(int(del_user_id))
            msg = "Usuario eliminado."

    users = db.list_users()
    options = [{"label": f"{u['name']} ({u.get('role', '?')})", "value": u["id"]} for u in users]
    return users, options, msg

# =========================
# COACH: Roster + Equipos
# =========================

@app.callback(Output("coach-sport-custom-box", "style"), Input("coach-search-sport", "value"))
def toggle_custom_sport_coach(selected):
    return {} if selected == "OTRA" else {"display": "none"}


@app.callback(Output("team-sport-custom-box", "style"), Input("team-sport", "value"))
def toggle_custom_sport_team(selected):
    return {} if selected == "OTRA" else {"display": "none"}


def _fallback_search_athletes(text: str = "", sport: str = None, limit: int = 50):
    """Fallback si tu db.py aún no trae search_athletes()."""
    rows = []
    try:
        rows = db.list_users() or []
    except Exception:
        rows = []
    rows = [u for u in rows if (u.get("role", "deportista") == "deportista")]

    t = (text or "").strip().lower()
    s = (sport or "").strip().lower() if sport else None

    out = []
    for u in rows:
        name = (u.get("name") or "").lower()
        usport = (u.get("sport") or "").lower()
        if t and t not in name:
            continue
        if s and s != usport:
            continue
        out.append(u)
        if len(out) >= int(limit):
            break
    return out


@app.callback(
    Output("tbl-search-athletes", "data"),
    Output("coach-search-msg", "children"),
    Input("btn-coach-search", "n_clicks"),
    State("coach-search-text", "value"),
    State("coach-search-sport", "value"),
    State("coach-search-sport-custom", "value"),
    prevent_initial_call=True
)
def coach_search_athletes(n, text, sport, sport_custom):
    if _to_str(session.get("role")) != "coach":
        return [], "Inicia sesión como coach."
    if not n:
        raise PreventUpdate

    text = (text or "").strip()
    sport = (sport or "").strip()
    if sport == "OTRA":
        sport = (sport_custom or "").strip()

    sport_filter = sport if sport else None

    try:
        if hasattr(db, "search_athletes"):
            results = db.search_athletes(text=text, sport=sport_filter, limit=50) or []
        else:
            results = _fallback_search_athletes(text=text, sport=sport_filter, limit=50)
    except Exception:
        results = _fallback_search_athletes(text=text, sport=sport_filter, limit=50)

    return results, f"{len(results)} deportista(s) encontrado(s)."


def _refresh_roster_and_opts(coach_id: int):
    roster = _coach_roster(int(coach_id))
    roster_opts = [{"label": f"{a['name']} ({a.get('sport') or '-'})", "value": a["id"]} for a in roster]
    return roster, roster_opts


@app.callback(
    Output("tbl-roster", "data", allow_duplicate=True),
    Output("team-add-athlete", "options", allow_duplicate=True),
    Output("coach-roster-msg", "children", allow_duplicate=True),
    Input("btn-roster-add", "n_clicks"),
    State("tbl-search-athletes", "data"),
    State("tbl-search-athletes", "selected_rows"),
    prevent_initial_call=True
)
def coach_add_to_roster(n, rows, selected_rows):
    if _to_str(session.get("role")) != "coach":
        return [], [], "No tienes permisos."
    if not n:
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return [], [], "Sesión inválida. Vuelve a iniciar sesión."

    if not rows or not selected_rows:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return roster, roster_opts, "Selecciona un deportista de la tabla de búsqueda."

    try:
        athlete = rows[selected_rows[0]]
        athlete_id = int(athlete.get("id"))
    except Exception:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return roster, roster_opts, "Selección inválida."

    # Adoptar atleta (preferido) o fallback a legacy coach_id
    try:
        if hasattr(db, "adopt_athlete_set_primary_if_empty"):
            db.adopt_athlete_set_primary_if_empty(coach_id, athlete_id)
        elif hasattr(db, "adopt_athlete"):
            db.adopt_athlete(coach_id, athlete_id)
        elif hasattr(db, "_get_conn"):
            # fallback: asigna coach_id si está vacío
            with db._get_conn() as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE users SET coach_id=COALESCE(coach_id, ?) WHERE id=?",
                    (int(coach_id), int(athlete_id))
                )
                con.commit()
    except Exception:
        pass

    roster, roster_opts = _refresh_roster_and_opts(coach_id)
    return roster, roster_opts, "Deportista agregado a tu roster."


@app.callback(
    Output("tbl-roster", "data", allow_duplicate=True),
    Output("team-add-athlete", "options", allow_duplicate=True),
    Output("coach-roster-msg", "children", allow_duplicate=True),
    Input("btn-roster-remove", "n_clicks"),
    State("tbl-roster", "data"),
    State("tbl-roster", "selected_rows"),
    prevent_initial_call=True
)
def coach_remove_from_roster(n, roster_rows, selected_rows):
    if _to_str(session.get("role")) != "coach":
        return [], [], "No tienes permisos."
    if not n:
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return [], [], "Sesión inválida. Vuelve a iniciar sesión."

    if not roster_rows or not selected_rows:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return roster, roster_opts, "Selecciona un deportista de tu roster."

    try:
        athlete = roster_rows[selected_rows[0]]
        athlete_id = int(athlete.get("id"))
    except Exception:
        roster, roster_opts = _refresh_roster_and_opts(coach_id)
        return roster, roster_opts, "Selección inválida."

    # Quitar adopción (si existe) y también limpiar legacy (si aplica)
    try:
        if hasattr(db, "remove_adopted_athlete"):
            db.remove_adopted_athlete(coach_id, athlete_id)
        if hasattr(db, "_get_conn"):
            with db._get_conn() as con:
                cur = con.cursor()
                cur.execute(
                    "UPDATE users SET coach_id=NULL WHERE id=? AND coach_id=?",
                    (int(athlete_id), int(coach_id))
                )
                con.commit()
    except Exception:
        pass

    roster, roster_opts = _refresh_roster_and_opts(coach_id)
    return roster, roster_opts, "Deportista quitado del roster."


@app.callback(
    Output("team-select", "options"),
    Output("team-select", "value"),
    Output("team-create-msg", "children"),
    Input("btn-team-create", "n_clicks"),
    State("team-name", "value"),
    State("team-sport", "value"),
    State("team-sport-custom", "value"),
    prevent_initial_call=True
)
def coach_create_team(n, name, sport, sport_custom):
    if _to_str(session.get("role")) != "coach":
        return [], None, "No tienes permisos."
    if not n:
        raise PreventUpdate

    try:
        coach_id = int(session.get("user_id"))
    except Exception:
        return [], None, "Sesión inválida. Vuelve a iniciar sesión."

    name = (name or "").strip()
    sport = (sport or "").strip()
    if sport == "OTRA":
        sport = (sport_custom or "").strip()
    sport_val = sport if sport else None

    if not name:
        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
        return team_opts, None, "Nombre de equipo requerido."

    if not hasattr(db, "create_team"):
        teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
        team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
        return team_opts, None, "Tu db.py todavía no soporta equipos (create_team)."

    new_id = None
    try:
        new_id = db.create_team(coach_id, name, sport_val)
    except Exception:
        new_id = None

    teams = db.list_teams(coach_id) if hasattr(db, "list_teams") else []
    team_opts = [{"label": f"{t['name']}{(' — '+t['sport']) if t.get('sport') else ''}", "value": t["id"]} for t in (teams or [])]
    return team_opts, new_id, "Equipo creado." if new_id else "No se pudo crear el equipo."


@app.callback(
    Output("tbl-team-members", "data"),
    Input("team-select", "value"),
)
def coach_load_team_members(team_id):
    if not team_id:
        return []
    if not hasattr(db, "list_team_members"):
        return []
    try:
        return db.list_team_members(int(team_id)) or []
    except Exception:
        return []


@app.callback(
    Output("tbl-team-members", "data", allow_duplicate=True),
    Output("team-msg", "children", allow_duplicate=True),
    Input("btn-team-add-member", "n_clicks"),
    State("team-select", "value"),
    State("team-add-athlete", "value"),
    prevent_initial_call=True
)
def coach_add_team_member(n, team_id, athlete_id):
    if _to_str(session.get("role")) != "coach":
        return [], "No tienes permisos."
    if not n:
        raise PreventUpdate

    if not team_id:
        return [], "Selecciona un equipo."
    if not athlete_id:
        current = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
        return current or [], "Selecciona un deportista."

    if not hasattr(db, "add_team_member"):
        current = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
        return current or [], "Tu db.py todavía no soporta equipos (add_team_member)."

    try:
        db.add_team_member(int(team_id), int(athlete_id), role_label=None)
    except Exception:
        pass

    members = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
    return members or [], "Miembro agregado."


@app.callback(
    Output("tbl-team-members", "data", allow_duplicate=True),
    Output("team-msg", "children", allow_duplicate=True),
    Input("btn-team-remove-member", "n_clicks"),
    State("team-select", "value"),
    State("tbl-team-members", "data"),
    State("tbl-team-members", "selected_rows"),
    prevent_initial_call=True
)
def coach_remove_team_member(n, team_id, member_rows, selected_rows):
    if _to_str(session.get("role")) != "coach":
        return [], "No tienes permisos."
    if not n:
        raise PreventUpdate

    if not team_id:
        return [], "Selecciona un equipo."
    if not member_rows or not selected_rows:
        current = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
        return current or [], "Selecciona un miembro."

    # La fila trae athlete_id (según el schema de db)
    try:
        row = member_rows[selected_rows[0]]
        athlete_id = int(row.get("athlete_id") or row.get("id"))
    except Exception:
        current = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
        return current or [], "Selección inválida."

    if not hasattr(db, "remove_team_member"):
        current = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
        return current or [], "Tu db.py todavía no soporta equipos (remove_team_member)."

    try:
        db.remove_team_member(int(team_id), int(athlete_id))
    except Exception:
        pass

    members = db.list_team_members(int(team_id)) if hasattr(db, "list_team_members") else []
    return members or [], "Miembro quitado."

# ---- PERFIL DE DEPORTISTA (para coach) ----
def view_deportista():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        return html.Div("No tienes permisos para ver esta sección (solo coach).", className="muted")

    coach_id = session.get("user_id")
    athletes = _coach_roster(int(coach_id)) if coach_id else []
    options_users = [
        {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
        for u in athletes
    ]
    default_val = options_users[0]["value"] if options_users else None

    return html.Div([
        h2("Perfil de deportista"),
        html.Small("Selecciona un deportista para ver sus datos, contacto y últimas métricas.",
                   style={"opacity": 0.8}),
        html.Br(),
        html.Label("Deportista"),
        dcc.Dropdown(
            id="athlete-select",
            options=options_users,
            value=default_val,
            placeholder="Selecciona deportista..."
        ),
        html.Br(),
        html.Div(id="athlete-card")
    ])


@app.callback(
    Output("athlete-card", "children"),
    Input("athlete-select", "value"),
    prevent_initial_call=False
)
def render_athlete_card(user_id):
    role = _to_str(session.get("role")) or "no autenticado"
    coach_id = session.get("user_id")

    if role != "coach":
        return html.Div("No tienes permisos para ver esta sección.", className="muted")
    if not user_id:
        return html.Div("Selecciona un deportista en la lista de arriba.")

    athletes = _coach_roster(int(coach_id)) if coach_id else []
    if not any(a["id"] == user_id for a in athletes):
        return html.Div("Este deportista no pertenece a tu equipo.", className="muted")

    u = db.get_user_by_id(int(user_id))
    if not u:
        return html.Div("No se encontró el deportista seleccionado.")

    name = u.get("name", "Sin nombre")
    sport = u.get("sport", "—")
    urole = u.get("role", "deportista")
    created_at = u.get("created_at", "—")

    email = u.get("email") or u.get("correo") or None

    try:
        qrows = db.list_questionnaires(int(user_id))
    except Exception:
        qrows = []
    last_q = qrows[0] if qrows else None
    wellness = float(last_q["wellness_score"]) if last_q and last_q.get("wellness_score") is not None else None
    q_date = last_q["ts"] if last_q else "Sin cuestionarios"

    try:
        last_ecg = db.get_last_ecg_metrics(int(user_id))
    except Exception:
        last_ecg = None

    try:
        sens_codes = db.get_user_sensors(int(user_id))
    except Exception:
        sens_codes = []
    sens_labels = [S.catalog()[c]["name"] for c in sens_codes] if sens_codes else []

    blocks = [
        html.H3(name, className="card-title"),
        html.Div(f"Rol: {urole} · Deporte: {sport}"),
        html.Div(f"Alta en el sistema: {created_at}",
                 style={"opacity": 0.8, "fontSize": "13px", "marginTop": "4px"}),
        html.Hr(),
        html.H4("Contacto"),
        html.Div(f"Email: {email}" if email else "Email: no disponible"),
    ]
    if email:
        blocks.append(
            html.A(
                "Enviar correo",
                href=f"mailto:{email}",
                className="btn btn-primary",
                style={"display": "inline-block", "marginTop": "6px"}
            )
        )

    blocks.append(html.Hr())
    blocks.append(html.H4("Último cuestionario de bienestar"))
    if wellness is not None:
        blocks.append(html.Div(f"Wellness: {wellness:.1f} / 100"))
        blocks.append(html.Div(f"Fecha: {q_date}", style={"opacity": 0.8, "fontSize": "13px"}))
    else:
        blocks.append(html.Div("Sin cuestionarios registrados."))

    blocks.append(html.Hr())
    blocks.append(html.H4("Últimas métricas de ECG"))
    if last_ecg:
        blocks.append(html.Div(f"BPM: {last_ecg.get('bpm', 0):.0f}"))
        blocks.append(html.Div(f"SDNN: {last_ecg.get('sdnn', 0):.0f} ms"))
        blocks.append(html.Div(f"RMSSD: {last_ecg.get('rmssd', 0):.0f} ms"))
    else:
        blocks.append(html.Div("Sin registros ECG guardados."))

    blocks.append(html.Hr())
    blocks.append(html.H4("Sensores asignados"))
    if sens_labels:
        blocks.append(html.Ul([html.Li(lbl) for lbl in sens_labels]))
    else:
        blocks.append(html.Div("No hay sensores asignados."))

    return html.Div(
        style={
            "background": "#151a21",
            "padding": "16px",
            "borderRadius": "12px",
            "border": "1px solid #232a36",
            "maxWidth": "620px"
        },
        children=blocks
    )


# ---- ANUNCIOS AL EQUIPO (para coach) ----
def view_anuncios():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        return html.Div("No tienes permisos para ver esta sección (solo coach).", className="muted")

    coach_id = session.get("user_id")
    athletes = _coach_roster(int(coach_id)) if coach_id else []

    emails = []
    for u in athletes:
        addr = u.get("email") or u.get("correo")
        if addr:
            emails.append(addr)

    emails_str = ", ".join(emails) if emails else "No hay correos disponibles."
    mailto_link = f"mailto:?bcc={','.join(emails)}" if emails else "#"

    return html.Div([
        h2("Anuncios al equipo"),
        html.P(
            "Desde aquí puedes copiar todos los correos de tus deportistas o abrir tu cliente de correo "
            "con un mensaje dirigido a todos (usando BCC)."
        ),
        html.H4("Correos del equipo"),
        html.Div(
            emails_str,
            style={
                "background": "#151a21",
                "padding": "10px",
                "borderRadius": "8px",
                "border": "1px solid #232a36",
                "fontFamily": "monospace",
                "fontSize": "13px"
            }
        ),
        html.Br(),
        html.A(
            "Redactar anuncio al equipo",
            href=mailto_link,
            className="btn btn-primary",
            style={
                "display": "inline-block",
                "pointerEvents": "auto" if emails else "none",
                "opacity": 1.0 if emails else 0.4
            }
        ),
        html.Div(
            "Tip: el anuncio se envía por correo. Más adelante podríamos guardar los avisos en la app "
            "como un tablón de anuncios.",
            className="muted",
            style={"marginTop": "12px", "fontSize": "13px", "opacity": 0.8}
        )
    ], style={"maxWidth": "800px"})


# ---- CONTACTO COACH (para deportista) ----
def view_contacto_coach():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div("Esta sección está pensada para deportistas.", className="muted")

    user_id = session.get("user_id")
    coach = db.get_user_coach(int(user_id)) if user_id else None

    if coach:
        options = [{
            "label": f"{coach['name']} ({coach.get('sport', '-')})",
            "value": coach["id"],
        }]
        default_val = coach["id"]
        helper = "Este es tu coach asignado en PowerSync."
    else:
        coaches = db.list_coaches()
        options = [{
            "label": f"{u['name']} ({u.get('sport', '-')})",
            "value": u["id"],
        } for u in coaches]
        default_val = options[0]["value"] if options else None
        helper = "Todavía no tienes un coach asignado. Selecciona uno de la lista para contactar."

    return html.Div([
        h2("Contactar a mi coach"),
        html.Small(helper, className="text-muted"),
        html.Br(),
        dcc.Dropdown(
            id="coach-select",
            options=options,
            value=default_val,
            placeholder="Selecciona coach..."
        ),
        html.Br(),
        html.Div(id="coach-contact-card")
    ])


@app.callback(
    Output("coach-contact-card", "children"),
    Input("coach-select", "value"),
    prevent_initial_call=False
)
def render_coach_contact_card(coach_id):
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div("No tienes permisos para usar esta sección.", className="muted")

    if not coach_id:
        return html.Div("Selecciona un coach en el desplegable de arriba.")

    user_id = session.get("user_id")
    assigned = db.get_user_coach(int(user_id)) if user_id else None
    if assigned and assigned["id"] == coach_id:
        u = assigned
    else:
        coaches = db.list_coaches()
        u = next((x for x in coaches if x["id"] == coach_id), None)

    if not u:
        return html.Div("No se encontró el coach seleccionado.")

    name = u.get("name", "Coach")
    sport = u.get("sport", "—")
    email = u.get("email") or u.get("correo")

    children = [
        html.H3(name, className="card-title"),
        html.Div(f"Deporte / especialidad: {sport}"),
        html.Br(),
        html.Div(f"Email: {email}" if email else "Email: no disponible"),
    ]
    if email:
        children.append(
            html.A(
                "Escribirle a mi coach",
                href=f"mailto:{email}",
                className="btn btn-primary",
                style={"display": "inline-block", "marginTop": "6px"}
            )
        )

    return html.Div(
        style={
            "background": "#151a21",
            "padding": "16px",
            "borderRadius": "12px",
            "border": "1px solid #232a36",
            "maxWidth": "620px"
        },
        children=children
    )


# === NUEVA VISTA: SESIÓN RÁPIDA (DEPORTISTA) ===
def view_sesion():
    """
    Vista de 'Resumen de hoy' pensada para deportista.
    Muestra un resumen de bienestar + HRV y atajos a Cuestionario, ECG e Histórico.
    """
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or "no autenticado"

    if not uid:
        return html.Div(
            [
                html.H2("Resumen de hoy"),
                html.P("Inicia sesión para ver tu sesión rápida."),
            ],
            className="page-content",
        )

    if role != "deportista":
        return html.Div(
            [
                html.H2("Resumen de hoy"),
                html.P("Esta vista está pensada para deportistas. Usa el menú lateral para navegar."),
            ],
            className="page-content",
        )

    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None

    user = db.get_user_by_id(uid_int) if uid_int else None
    name = user.get("name") if user else "Deportista"

    # Último cuestionario
    last_wellness_text = "Sin registros"
    last_wellness_val = None
    try:
        qs = db.list_questionnaires(uid_int)
        if qs:
            q0 = qs[0]
            last_wellness_val = q0.get("wellness_score", None)
            ts = q0.get("ts") or ""
            ts_pretty = ts.replace("T", " ")[:16] if ts else ""
            if last_wellness_val is not None:
                last_wellness_text = f"{last_wellness_val:.0f} / 100 · {ts_pretty}"
            else:
                last_wellness_text = ts_pretty or "Sin registros"
    except Exception:
        pass

    # Último ECG / HRV
    last_bpm = "Sin registros"
    last_hrv_detail = ""
    try:
        hrv = db.get_last_ecg_metrics(uid_int)
        if hrv:
            bpm = hrv.get("bpm", None)
            sdnn = hrv.get("sdnn", None)
            rmssd = hrv.get("rmssd", None)
            if bpm is not None:
                last_bpm = f"{bpm:.0f} bpm"
            if sdnn is not None or rmssd is not None:
                parts = []
                if sdnn is not None:
                    parts.append(f"SDNN {sdnn:.1f} ms")
                if rmssd is not None:
                    parts.append(f"RMSSD {rmssd:.1f} ms")
                last_hrv_detail = " · ".join(parts)
    except Exception:
        pass

    return html.Div(
        [
            html.H2("Resumen de hoy"),
            html.P(f"Hola, {name}. Aquí tienes un resumen rápido de tu estado actual."),

            html.Div(
                className="kpis",
                children=[
                    html.Div(
                        className="kpi",
                        children=[
                            html.Div("Bienestar (último cuestionario)", className="kpi-label"),
                            html.Div(
                                f"{last_wellness_val:.0f} / 100" if last_wellness_val is not None else "Sin datos",
                                className="kpi-value",
                            ),
                            html.Div(last_wellness_text, className="kpi-sub"),
                            html.Div(className="kpi-ecg-line"),
                        ],
                    ),
                    html.Div(
                        className="kpi",
                        children=[
                            html.Div("Cardio / HRV (último ECG)", className="kpi-label"),
                            html.Div(last_bpm, className="kpi-value"),
                            html.Div(
                                last_hrv_detail or "Sube un ECG en la sección ECG para ver tus métricas.",
                                className="kpi-sub",
                            ),
                            html.Div(className="kpi-ecg-line"),
                        ],
                    ),
                ],
            ),

            html.Hr(),

            html.Div(
                style={"display": "flex", "gap": "16px", "flexWrap": "wrap", "marginTop": "16px"},
                children=[
                    dcc.Link(
                        html.Button("Responder cuestionario", className="btn-primary"),
                        href="/cuestionario",
                    ),
                    dcc.Link(
                        html.Button("Cargar / ver ECG", className="btn-secondary"),
                        href="/ecg",
                    ),
                    dcc.Link(
                        html.Button("Ver histórico de bienestar", className="btn-secondary"),
                        href="/historico",
                    ),
                ],
            ),
        ],
        className="page-content",
    )


# --- Home por tiles (según rol) ---

def home_tiles():
    """
    Home tipo dashboard (más informativo).
    NOTA: Solo UI (sin callbacks nuevos). Usa datos existentes cuando están disponibles.
    """
    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")
    logged = bool(uid)

    # ---- Helpers locales (UI) ----
    def _parse_ts(ts: str):
        if not ts:
            return None
        try:
            # Soporta "YYYY-MM-DDTHH:MM..." o "YYYY-MM-DD HH:MM..."
            ts2 = ts.replace("T", " ")
            return datetime.fromisoformat(ts2[:19])
        except Exception:
            return None

    def _get_user():
        try:
            return db.get_user_by_id(int(uid)) if uid else None
        except Exception:
            return None

    def _get_last_wellness(user_id: int):
        """
        Regresa: (val_float|None, ts_str|None, pretty_str)
        """
        val = None
        ts = None
        pretty = "Sin registros"
        try:
            qs = db.list_questionnaires(int(user_id)) or []
            if qs:
                q0 = qs[0]
                ts = q0.get("ts") or ""
                val = q0.get("wellness_score", None)
                dt = _parse_ts(ts)
                pretty_ts = dt.strftime("%d %b %Y · %H:%M") if dt else (ts.replace("T", " ")[:16] if ts else "")
                if val is not None:
                    pretty = f"{float(val):.0f} / 100 · {pretty_ts}" if pretty_ts else f"{float(val):.0f} / 100"
                else:
                    pretty = pretty_ts or "Sin registros"
        except Exception:
            pass
        return val, ts, pretty

    def _count_checkins_7d(user_id: int):
        """Cuenta cuestionarios de bienestar en los últimos 7 días (best-effort)."""
        cutoff = datetime.utcnow() - timedelta(days=7)
        try:
            rows = db.list_questionnaires(int(user_id)) or []
        except Exception:
            rows = []
        c = 0
        for r in rows:
            dt = _parse_ts(r.get("ts") or "")
            if dt and dt >= cutoff:
                c += 1
        return c


    def _get_wellness_trend_fig(user_id: int, limit: int = 14):
        """
        Mini tendencia de bienestar (últimos N).
        Si no hay data: figura vacía con mensaje.
        """
        try:
            rows = db.list_questionnaires(int(user_id)) or []
        except Exception:
            rows = []

        pts = []
        for r in rows[: max(limit, 1)]:
            v = r.get("wellness_score", None)
            ts = r.get("ts") or ""
            dt = _parse_ts(ts)
            if v is None or dt is None:
                continue
            pts.append((dt, float(v)))

        pts.sort(key=lambda x: x[0])

        fig = go.Figure()
        if not pts:
            fig.update_layout(
                template="plotly_dark",
                height=240,
                margin=dict(l=18, r=12, t=34, b=18),
                title="Tendencia de bienestar (sin datos)",
            )
            fig.update_xaxes(visible=False)
            fig.update_yaxes(visible=False)
            return fig

        x = [p[0].strftime("%m-%d") for p in pts]
        y = [p[1] for p in pts]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Wellness"))
        fig.update_layout(
            template="plotly_dark",
            height=240,
            margin=dict(l=18, r=12, t=34, b=18),
            title="Tendencia de bienestar (últimos 14)",
            showlegend=False,
        )
        fig.update_yaxes(range=[0, 100], title=None)
        fig.update_xaxes(title=None)
        return fig

    def _get_last_ecg(user_id: int):
        """
        Regresa strings listos para UI.
        """
        bpm_txt = "Sin registros"
        hrv_txt = "—"
        try:
            hrv = db.get_last_ecg_metrics(int(user_id))
            if hrv:
                bpm = hrv.get("bpm", None)
                sdnn = hrv.get("sdnn", None)
                rmssd = hrv.get("rmssd", None)
                if bpm is not None:
                    bpm_txt = f"{float(bpm):.0f} bpm"
                parts = []
                if sdnn is not None:
                    parts.append(f"SDNN {float(sdnn):.0f} ms")
                if rmssd is not None:
                    parts.append(f"RMSSD {float(rmssd):.0f} ms")
                hrv_txt = " · ".join(parts) if parts else "—"
        except Exception:
            pass
        return bpm_txt, hrv_txt

    def _team_card(role: str, user):
        if role == "deportista" and uid:
            coach = None
            try:
                coach = db.get_user_coach(int(uid))
            except Exception:
                coach = None

            if coach:
                email = coach.get("email") or coach.get("correo")
                return html.Div([
                    html.H4("Mi equipo", className="card-title"),
                    html.Div([
                        html.Div(coach.get("name", "Coach"), style={"fontWeight": 800, "fontSize": "16px"}),
                        html.Div(coach.get("sport", "—"), style={"opacity": 0.85, "fontSize": "13px"}),
                        html.Div(f"Email: {email}" if email else "Email: no disponible", style={"opacity": 0.85, "fontSize": "13px", "marginTop": "6px"}),
                        html.Div(className="spacer-10"),
                        dcc.Link(html.Button("Contacto", className="btn btn-primary"), href="/contacto"),
                    ])
                ])
            return html.Div([
                html.H4("Mi equipo", className="card-title"),
                html.Div("Aún no tienes coach asignado o no hay datos disponibles.", style={"opacity": 0.85}),
                html.Div(className="spacer-10"),
                dcc.Link(html.Button("Ir a Equipo", className="btn btn-ghost"), href="/usuarios"),
            ])

        if role == "coach" and uid:
            roster = _coach_roster(int(uid)) if uid else []
            teams = []
            if hasattr(db, "list_teams"):
                try:
                    teams = db.list_teams(int(uid)) or []
                except Exception:
                    teams = []
            return html.Div([
                html.H4("Mi equipo", className="card-title"),
                html.Div([
                    html.Div(f"Deportistas en roster: {len(roster)}", style={"fontWeight": 800, "fontSize": "16px"}),
                    html.Div(f"Equipos: {len(teams) if teams else 0}", style={"opacity": 0.85, "fontSize": "13px", "marginTop": "4px"}),
                    html.Div(className="spacer-10"),
                    dcc.Link(html.Button("Gestionar equipo", className="btn btn-primary"), href="/usuarios"),
                ])
            ])

        return html.Div([
            html.H4("Equipo", className="card-title"),
            html.Div("Información de equipo no disponible para este rol.", style={"opacity": 0.85}),
        ])

    def _recommended_today(sport: str):
        base = [
            "Calentamiento 8–10 min (movilidad + activación).",
            "Trabajo técnico 20–30 min (calidad > cantidad).",
            "Bloque principal con RPE moderado y descansos completos.",
            "Vuelta a la calma 5 min + estiramientos suaves.",
        ]
        by_sport = {
            "Taekwondo": [
                "Técnica: combinaciones 3–4 golpes, enfoque en distancia.",
                "Pierna: 3×(8–10) patadas controladas por lado.",
                "Condición: 6×30s alta intensidad / 60s suave.",
            ],
            "Box": [
                "Sombra 3×3 min (pies + guardia).",
                "Saco: 4×2 min (jab–cross–hook).",
                "Core: 3×(30–45s) planchas + rotaciones.",
            ],
            "Judo": [
                "Movilidad de cadera/hombro 8 min.",
                "Uchi-komi técnico 4×(2–3 min).",
                "Agarres: 6×20s intenso / 40s descanso.",
            ],
            "Kickboxing": [
                "Técnica: combinaciones puño–pierna 4×3 min.",
                "Saco: potencia controlada 6×30s / 60s descanso.",
                "Respiración 3 min para bajar pulsaciones.",
            ],
        }
        if sport and sport in by_sport:
            return by_sport[sport] + ["—"] + base
        return base

    def tile(title, subtitle, href, icon):
        return dcc.Link(
            html.Div([
                html.Img(src=f"/assets/icons/{icon}", className="tile-icon"),
                html.Div([
                    html.Div(title, className="tile-title"),
                    html.Small(subtitle, style={"display": "block", "marginTop": "2px", "color": "var(--muted)"}),
                ])
            ], className="tile-card", style={"alignItems": "flex-start"}),
            href=href,
            className="tile-link"
        )

    # ---- Si no está loggeado ----
    if not logged:
        return html.Div([
            html.H1("Panel", className="page-title"),
            html.Div(className="card", children=[
                html.H3("Bienvenido a PowerSync", className="card-title"),
                html.P("Inicia sesión para ver tu panel y tus métricas."),
                html.Div(className="row-wrap-10", children=[
                    dcc.Link(html.Button("Iniciar sesión", className="btn btn-primary"), href="/login"),
                    dcc.Link(html.Button("Crear cuenta", className="btn btn-ghost"), href="/registro"),
                ])
            ])
        ])

    user = _get_user()
    name = (user or {}).get("name") or "Deportista"
    sport = (user or {}).get("sport") or ""
    role_label = "Deportista" if role == "deportista" else ("Coach" if role == "coach" else "Admin")

    # ---- Datos rápidos (si existen) ----
    last_wellness_val, last_wellness_ts, last_wellness_pretty = _get_last_wellness(int(uid))

    checkins_7d = 0
    try:
        checkins_7d = _count_checkins_7d(int(uid)) if uid else 0
    except Exception:
        checkins_7d = 0

    bpm_txt, hrv_txt = _get_last_ecg(int(uid)) if uid and role == "deportista" else ("—", "—")

    # KPI cards
    kpis_children = [
        html.Div(className="kpi", children=[
            html.Div("Bienestar (último)", className="kpi-label"),
            html.Div(f"{float(last_wellness_val):.0f}" if last_wellness_val is not None else "—", className="kpi-value"),
            html.Div(last_wellness_pretty, className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ]

    if role == "deportista":
        kpis_children += [
            html.Div(className="kpi", children=[
                html.Div("Cardio (último ECG)", className="kpi-label"),
                html.Div(bpm_txt, className="kpi-value"),
                html.Div(hrv_txt, className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
        ]

    # Conteos simples
    qs_count = 0
    try:
        qs_count = len(db.list_questionnaires(int(uid)) or [])
    except Exception:
        qs_count = 0

    kpis_children += [
        html.Div(className="kpi", children=[
            html.Div("Check-ins registrados", className="kpi-label"),
            html.Div(str(qs_count), className="kpi-value"),
            html.Div("Historial de bienestar disponible", className="kpi-sub"),
            html.Div(className="kpi-ecg-line"),
        ]),
    ]

    # Recomendado hoy (lista)
    recs = _recommended_today(sport)
    rec_list = []
    for r in recs:
        if r == "—":
            rec_list.append(html.Hr(style={"opacity": 0.35}))
        else:
            rec_list.append(html.Li(r))

    # Tiles (accesos)
    if role == "coach":
        tiles = [
            tile("Equipo", "Roster y equipos", "/usuarios", "team.svg"),
            tile("Perfil de atleta", "Datos y últimas métricas", "/deportista", "profile.svg"),
            tile("Bienestar del equipo", "Cuestionarios", "/cuestionario", "wellbeing.svg"),
            tile("Tendencias del equipo", "Históricos", "/historico", "history.svg"),
            tile("Sensores", "Asignación y estados", "/sensores", "sensors.svg"),
            tile("Comparar", "Sesión vs sesión", "/comparar", "compare.svg"),
            tile("Comunicados", "Email al equipo", "/anuncios", "signals.svg"),
            tile("Perfil", "Cuenta y ajustes", "/dashboard", "profile.svg"),
        ]
    else:
        tiles = [
            tile("Resumen de hoy", "Vista rápida de estado", "/sesion", "session.svg"),
            tile("Señales y métricas", "ECG, IMU, EMG, RESP", "/ecg", "signals.svg"),
            tile("Bienestar", "Check-in diario", "/cuestionario", "wellbeing.svg"),
            tile("Tendencias", "Histórico y evolución", "/historico", "history.svg"),
            tile("Comparar", "Sesión vs sesión", "/comparar", "compare.svg"),
            tile("Sensores", "Asignación / calibración", "/sensores", "sensors.svg"),
            tile("Peso", "Seguimiento", "/peso", "weight.svg"),
            tile("Nutrición", "Adherencia", "/nutricion", "nutrition.svg"),
            tile("Mi equipo", "Coach y contacto", "/usuarios", "team.svg"),
            tile("Perfil", "QR y datos", "/dashboard", "profile.svg"),
        ]

    # Layout home dashboard
    left_col = html.Div(className="panel-col panel-col--left", children=[
        html.Div(className="card", children=[
            html.H3(f"Hola, {name}", className="card-title"),
            html.Div(f"{role_label}{(' · ' + sport) if sport else ''}", className="text-muted"),
            html.Div(className="spacer-10"),
            html.Div(className="ecg-divider"),
            html.Div(className="spacer-10"),
            html.Div(className="kpis kpis--auto", children=kpis_children),
        ]),
        html.Div(className="card", children=[
            html.H4("Actividad reciente", className="card-title"),
            html.Ul([
                html.Li(f"Último bienestar: {last_wellness_pretty}"),
                html.Li(f"Último ECG: {bpm_txt} · {hrv_txt}" if role == "deportista" else "Último ECG: —"),
                html.Li("Tip: mantén consistencia en check-ins para ver tendencias reales."),
            ], className="list-compact"),
        ]),
        
html.Div(className="card", children=[
    html.H4("Resumen semanal", className="card-title"),
    html.Small("Vista rápida de tu actividad antes de entrar al análisis.", className="text-muted"),
    html.Div(className="kpis kpis--3 kpis--tight", children=[
html.Div(className="kpi kpi--mini", children=[
                html.Div("Check-ins (7 días)", className="kpi-label"),
                html.Div(str(checkins_7d), className="kpi-value"),
                html.Div("cuestionarios", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi kpi--mini", children=[
                html.Div("Último bienestar", className="kpi-label"),
                html.Div(
                    f"{float(last_wellness_val):.0f} / 100" if last_wellness_val is not None else "—",
                    className="kpi-value"
                ),
                html.Div(last_wellness_pretty, className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
            html.Div(className="kpi kpi--mini", children=[
                html.Div("Último ECG", className="kpi-label"),
                html.Div(bpm_txt if role == "deportista" else "—", className="kpi-value"),
                html.Div(hrv_txt if role == "deportista" else "—", className="kpi-sub"),
                html.Div(className="kpi-ecg-line"),
            ]),
]),
]),
html.Div(className="card", children=[
            html.H4("Tendencia de bienestar", className="card-title"),
            dcc.Graph(
                figure=_get_wellness_trend_fig(int(uid)),
                config={"displayModeBar": False, "responsive": True},
                className="panel-graph",
                style={"height": "260px", "width": "100%"}
            ),
        ]),
        html.Div(className="card", children=[
            html.H4("Accesos rápidos", className="card-title"),
            html.Div(tiles, className="grid"),
        ]),
    ])

    right_col = html.Div(className="panel-col panel-col--right", children=[
        html.Div(className="card", children=_team_card(role, user)),
        html.Div(className="card", children=[
            html.H4("Recomendado hoy", className="card-title"),
            html.Ul(rec_list, className="list-compact"),
            html.Small("Recomendaciones generales. Más adelante podemos personalizarlas por carga y sesiones.", className="text-muted"),
        ]),
        html.Div(className="card", children=[
            html.H4("Siguiente paso", className="card-title"),
            html.Div("Completa tu check-in y revisa tus señales para ajustar la carga.", className="text-muted"),
            html.Div(className="spacer-10"),
            html.Div(className="stack-8", children=[
                dcc.Link(html.Button("Hacer check-in", className="btn btn-primary"), href="/cuestionario"),
                dcc.Link(html.Button("Ver señales", className="btn btn-ghost"), href="/ecg"),
            ])
        ]),
    ])

    return html.Div([
        html.H1("Panel", className="page-title"),
        html.Div(className="panel-grid", children=[left_col, right_col]),
    ])


# =======================
# Plan de peso (deportista)
# =======================
def view_peso():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div(
            "Esta sección está pensada para deportistas. Más adelante añadiremos vista para coach.",
            className="muted"
        )

    today = datetime.utcnow().date().isoformat()

    return html.Div(
        style={"maxWidth": "900px"},
        children=[
            h2("Plan de peso"),
            html.Small(
                "Registra tu peso y tu objetivo para ver cómo evolucionas en el tiempo.",
                style={"opacity": 0.8}
            ),
            dcc.Store(id="peso-store", data={"rev": 0}),
            html.Br(),

            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "150px 150px 1fr",
                    "gap": "12px",
                    "alignItems": "center",
                },
                children=[
                    html.Div(children=[
                        html.Label("Fecha"),
                        dcc.DatePickerSingle(
                            id="peso-date",
                            date=today,
                            display_format="YYYY-MM-DD"
                        ),
                    ]),
                    html.Div(children=[
                        html.Label("Peso actual (kg)"),
                        dcc.Input(
                            id="peso-actual",
                            type="number",
                            min=0,
                            step=0.1,
                            placeholder="Ej: 68.5",
                            style={"width": "100%"},
                        ),
                    ]),
                    html.Div(children=[
                        html.Label("Objetivo (kg, opcional)"),
                        dcc.Input(
                            id="peso-objetivo",
                            type="number",
                            min=0,
                            step=0.1,
                            placeholder="Ej: 66.0",
                            style={"width": "100%"},
                        ),
                    ]),
                ],
            ),

            html.Br(),
            html.Label("Nota (opcional)"),
            dcc.Input(
                id="peso-nota",
                type="text",
                placeholder="Ej: Semana de descarga / competición cercana",
                style={"width": "100%"},
            ),
            html.Br(), html.Br(),
            html.Button("Guardar registro", id="btn-save-peso", className="btn btn-primary"),
            html.Div(id="peso-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),

            html.Hr(),

            html.H4("Progreso de peso"),
            dcc.Graph(id="peso-graph", figure=go.Figure(), style={"height": "420px"}),

            html.Br(),
            html.H4("Registros guardados"),
            DataTable(
                id="peso-table",
                columns=[
                    {"name": "Fecha", "id": "date"},
                    {"name": "Peso (kg)", "id": "weight"},
                    {"name": "Objetivo (kg)", "id": "target"},
                    {"name": "Nota", "id": "note"},
                ],
                data=[],
                page_size=10,
                style_table={"overflowX": "auto"},
                style_cell={
                    "background": "#151a21",
                    "color": "#E7ECF3",
                    "border": "1px solid #232a36",
                },
            ),
        ],
    )


@app.callback(
    Output("peso-store", "data"),
    Output("peso-msg", "children"),
    Input("btn-save-peso", "n_clicks"),
    State("peso-store", "data"),
    State("peso-date", "date"),
    State("peso-actual", "value"),
    State("peso-objetivo", "value"),
    State("peso-nota", "value"),
    prevent_initial_call=True,
)
def save_peso(n, data, date, weight, target, note):
    if not n:
        raise PreventUpdate

    # Persistencia en DB (sin tocar otras funciones)
    if not session.get("user_id"):
        return (data or {"rev": 0}), "Inicia sesión para guardar tu peso."

    try:
        uid = int(session.get("user_id"))
    except Exception:
        return (data or {"rev": 0}), "Sesión inválida. Vuelve a iniciar sesión."

    if weight is None:
        return (data or {"rev": 0}), "Introduce tu peso actual en kg."

    date_str = date or datetime.utcnow().date().isoformat()
    try:
        w = float(weight)
    except Exception:
        return (data or {"rev": 0}), "Peso actual no válido."

    try:
        t = float(target) if target is not None else None
    except Exception:
        t = None

    try:
        if hasattr(db, "add_weight_entry"):
            db.add_weight_entry(uid, date_str, w, t, (note or "").strip())
        else:
            return (data or {"rev": 0}), "Tu db.py aún no soporta peso persistente (add_weight_entry)."
    except Exception:
        return (data or {"rev": 0}), "No se pudo guardar el registro de peso."

    cur = data or {"rev": 0}
    try:
        rev = int(cur.get("rev", 0)) + 1
    except Exception:
        rev = 1
    return {"rev": rev}, "Registro guardado."



@app.callback(
    Output("peso-graph", "figure"),
    Output("peso-table", "data"),
    Input("peso-store", "data"),
    prevent_initial_call=False,
)
def update_peso_view(_store):
    # Carga desde DB (persistente)
    if not session.get("user_id"):
        fig = go.Figure()
        fig.update_layout(
            height=420,
            margin=dict(l=40, r=18, t=52, b=40),
            title=dict(text="Inicia sesión para ver tus registros de peso", x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                      color="#f2f5fa", size=13),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            transition=dict(duration=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        return fig, []

    try:
        uid = int(session.get("user_id"))
    except Exception:
        uid = None

    rows = []
    try:
        if uid and hasattr(db, "list_weight_entries"):
            rows = db.list_weight_entries(uid, limit=200) or []
    except Exception:
        rows = []

    # Tabla: devolvemos lo que venga (usualmente más reciente primero)
    table_data = [
        {"date": r.get("date"), "weight": r.get("weight"), "target": r.get("target"), "note": r.get("note")}
        for r in (rows or [])
    ]

    if not rows:
        fig = go.Figure()
        fig.update_layout(
            height=420,
            margin=dict(l=40, r=18, t=52, b=40),
            title=dict(text="Aún no hay registros de peso", x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                      color="#f2f5fa", size=13),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            transition=dict(duration=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        return fig, table_data

    rows_sorted = sorted(rows, key=lambda x: (x.get("date") or "", x.get("id") or 0))
    dates = [d.get("date") for d in rows_sorted]
    weights = [d.get("weight") for d in rows_sorted]
    targets = [d.get("target") for d in rows_sorted]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates, y=weights, mode="lines+markers", name="Peso (kg)",
            line=dict(width=3, color="#4f9fd9"),
            marker=dict(size=7),
        )
    )

    if any(t is not None for t in targets):
        fig.add_trace(
            go.Scatter(
                x=dates, y=targets, mode="lines+markers", name="Objetivo (kg)",
                line=dict(width=2, dash="dash", color="#a7b1bc"),
                marker=dict(size=6),
            )
        )

    fig.update_layout(
        height=420,
        margin=dict(l=40, r=18, t=52, b=40),
        title=dict(text="Evolución del peso", x=0.02, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                  color="#f2f5fa", size=13),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        transition=dict(duration=0),
    )
    fig.update_xaxes(title_text="Fecha", showgrid=True, gridcolor="rgba(49,68,95,0.35)",
                     linecolor="rgba(49,68,95,0.7)", ticks="outside",
                     tickcolor="rgba(49,68,95,0.7)", zeroline=False)
    fig.update_yaxes(title_text="Peso (kg)", showgrid=True, gridcolor="rgba(49,68,95,0.35)",
                     linecolor="rgba(49,68,95,0.7)", ticks="outside",
                     tickcolor="rgba(49,68,95,0.7)", zeroline=False)

    return fig, table_data



# =======================
# Nutrición (deportista)
# =======================
def view_nutricion():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    if role != "deportista":
        return html.Div(
            "Esta sección está pensada para deportistas. Más adelante añadiremos vista para coach.",
            className="muted"
        )

    today = datetime.utcnow().date().isoformat()

    return html.Div(
        style={"maxWidth": "900px"},
        children=[
            h2("Nutrición"),
            html.Small(
                "Registra qué tan bien cumpliste tu plan de alimentación y, si quieres, tus kcal diarias.",
                style={"opacity": 0.8},
            ),
            dcc.Store(id="nutri-store", data={"rev": 0}),
            html.Br(),

            html.Div(
                style={
                    "display": "grid",
                    "gridTemplateColumns": "150px 1fr 1fr",
                    "gap": "12px",
                    "alignItems": "center",
                },
                children=[
                    html.Div(children=[
                        html.Label("Fecha"),
                        dcc.DatePickerSingle(
                            id="nutri-date",
                            date=today,
                            display_format="YYYY-MM-DD",
                        ),
                    ]),
                    html.Div(children=[
                        html.Label("Adherencia al plan (%)"),
                        dcc.Slider(
                            id="nutri-adherencia",
                            min=0,
                            max=100,
                            step=5,
                            value=80,
                            tooltip={"placement": "bottom"},
                        ),
                    ]),
                    html.Div(children=[
                        html.Label("Kcal totales (opcional)"),
                        dcc.Input(
                            id="nutri-kcal",
                            type="number",
                            min=0,
                            step=10,
                            placeholder="Ej: 2200",
                            style={"width": "100%"},
                        ),
                    ]),
                ],
            ),

            html.Br(),
            html.Label("Comentario (opcional)"),
            dcc.Input(
                id="nutri-nota",
                type="text",
                placeholder="Ej: Día de descanso, mucha hambre por la noche, etc.",
                style={"width": "100%"},
            ),
            html.Br(), html.Br(),
            html.Button("Guardar registro", id="btn-save-nutri", className="btn btn-primary"),
            html.Div(id="nutri-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),

            html.Hr(),
            html.H4("Adherencia en el tiempo"),
            dcc.Graph(id="nutri-graph", figure=go.Figure(), style={"height": "420px"}),

            html.Br(),
            html.H4("Registros guardados"),
            DataTable(
                id="nutri-table",
                columns=[
                    {"name": "Fecha", "id": "date"},
                    {"name": "Adherencia (%)", "id": "adherence"},
                    {"name": "Kcal", "id": "kcal"},
                    {"name": "Comentario", "id": "note"},
                ],
                data=[],
                page_size=10,
                style_table={"overflowX": "auto"},
                style_cell={
                    "background": "#151a21",
                    "color": "#E7ECF3",
                    "border": "1px solid #232a36",
                },
            ),
        ],
    )


@app.callback(
    Output("nutri-store", "data"),
    Output("nutri-msg", "children"),
    Input("btn-save-nutri", "n_clicks"),
    State("nutri-store", "data"),
    State("nutri-date", "date"),
    State("nutri-adherencia", "value"),
    State("nutri-kcal", "value"),
    State("nutri-nota", "value"),
    prevent_initial_call=True,
)
def save_nutricion(n, data, date, adherence, kcal, note):
    if not n:
        raise PreventUpdate

    # Persistencia en DB (sin tocar otras funciones)
    if not session.get("user_id"):
        return (data or {"rev": 0}), "Inicia sesión para guardar tu nutrición."

    try:
        uid = int(session.get("user_id"))
    except Exception:
        return (data or {"rev": 0}), "Sesión inválida. Vuelve a iniciar sesión."

    if adherence is None:
        return (data or {"rev": 0}), "Indica al menos tu % de adherencia."

    date_str = date or datetime.utcnow().date().isoformat()
    try:
        adh = float(adherence)
    except Exception:
        return (data or {"rev": 0}), "Adherencia no válida."

    try:
        kc = float(kcal) if kcal is not None else None
    except Exception:
        kc = None

    try:
        if hasattr(db, "add_nutrition_entry"):
            db.add_nutrition_entry(uid, date_str, adh, kc, (note or "").strip())
        else:
            return (data or {"rev": 0}), "Tu db.py aún no soporta nutrición persistente (add_nutrition_entry)."
    except Exception:
        return (data or {"rev": 0}), "No se pudo guardar el registro de nutrición."

    cur = data or {"rev": 0}
    try:
        rev = int(cur.get("rev", 0)) + 1
    except Exception:
        rev = 1
    return {"rev": rev}, "Registro de nutrición guardado."



@app.callback(
    Output("nutri-graph", "figure"),
    Output("nutri-table", "data"),
    Input("nutri-store", "data"),
    prevent_initial_call=False,
)
def update_nutri_view(_store):
    # Carga desde DB (persistente)
    if not session.get("user_id"):
        fig = go.Figure()
        fig.update_layout(
            height=420,
            margin=dict(l=40, r=18, t=52, b=40),
            title=dict(text="Inicia sesión para ver tus registros de nutrición", x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                      color="#f2f5fa", size=13),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            transition=dict(duration=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        return fig, []

    try:
        uid = int(session.get("user_id"))
    except Exception:
        uid = None

    rows = []
    try:
        if uid and hasattr(db, "list_nutrition_entries"):
            rows = db.list_nutrition_entries(uid, limit=200) or []
    except Exception:
        rows = []

    table_data = [
        {"date": r.get("date"), "adherence": r.get("adherence"), "kcal": r.get("kcal"), "note": r.get("note")}
        for r in (rows or [])
    ]

    if not rows:
        fig = go.Figure()
        fig.update_layout(
            height=420,
            margin=dict(l=40, r=18, t=52, b=40),
            title=dict(text="Aún no hay registros de nutrición", x=0.02, xanchor="left"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                      color="#f2f5fa", size=13),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            transition=dict(duration=0),
        )
        fig.update_xaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        fig.update_yaxes(showgrid=True, gridcolor="rgba(49,68,95,0.35)", linecolor="rgba(49,68,95,0.7)",
                         ticks="outside", tickcolor="rgba(49,68,95,0.7)", zeroline=False)
        return fig, table_data

    rows_sorted = sorted(rows, key=lambda x: (x.get("date") or "", x.get("id") or 0))
    dates = [d.get("date") for d in rows_sorted]
    adherence = [d.get("adherence") for d in rows_sorted]
    kcal = [d.get("kcal") for d in rows_sorted]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=dates, y=adherence, name="Adherencia (%)", marker=dict(color="#249aa5")))

    if any(k is not None for k in kcal):
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=kcal,
                name="Kcal",
                mode="lines+markers",
                yaxis="y2",
                line=dict(width=2.5, color="#4f9fd9"),
                marker=dict(size=6),
            )
        )
        fig.update_layout(
            yaxis=dict(title="Adherencia (%)"),
            yaxis2=dict(
                title=dict(text="Kcal", font=dict(color="#a7b1bc")),
                overlaying="y",
                side="right",
                showgrid=False,
                zeroline=False,
                tickfont=dict(color="#a7b1bc"),
            ),
        )

    fig.update_layout(
        height=420,
        margin=dict(l=40, r=18, t=52, b=40),
        title=dict(text="Nutrición y adherencia", x=0.02, xanchor="left"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif",
                  color="#f2f5fa", size=13),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        transition=dict(duration=0),
        bargap=0.18,
    )
    fig.update_xaxes(title_text="Fecha", showgrid=True, gridcolor="rgba(49,68,95,0.35)",
                     linecolor="rgba(49,68,95,0.7)", ticks="outside",
                     tickcolor="rgba(49,68,95,0.7)", zeroline=False)
    fig.update_yaxes(title_text="Adherencia (%)", showgrid=True, gridcolor="rgba(49,68,95,0.35)",
                     linecolor="rgba(49,68,95,0.7)", ticks="outside",
                     tickcolor="rgba(49,68,95,0.7)", zeroline=False)

    return fig, table_data



# =======================
# Sobre PowerSync
# =======================
def view_sobre():
    return html.Div([
        h2("Sobre PowerSync"),
        html.P(
            "PowerSync es una plataforma para monitorizar deportes de contacto "
            "y el bienestar del deportista."
        ),
        html.P(
            "Aquí puedes centralizar tus sesiones, cuestionarios de bienestar y datos de sensores "
            "para tomar mejores decisiones de entrenamiento."
        ),
        html.Ul([
            html.Li("Monitorización de sesiones (ECG, carga, bienestar)."),
            html.Li("Integración con sensores y cuestionarios de bienestar."),
            html.Li("Histórico y comparativas para ver tendencias en el tiempo."),
        ], className="list-compact"),
    ], style={"maxWidth": "800px"})


# =======================
# Invita a tus amigos / deportistas
# =======================
def view_invita():
    user_name = _to_str(session.get("name")) or "tu amigo"
    user_id = session.get("user_id")
    if isinstance(user_id, int):
        ref_code = f"ATH-{user_id:04d}"
    else:
        ref_code = "POWERSYNC"

    invite_path = f"/registro?ref={ref_code}"

    return html.Div([
        h2("Invita a tus amigos"),
        html.P(
            "Comparte PowerSync con otros deportistas de tu equipo para que también puedan "
            "monitorizar sus sesiones."
        ),
        html.P([
            "Enlace de registro que puedes compartir: ",
            html.Code(
                invite_path,
                style={
                    "padding": "4px 8px",
                    "borderRadius": "6px",
                    "background": "#151a21",
                },
            ),
        ], style={"marginTop": "8px"}),
        html.P([
            "Tu código de referencia: ",
            html.Strong(ref_code),
        ], style={"marginTop": "12px"}),
        html.Div(
            "Más adelante aquí podemos añadir botones de compartir por WhatsApp, email, etc.",
            className="muted",
            style={"marginTop": "12px", "fontSize": "13px", "opacity": 0.8},
        ),
    ], style={"maxWidth": "800px"})


# === Sidebar toggle (callback normal en Python) ===
@app.callback(
    Output("ui-sidebar-collapsed", "data"),
    Output("sidebar", "style"),
    Output("page-content", "style"),
    Output("btn-toggle-sidebar", "style"),
    Output("btn-toggle-sidebar", "children"),
    Input("btn-toggle-sidebar", "n_clicks"),
    State("ui-sidebar-collapsed", "data"),
)
def toggle_sidebar(n, collapsed):
    collapsed = bool(collapsed)
    clicked = (n or 0) > 0
    NEW = (not collapsed) if clicked else collapsed

    # Solo movemos posiciones (el look lo controla CSS)
    sb = {"left": "0px"}
    pg = {"marginLeft": f"{SIDEBAR_W}px"}
    btn = {"left": f"{SIDEBAR_W + 12}px"}
    txt = "«"

    if NEW:
        sb["left"] = f"-{SIDEBAR_W}px"
        pg["marginLeft"] = f"{PAGE_COLLAPSED_MARGIN}px"
        btn["left"] = "16px"
        txt = "»"

    return NEW, sb, pg, btn, txt


# ====== ROUTER ======
@app.callback(Output("page-content", "children"), Input("url", "pathname"))
def router(path):
    def errbox(title, err):
        return html.Div([
            h2(title),
            html.Pre(err, style={
                "whiteSpace": "pre-wrap", "background": "#2b1f23",
                "border": "1px solid #4a2b31", "padding": "12px",
                "borderRadius": "10px", "color": "#FFB4B4", "overflow": "auto"
            })
        ])

    if path in ("/", "/inicio", "/home"):
        return home_tiles()
    if path in ("/usuarios", "/legacy"):
        return view_usuarios()
    if path == "/deportista":
        return view_deportista()
    if path == "/anuncios":
        return view_anuncios()
    if path == "/contacto":
        return view_contacto_coach()
    if path == "/sensores":
        return sensors_view.layout()
    if path == "/ecg":
        return signals_view.layout()
    if path == "/cuestionario":
        return wellbeing_page.layout_questionnaire()
    if path == "/historico":
        return wellbeing_page.layout_history()
    if path == "/sesion":
        return view_sesion()
    if path == "/comparar":
        return compare_view.layout()
    if path == "/peso":
        return view_peso()
    if path == "/nutricion":
        return view_nutricion()
    if path == "/sobre":
        return view_sobre()
    if path == "/invita":
        return view_invita()

    mod, err = None, None
    if path == "/login":
        mod, err = page_login, err_login
    if path == "/registro":
        mod, err = page_register, err_register
    if path == "/dashboard":
        mod, err = page_dashboard, err_dashboard
    if path == "/logout":
        mod, err = page_logout, err_logout

    if err:
        return errbox(f"Error importando {path}", err)
    if not mod:
        return html.Div("Vista no disponible.")
    return mod.layout() if callable(getattr(mod, "layout", None)) else mod.layout


# === Auto-open helper ===
AUTO_OPEN = os.environ.get("POWERSYNC_AUTO_OPEN", "1") == "1"
_OPEN_SENTINEL = os.path.join(os.path.expanduser("~"), ".powersync_opened")


def _open_browser_once(url):
    try:
        if not os.path.exists(_OPEN_SENTINEL):
            webbrowser.open_new(url)
            open(_OPEN_SENTINEL, "w").close()
    except Exception:
        pass


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8050))
    URL = f"http://127.0.0.1:{PORT}/"
    if AUTO_OPEN and os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        Timer(1.0, lambda: _open_browser_once(URL)).start()
    app.run(debug=True, port=PORT)


# =======================
# Quick actions bar
# =======================
class QuickBar:
    @staticmethod
    def _chip(label: str, href: str, icon: str, active: bool = False):
        base = {
            "display": "inline-flex", "alignItems": "center", "gap": "8px",
            "padding": "10px 14px", "borderRadius": "12px",
            "background": "#121722", "border": "1px solid #1f2630",
            "boxShadow": "0 2px 10px rgba(0,0,0,.25)", "fontWeight": 600
        }
        if active:
            base["border"] = "1px solid #34D7E0"
        return dcc.Link(
            html.Span([
                html.Img(src=f"/assets/icons/{icon}", style={"height": "18px", "opacity": 0.9}),
                html.Span(label)
            ]),
            href=href,
            style=base,
            className="quick-chip"
        )

    @staticmethod
    def layout(active_path: str = ""):
        row_style = {"display": "flex", "flexWrap": "wrap",
                     "gap": "10px", "margin": "0 0 16px 0"}
        chips = [
            QuickBar._chip("Señales", "/ecg", "signals.svg", active=active_path == "/ecg"),
            QuickBar._chip("Bienestar", "/cuestionario", "wellbeing.svg",
                           active=active_path == "/cuestionario"),
            QuickBar._chip("Tendencias", "/historico", "history.svg",
                           active=active_path == "/historico"),
            QuickBar._chip("Sensores", "/sensores", "sensors.svg",
                           active=active_path == "/sensores"),
            QuickBar._chip("Comparar", "/comparar", "compare.svg",
                           active=active_path == "/comparar"),
            QuickBar._chip("Peso", "/peso", "weight.svg", active=active_path == "/peso"),
            QuickBar._chip("Nutrición", "/nutricion", "nutrition.svg",
                           active=active_path == "/nutricion"),
            QuickBar._chip("Equipo", "/usuarios", "team.svg",
                           active=active_path == "/usuarios"),
        ]
        return html.Div(chips, style=row_style)