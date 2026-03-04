from dash import html, dcc, Input, Output, State, callback
from flask import session
import db

DEPORTES = ["Taekwondo","Judo","Kickboxing","Box","Muay Thai","MMA","Karate","Sambo"]
SYMS = "!@#$%^&*()-_=+[]{};:'\",.<>/?\\|`~"


def strength_score(pw: str) -> int:
    if not pw:
        return 0
    length = len(pw)
    has_lower = any(c.islower() for c in pw)
    has_upper = any(c.isupper() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_sym = any(c in SYMS for c in pw)
    classes = sum([has_lower, has_upper, has_digit, has_sym])
    score = min(6, length // 2) + classes * 2
    if length >= 12:
        score += 2
    return min(score, 14)


def strength_label_color(score: int):
    pct = int(score / 14 * 100) if score > 0 else 0
    if score <= 3:
        return "Muy débil", "#ff4d4d", pct
    if score <= 6:
        return "Débil", "#ff9f43", pct
    if score <= 9:
        return "Media", "#f1c40f", pct
    if score <= 12:
        return "Fuerte", "#2ecc71", pct
    return "Excelente", "#00f28a", pct


layout = html.Div([
    html.Div(className="card", children=[
        html.H2("Crea tu cuenta"),

        html.Div([html.Label("Nombre completo"),
                  dcc.Input(id="reg-name", type="text", placeholder="Nombre y Apellidos")],
                 style={"margin": "8px 0"}),

        html.Div([html.Label("Correo"),
                  dcc.Input(id="reg-email", type="email", placeholder="tu@correo.com")],
                 style={"margin": "8px 0"}),

        html.Div([
            html.Label("Contraseña"),
            dcc.Input(id="reg-pass", type="password", placeholder="••••••••"),
            html.Div(style={"marginTop": "8px"}, children=[
                html.Label("Fuerza de la contraseña", style={"fontSize": "12px"}),
                html.Div([html.Div(id="pw-bar", style={
                    "height": "8px", "width": "0%", "borderRadius": "8px",
                    "background": "#2b3a52", "transition": "width .25s"
                })],
                         style={"background": "#0d131b", "border": "1px solid #283142",
                                "borderRadius": "10px", "padding": "4px"}),
                html.Div(id="pw-label", style={"marginTop": "6px", "fontSize": "12px", "color": "#b9c4cf"})
            ])
        ], style={"margin": "8px 0"}),

        html.Div([html.Label("Confirmar contraseña"),
                  dcc.Input(id="reg-pass2", type="password", placeholder="Repita la contraseña")],
                 style={"margin": "8px 0"}),

        html.Div([html.Label("Rol"),
                  dcc.Dropdown(
                      id="reg-role",
                      options=[{"label": "Coach", "value": "coach"}, {"label": "Deportista", "value": "deportista"}],
                      placeholder="Selecciona rol…"
                  )],
                 style={"margin": "8px 0"}),

        # Deporte + "Otro (Especificar)" + input condicional (IDs únicos para no chocar)
        html.Div([
            html.Label("Deporte / Arte marcial"),
            dcc.Dropdown(
                id="reg-sport",
                options=[{"label": d, "value": d} for d in DEPORTES] + [{"label": "Otro (Especificar)", "value": "OTRA"}],
                placeholder="Selecciona deporte…"
            ),
            html.Div(
                id="reg-sport-custom-box",
                style={"display": "none", "marginTop": "8px"},
                children=[
                    html.Label("Especifica tu deporte / especialidad"),
                    dcc.Input(
                        id="reg-sport-custom",
                        type="text",
                        placeholder="Ej: BJJ, Lucha olímpica, K-1, etc."
                    )
                ]
            )
        ], style={"margin": "8px 0"}),

        html.Button("Registrarme", id="btn-register", className="btn btn-primary"),
        html.A("¿Ya tienes cuenta? Inicia sesión", href="/login", style={"marginLeft": "12px"}),

        html.Div(id="reg-msg", style={"marginTop": "12px", "color": "#FFB4B4"}),
        html.Div(id="reg-redirect")
    ])
])


@callback(Output("pw-bar", "style"), Output("pw-label", "children"), Input("reg-pass", "value"))
def update_pw_strength(pw):
    score = strength_score(pw or "")
    label, color, pct = strength_label_color(score)
    style = {
        "height": "8px",
        "width": f"{pct}%",
        "borderRadius": "8px",
        "background": color,
        "transition": "width .25s, background .25s",
    }
    hint = " Sugerencia: usa 8+ caracteres con mayúsculas, minúsculas, números y símbolo." if score < 7 else ""
    return style, f"{label} ({pct}%) {hint}"


@callback(Output("reg-sport-custom-box", "style"), Input("reg-sport", "value"))
def toggle_custom_sport(selected):
    return {"display": "block", "marginTop": "8px"} if selected == "OTRA" else {"display": "none"}


@callback(
    Output("reg-msg", "children"),
    Output("reg-redirect", "children"),
    Input("btn-register", "n_clicks"),
    State("reg-name", "value"),
    State("reg-email", "value"),
    State("reg-pass", "value"),
    State("reg-pass2", "value"),
    State("reg-role", "value"),
    State("reg-sport", "value"),
    State("reg-sport-custom", "value"),
    prevent_initial_call=True
)
def do_register(n, name, email, pw, pw2, role, sport, sport_custom):
    if not all([name, email, pw, pw2, role]):
        return "Falta completar campos obligatorios (nombre, correo, contraseña, confirmar, rol).", None

    if not sport:
        return "Selecciona deporte (o 'Otro').", None

    if sport == "OTRA":
        if not (sport_custom and str(sport_custom).strip()):
            return "Seleccionaste 'Otro'. Especifica tu deporte / especialidad.", None
        sport = str(sport_custom).strip()

    if pw != pw2:
        return "Las contraseñas no coinciden.", None

    if strength_score(pw) < 7:
        return "Contraseña demasiado débil. Usa 8+ caracteres con mayúsculas, minúsculas, números y símbolo.", None

    email_clean = (email or "").strip()

    try:
        db.create_user(name, email_clean, pw, role, sport)
    except Exception as e:
        return f"Error: {e}", None

    user = db.get_user_by_email(email_clean)
    session["user_id"] = user["id"]
    session["role"] = user["role"] or "deportista"
    session["name"] = user["name"] or ""
    session["sport"] = user["sport"] or ""

    return "", dcc.Location(pathname="/dashboard", id="redirect-register")
