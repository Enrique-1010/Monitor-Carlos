import json
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, graph_config

from dash import html, dcc, Input, Output, State, callback
from dash.exceptions import PreventUpdate

from flask import session

import db
import questionnaires as Q


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v



def _safe_int(v):
    try:
        return int(v)
    except Exception:
        return None

def h2(txt):
    return html.H2(txt, style={"margin": "6px 0 12px"})


def _coach_roster(coach_id: int):
    """Roster unificado del coach.

    Intenta (en orden):
    - list_roster_for_coach
    - list_my_athletes (adopción)
    - list_athletes_for_coach (legacy coach_id)
    """
    if not coach_id:
        return []

    out = []
    seen = set()

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


def _team_member_ids(team_id: int):
    if not team_id:
        return set()
    if not hasattr(db, "list_team_members"):
        return set()
    try:
        members = db.list_team_members(int(team_id)) or []
        return {int(m.get("athlete_id")) for m in members if m.get("athlete_id") is not None}
    except Exception:
        return set()


def _session_label(s: dict) -> str:
    if not s:
        return "—"
    sid = s.get("id", "—")
    ts = (s.get("ts_start") or "")[:19].replace("T", " ")
    st = (s.get("status") or "—")
    return f"#{sid} · {ts} · {st}"


# =======================
# Cuestionario (layout)
# =======================


def layout_questionnaire():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    # Siempre incluimos q-team aunque sea oculto para evitar IDs inexistentes
    team_selector = html.Div([
        dcc.Dropdown(id="q-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []
    options_users = []
    default_user = None

    if role == "coach" and uid:
        coach_id = int(uid)

        teams = []
        if hasattr(db, "list_teams"):
            try:
                teams = db.list_teams(coach_id) or []
            except Exception:
                teams = []

        team_options = [{"label": "Todos", "value": "ALL"}]
        team_options += [
            {
                "label": f"{t.get('name', 'Equipo')}" + (f" · {t.get('sport')}" if t.get("sport") else ""),
                "value": t.get("id"),
            }
            for t in teams
            if t.get("id") is not None
        ]

        default_team = "ALL"
        if len(team_options) > 1:
            default_team = team_options[1]["value"]  # primer equipo real

        team_selector = html.Div([
            html.Label("Equipo"),
            dcc.Dropdown(
                id="q-team",
                options=team_options,
                value=default_team,
                placeholder="Selecciona equipo...",
            ),
            html.Br(),
        ])

        athletes = _coach_roster(coach_id)

        if default_team not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(default_team))
            if member_ids:
                athletes = [a for a in athletes if int(a.get("id")) in member_ids]
            else:
                athletes = []

        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes
            if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes
            if u and u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]
        options_users = [
            {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
            for u in athletes
            if u.get("id") is not None
        ]
        default_user = options_users[0]["value"] if options_users else None

    if role == "deportista":
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(id="q-user", options=options_users, value=default_user, disabled=True),
        ])
    else:
        user_selector = html.Div([
            html.Label("Deportista"),
            dcc.Dropdown(
                id="q-user",
                options=options_users,
                value=default_user,
                placeholder="Selecciona deportista...",
            ),
        ])

    items = []
    for key, label in Q.questions():
        if key in ("sueno_horas", "duracion", "golpes_cabeza"):
            if key == "sueno_horas":
                rmin, rmax, step, init = 0, 12, 1, 7
            elif key == "duracion":
                rmin, rmax, step, init = 0, 240, 5, 60
            else:
                rmin, rmax, step, init = 0, 20, 1, 0
            items.append(
                html.Div([
                    html.Label(label),
                    dcc.Slider(
                        id=f"q-{key}",
                        min=rmin,
                        max=rmax,
                        step=step,
                        value=init,
                        tooltip={"placement": "bottom"},
                    ),
                ], style={"marginBottom": "8px"})
            )
        else:
            items.append(
                html.Div([
                    html.Label(label),
                    dcc.Slider(
                        id=f"q-{key}",
                        min=1,
                        max=10,
                        step=1,
                        value=5,
                        tooltip={"placement": "bottom"},
                    ),
                ], style={"marginBottom": "8px"})
            )

    return html.Div([
        h2("Cuestionario de Autopercepción"),
        html.Small("Sólo aplicable a deportistas.", style={"opacity": 0.8}),
        html.Br(),
        team_selector,
        user_selector,
        html.Br(),
        html.Div([
            html.Label("Asociar a sesión"),
            dcc.Dropdown(
                id="q-session",
                options=[
                    {"label": "Auto (sesión abierta si existe)", "value": "AUTO"},
                    {"label": "Sin sesión (general)", "value": "NONE"},
                ],
                value="AUTO",
                placeholder="Selecciona sesión…",
                clearable=False,
            ),
            html.Small(
                "Tip: si creas una sesión en Señales, puedes ligar el cuestionario para comparaciones por sesión.",
                style={"opacity": 0.75},
            ),
        ], style={"margin": "8px 0"}),
        html.Div(children=items),
        html.Button("Guardar cuestionario", id="btn-save-q", className="btn btn-primary"),
        html.Br(),
        html.Br(),
        dcc.Graph(id="q-gauge", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
        html.Div(id="q-explain"),
    ])


@callback(
    Output("q-user", "options"),
    Output("q-user", "value"),
    Input("q-team", "value"),
    prevent_initial_call=False,
)
def update_q_user_options(team_id):
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        raise PreventUpdate

    coach_id = session.get("user_id")
    if not coach_id:
        return [], None

    athletes = _coach_roster(int(coach_id))

    if team_id not in (None, "", "ALL"):
        member_ids = _team_member_ids(int(team_id))
        if member_ids:
            athletes = [a for a in athletes if int(a.get("id")) in member_ids]
        else:
            athletes = []

    options = [
        {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
        for u in athletes
        if u.get("id") is not None
    ]
    value = options[0]["value"] if options else None
    return options, value


# ---- Sesiones para asociar cuestionario ----
@callback(
    Output("q-session", "options"),
    Output("q-session", "value"),
    Input("q-user", "value"),
    prevent_initial_call=False,
)
def load_q_sessions(user_id):
    # Siempre damos estas 2 opciones base:
    # - AUTO: usa sesión abierta si existe; si no existe, se crea al guardar
    # - NONE: guarda cuestionario sin asociarlo a sesión
    base_opts = [
        {"label": "Auto (sesión abierta / crear si no existe)", "value": "AUTO"},
        {"label": "Sin sesión (general)", "value": "NONE"},
    ]

    if not user_id:
        return base_opts, "AUTO"

    sessions = []
    if hasattr(db, "list_sessions"):
        try:
            sessions = db.list_sessions(int(user_id), limit=25) or []
        except Exception:
            sessions = []

    opts = base_opts.copy()
    for s in sessions:
        sid = s.get("id")
        if sid is None:
            continue
        ts = (s.get("ts_start") or "")[:16]
        status = s.get("status") or ""
        label = f"#{sid} · {ts} · {status}" if ts else f"#{sid} · {status}"
        opts.append({"label": label, "value": int(sid)})

    return opts, "AUTO"

@callback(
    Output("q-gauge", "figure"),
    Output("q-explain", "children"),
    Input("btn-save-q", "n_clicks"),
    State("q-user", "value"),
    State("q-session", "value"),
    *[State(f"q-{k}", "value") for k, _ in Q.questions()],
    prevent_initial_call=True,
)
def save_q(n, user_id, session_id, *values):
    if not user_id:
        raise PreventUpdate

    # Hardening: si llega None, aplicamos defaults razonables
    defaults = {
        "sueno_horas": 7,
        "duracion": 60,
        "golpes_cabeza": 0,
    }

    ans = {}
    for (k, _), v in zip(Q.questions(), values):
        if v is None:
            v = defaults.get(k, 5)
        ans[k] = v

    wellness = Q.wellness_score(ans)

    # Resolver sesión:
    # - AUTO: usa sesión abierta; si no hay, crea una
    # - NONE: no asocia a sesión
    sid = None
    if session_id in (None, "", "NONE"):
        sid = None
    elif session_id == "AUTO":
        if hasattr(db, "ensure_open_session"):
            try:
                actor_id = _safe_int(session.get("user_id"))
                athlete = db.get_user_by_id(int(user_id)) if hasattr(db, "get_user_by_id") else None
                sport = athlete.get("sport") if athlete else None
                sid = db.ensure_open_session(int(user_id), created_by=actor_id, sport=sport)
            except Exception:
                sid = None
    else:
        sid = _safe_int(session_id)

    db.save_questionnaire(
        int(user_id),
        ans,
        wellness,
        ans.get("rpe"),
        ans.get("duracion"),
        session_id=sid,
    )

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=wellness,
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "#34D7E0"},
            "steps": [
                {"range": [0, 60], "color": "#43141a"},
                {"range": [60, 80], "color": "#3a2f16"},
                {"range": [80, 100], "color": "#103530"},
            ],
        },
        title={"text": "Wellness (0-100)"},
    ))
    apply_chart_style(fig, title="Wellness (0-100)", height=420)

    txt = (
        "Interpretación: >80 listo para entrenar; 60–80 atención; <60 considera reducir carga. "
        "Integra fatiga/DOMS/estrés (restan), sueño y ánimo (suman), golpes a la cabeza y sRPE."
    )
    return fig, txt

def rolling_mean(y, window: int):
    y = list(map(float, y))
    n = len(y)
    if window <= 1 or n == 0:
        return y
    if n < window:
        m = sum(y) / n
        return [m] * n
    cumsum = [0.0]
    for val in y:
        cumsum.append(cumsum[-1] + val)
    res = []
    for i in range(1, n + 1):
        a = max(0, i - window)
        b = i
        w = b - a
        res.append((cumsum[b] - cumsum[a]) / w)
    return res


def layout_history():
    if not session.get("user_id"):
        return html.Div("Inicia sesión para ver esta página.")

    role = _to_str(session.get("role")) or "no autenticado"
    uid = session.get("user_id")

    # Siempre incluimos h-team aunque sea oculto para evitar IDs inexistentes
    team_selector = html.Div([
        dcc.Dropdown(id="h-team", options=[{"label": "Todos", "value": "ALL"}], value="ALL", style={"display": "none"})
    ])

    athletes = []

    if role == "coach" and uid:
        coach_id = int(uid)

        teams = []
        if hasattr(db, "list_teams"):
            try:
                teams = db.list_teams(coach_id) or []
            except Exception:
                teams = []

        team_options = [{"label": "Todos", "value": "ALL"}]
        team_options += [
            {
                "label": f"{t.get('name', 'Equipo')}" + (f" · {t.get('sport')}" if t.get("sport") else ""),
                "value": t.get("id"),
            }
            for t in teams
            if t.get("id") is not None
        ]

        default_team = "ALL"
        if len(team_options) > 1:
            default_team = team_options[1]["value"]

        team_selector = html.Div([
            html.Label("Equipo"),
            dcc.Dropdown(
                id="h-team",
                options=team_options,
                value=default_team,
                placeholder="Selecciona equipo...",
            ),
            html.Br(),
        ])

        athletes = _coach_roster(coach_id)

        if default_team not in (None, "", "ALL"):
            member_ids = _team_member_ids(int(default_team))
            if member_ids:
                athletes = [a for a in athletes if int(a.get("id")) in member_ids]
            else:
                athletes = []

    elif role == "deportista" and uid:
        u = db.get_user_by_id(int(uid))
        athletes = [u] if u and u.get("role") == "deportista" else []

    else:
        athletes = [u for u in db.list_users() if (u.get("role", "deportista") == "deportista")]

    options_users = [
        {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
        for u in (athletes or [])
        if u and u.get("id") is not None
    ]

    return html.Div([
        h2("Histórico de Cuestionarios"),
        html.Small(
            "Sólo deportistas con cuestionarios previos aparecerán en las gráficas.",
            style={"opacity": 0.8},
        ),
        html.Br(),
        team_selector,
        html.Label("Deportista"),
        dcc.Dropdown(id="h-user", options=options_users, placeholder="Selecciona deportista..."),
        html.Br(),
        dcc.Graph(id="h-wellness", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
        dcc.Graph(id="h-load", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
    ])


@callback(
    Output("h-user", "options"),
    Input("h-team", "value"),
    prevent_initial_call=False,
)
def h_team_filter(team_id):
    role = _to_str(session.get("role")) or "no autenticado"
    if role != "coach":
        raise PreventUpdate

    coach_id = session.get("user_id")
    if not coach_id:
        return []

    athletes = _coach_roster(int(coach_id))

    if team_id not in (None, "", "ALL"):
        member_ids = _team_member_ids(int(team_id))
        if member_ids:
            athletes = [a for a in athletes if int(a.get("id")) in member_ids]
        else:
            athletes = []

    return [
        {"label": f"{u.get('name', 'Sin nombre')} · {u.get('sport', '-')}", "value": u.get("id")}
        for u in athletes
        if u.get("id") is not None
    ]


@callback(
    Output("h-wellness", "figure"),
    Output("h-load", "figure"),
    Input("h-user", "value"),
    prevent_initial_call=True,
)
def render_history(user_id):
    if not user_id:
        raise PreventUpdate

    rows = list(reversed(db.list_questionnaires(int(user_id))))
    if not rows:
        return go.Figure(), go.Figure()

    ts, wel, rpe_vals, dur_vals = [], [], [], []
    for r in rows:
        try:
            ts.append(datetime.fromisoformat(r["ts"]))
        except Exception:
            ts.append(r.get("ts"))

        wel.append(float(r.get("wellness_score") or 0.0))
        rpe_vals.append(float(r.get("rpe") or 0.0))

        d = r.get("duration_min", r.get("duration", 0))
        try:
            dur_vals.append(float(d) if d is not None else 0.0)
        except Exception:
            dur_vals.append(0.0)

    ma = rolling_mean(wel, 7)

    f1 = go.Figure()
    f1.add_trace(go.Scatter(x=ts, y=wel, mode="lines+markers", name="Wellness"))
    f1.add_trace(go.Scatter(x=ts, y=ma, mode="lines", name="Media móvil 7"))
    apply_chart_style(f1, title="Wellness vs tiempo", x_title="Fecha", y_title="Wellness", height=420)

    try:
        f1.update_xaxes(tickformat="%b %d\\n%H:%M")
    except Exception:
        pass

    load = (np.array(rpe_vals) * np.array(dur_vals)).tolist()
    f2 = go.Figure()
    f2.add_trace(go.Bar(x=ts, y=load, name="Carga (sRPE)"))
    apply_chart_style(f2, title="Carga interna (RPE × duración)", x_title="Fecha", y_title="Carga", height=420)

    try:
        f2.update_xaxes(tickformat="%b %d\\n%H:%M")
    except Exception:
        pass

    return f1, f2



# =======================
# Dash Pages wrapper
# =======================
try:
    import dash
    dash.register_page(__name__, path="/wellbeing", name="Wellbeing")
except Exception:
    dash = None  # si se importa fuera de Dash Pages, no pasa nada

def layout():
    # Tabs para separar cuestionario e histórico
    return html.Div([
        dcc.Tabs(
            id="wb-tabs",
            value="q",
            children=[
                dcc.Tab(label="Cuestionario", value="q"),
                dcc.Tab(label="Histórico", value="h"),
            ],
        ),
        html.Div(id="wb-content", style={"marginTop": "12px"}),
    ])

@callback(Output("wb-content", "children"), Input("wb-tabs", "value"))
def _render_wb_tab(tab):
    return layout_questionnaire() if tab != "h" else layout_history()