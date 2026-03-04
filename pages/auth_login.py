from dash import html, dcc, Input, Output, State, callback
from flask import session
import hashlib
import db

def _check_pw(plain: str, stored):
    try:
        import bcrypt
        if isinstance(stored, str):
            stored = stored.encode("utf-8")
        return bcrypt.checkpw((plain or "").encode("utf-8"), stored)
    except Exception:
        # ✅ Fallback: SHA256 (compatible con db.py)
        if stored is None:
            return False
        if isinstance(stored, (bytes, bytearray)):
            stored_bytes = bytes(stored)
        else:
            stored_bytes = str(stored).encode("utf-8", "ignore")

        candidate = hashlib.sha256((plain or "").encode("utf-8")).hexdigest().encode("utf-8")
        return candidate == stored_bytes

layout = html.Div([
    html.Div(className="card", children=[
        html.H2("Iniciar sesión"),
        html.Div([html.Label("Correo"), dcc.Input(id="login-email", type="email", placeholder="tu@correo.com")],
                 style={"margin":"8px 0"}),
        html.Div([html.Label("Contraseña"), dcc.Input(id="login-pass", type="password", placeholder="••••••••")],
                 style={"margin":"8px 0"}),
        html.Div([
            dcc.Checklist(id="login-remember", options=[{"label":"  Recordarme", "value":"remember"}], value=[]),
            html.A("¿Has olvidado tu contraseña?", href="#", style={"marginLeft":"10px","fontSize":"12px","opacity":0.8})
        ], style={"display":"flex","alignItems":"center","gap":"10px","margin":"6px 0 10px"}),
        html.Button("Entrar", id="btn-login", className="btn btn-primary"),
        html.A("Crear cuenta", href="/registro", style={"marginLeft":"12px"}),
        html.Div(id="login-msg", style={"marginTop":"12px","color":"#FFB4B4"}),
        html.Div(id="login-redirect")
    ])
])

@callback(
    Output("login-msg","children"),
    Output("login-redirect","children"),
    Input("btn-login","n_clicks"),
    State("login-email","value"),
    State("login-pass","value"),
    prevent_initial_call=True
)
def do_login(n, email, pw):
    if not email or not pw:
        return "Completa usuario y contraseña.", None

    user = db.get_user_by_email(email)
    if not user:
        return "Usuario o contraseña incorrectos.", None

    stored = user.get("password_hash")
    if not _check_pw(pw, stored):
        return "Usuario o contraseña incorrectos.", None

    session["user_id"] = user["id"]
    session["role"] = user.get("role") or "coach"
    session["name"] = user.get("name") or ""
    session["sport"] = user.get("sport") or ""

    return "", dcc.Location(pathname="/dashboard", id="redirect-login")
