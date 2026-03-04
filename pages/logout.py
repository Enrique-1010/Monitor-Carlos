from dash import html, dcc
from flask import session

def layout():
    session.clear()
    return html.Div([
        html.Div("Cerrando sesión…", style={"opacity": 0.7}),
        dcc.Location(pathname="/login", id="redirect-logout")
    ])
