# sensors.py
"""
Catálogo de sensores para PowerSync y helpers de UI.

Incluye:
- ECG
- IMU guante (IMU_GLOVE)
- IMU cabeza (IMU_HEAD)
- EMG brazo (EMG_ARM)
- EMG pierna (EMG_LEG)
- Banda de respiración (RESP_BELT)
"""

from typing import Dict, List


# === Catálogo principal ===
# Cada entrada describe:
# - name: nombre "bonito" para mostrar en tarjetas
# - short: etiqueta corta para checklist/dropdowns
# - description: texto para explicar al coach/deportista
# - signals: qué tipo de señal aporta a la app (tabs de Cargar señales)
# - metrics: qué métricas clave aporta PowerSync con ese sensor
SENSOR_CATALOG: Dict[str, Dict] = {
    "ECG": {
        "name": "ECG / HRV (banda torácica)",
        "short": "ECG / HRV",
        "description": (
            "Sensor de electrocardiograma o banda torácica que permite registrar la señal ECG "
            "y los intervalos R–R. A partir de estos datos PowerSync calcula frecuencia cardiaca, "
            "variabilidad (HRV) y ayuda a monitorizar la recuperación y la carga interna."
        ),
        "signals": ["ecg"],
        "metrics": ["BPM", "SDNN", "RMSSD", "latidos detectados"],
    },
    "IMU_GLOVE": {
        "name": "IMU en guante / muñeca",
        "short": "IMU guante",
        "description": (
            "Unidad inercial colocada en guante o muñeca. Mide aceleración de los golpes para "
            "estimar volumen (nº de golpes), ritmo (golpes/min) e intensidad (g de impacto). "
            "Ideal para analizar sesiones de sparring o saco."
        ),
        "signals": ["imu_hits"],
        "metrics": ["golpes", "golpes/min", "intensidad media (g)", "intensidad máxima (g)"],
    },
    "IMU_HEAD": {
        "name": "IMU en casco / cabeza",
        "short": "IMU cabeza",
        "description": (
            "IMU colocada en el casco o la cabeza. Permite contar impactos a la cabeza y estimar "
            "la intensidad de cada impacto (pico de aceleración en g). Clave para vigilar la "
            "carga a nivel de seguridad y salud cerebral."
        ),
        "signals": ["imu_head"],
        "metrics": ["impactos cabeza", "pico de g por impacto"],
    },
    "EMG_ARM": {
        "name": "EMG brazo (antebrazo / bíceps)",
        "short": "EMG brazo",
        "description": (
            "Electromiografía superficial en brazo (antebrazo, tríceps o bíceps). Permite estimar "
            "el nivel de activación muscular y la fatiga local durante combinaciones de golpes "
            "o trabajo de manoplas."
        ),
        "signals": ["emg"],
        "metrics": ["RMS EMG", "intensidad relativa", "patrón de fatiga simple"],
    },
    "EMG_LEG": {
        "name": "EMG pierna (cuádriceps / isquios)",
        "short": "EMG pierna",
        "description": (
            "Electromiografía superficial en pierna (cuádriceps o isquios). Útil para analizar la "
            "técnica y la fatiga en patadas y desplazamientos, y ver cómo se degrada el gesto con "
            "la fatiga."
        ),
        "signals": ["emg"],
        "metrics": ["RMS EMG", "simetría básica", "patrón de fatiga simple"],
    },
    "RESP_BELT": {
        "name": "Banda de respiración",
        "short": "Respiración",
        "description": (
            "Banda torácica o abdominal para registrar el movimiento respiratorio. Permite estimar "
            "frecuencia respiratoria y observar cómo se recupera el deportista entre rounds o tras "
            "series intensas."
        ),
        "signals": ["resp"],
        "metrics": ["frecuencia respiratoria", "patrón de respiración"],
    },
}


# === Helpers públicos ===

def catalog() -> Dict[str, Dict]:
    """
    Devuelve el catálogo completo de sensores.
    """
    return SENSOR_CATALOG


def labels_for_checklist() -> List[Dict]:
    """
    Opciones para usar en dcc.Checklist (asignación de sensores).

    Ejemplo de salida:
    [
        {"label": "ECG / HRV", "value": "ECG"},
        {"label": "IMU guante", "value": "IMU_GLOVE"},
        ...
    ]
    """
    opts = []
    for code, data in SENSOR_CATALOG.items():
        label = data.get("short") or data.get("name") or code
        opts.append({"label": label, "value": code})
    return opts


def description(code: str) -> str:
    """
    Descripción larga de un sensor, para tooltips o tarjetas.
    """
    info = SENSOR_CATALOG.get(code)
    if not info:
        return "Sensor no reconocido."
    return info.get("description", info.get("name", code))


def metrics_for(code: str) -> List[str]:
    """
    Lista de métricas clave que aporta ese sensor (texto).
    """
    info = SENSOR_CATALOG.get(code, {})
    return list(info.get("metrics", []))


def signals_for(code: str) -> List[str]:
    """
    Lista de tipos de señal / pestañas donde se usa este sensor.

    Ejemplos de valores:
    - ['ecg']
    - ['imu_hits']
    - ['emg']
    - ['resp']
    """
    info = SENSOR_CATALOG.get(code, {})
    return list(info.get("signals", []))


def pretty_signals_for(code: str) -> str:
    """
    Versión amigable de las señales para mostrar en UI.
    """
    mapping = {
        "ecg": "ECG / HRV",
        "imu_hits": "Golpes (IMU guante)",
        "imu_head": "Impactos cabeza (IMU casco)",
        "emg": "EMG (actividad muscular)",
        "resp": "Respiración",
    }
    signals = signals_for(code)
    if not signals:
        return "—"
    names = [mapping.get(s, s) for s in signals]
    return ", ".join(names)
