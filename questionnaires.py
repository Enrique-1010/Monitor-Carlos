def questions():
    return [
        ("fatiga", "Nivel de fatiga (1-10)"),
        ("doms", "Dolor muscular (DOMS) (1-10)"),
        ("sueno_calidad", "Calidad de sueño (1-10)"),
        ("sueno_horas", "Horas de sueño (0-12)"),
        ("estres", "Estrés percibido (1-10)"),
        ("estado_animo", "Estado de ánimo (1-10)"),
        ("rpe", "Esfuerzo percibido última sesión (1-10)"),
        ("duracion", "Duración última sesión (min)"),
        ("golpes_cabeza", "Golpes a la cabeza recientes (0-20)"),
    ]

def wellness_score(ans: dict) -> float:
    sueno_horas = min(max(ans.get("sueno_horas", 7), 0), 12) / 12 * 100
    sueno_calidad = (ans.get("sueno_calidad", 5) / 10) * 100
    animo = (ans.get("estado_animo", 5) / 10) * 100

    fatiga = (ans.get("fatiga", 5) / 10) * 100
    doms = (ans.get("doms", 5) / 10) * 100
    estres = (ans.get("estres", 5) / 10) * 100

    golpes = min(max(ans.get("golpes_cabeza", 0), 0), 20) / 20 * 100

    pos = 0.35 * sueno_horas + 0.25 * sueno_calidad + 0.20 * animo
    neg = 0.25 * fatiga + 0.20 * doms + 0.20 * estres + 0.10 * golpes
    score = pos - neg + 50
    return float(max(0, min(100, score)))
