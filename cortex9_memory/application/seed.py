"""Seeds estructurales (sin datos personales): tipos, etiquetas, arquetipos y agentes base."""
from __future__ import annotations

MEMORY_TYPES = [
    ("world", "Hechos sobre el mundo externo: entorno, herramientas, cuentas, rutas, servicios."),
    ("user_profile", "Preferencias y datos declarados explícitamente sobre el usuario."),
    ("behavior", "Reglas de conducta para los agentes (cómo trabajar, qué no hacer)."),
    ("workstream", "Estado de un frente de trabajo: proyecto, repos, tesis, pendientes."),
]

CONFIDENCE_LABELS = [
    ("alta", "Verificación alta", "Confirmado en norma, resolución o fuente primaria abierta.", 0.85, 1.0),
    ("media", "Verificación media", "Atribuido a organismo o relevamiento secundario sin documento primario abierto.", 0.6, 0.85),
    ("estimacion", "Estimación / Conjetura", "Inferencia analítica declarada y justificada.", 0.0001, 0.6),
    ("no_encontrado", "NO ENCONTRADO", "Dato relevante no hallado en fuentes abiertas; se declara en vez de completarlo.", 0.0, 0.0),
]

# Arquetipos autorales (corpus público de Federico Nicolas Baez Rizzo).
ARCHETYPES = [
    (
        "formalizador-de-lo-informal",
        "Formalizador de lo informal",
        "Convierte una asimetría vivida en un teorema o índice con nombre.",
        [
            "Toda asimetría observada se formaliza como T*/I* con nombre propio",
            "Definición operativa explícita",
            "Ejemplos de corpus: TDEL, TAHT, TTRI (Entrópica); TDAF/TAFD (Epistémica)",
        ],
    ),
    (
        "arquitecto-bajo-restriccion",
        "Arquitecto bajo restricción",
        "Razona con trilemas y dependencia de senda: no se puede maximizar todo a la vez.",
        [
            "Nombrar tres objetivos en tensión",
            "Mostrar qué se sacrifica en cada vértice",
            "Conectar con la trayectoria histórica e institucional (dependencia de senda)",
        ],
    ),
    (
        "epistemologo-soberano",
        "Epistemólogo soberano",
        "Propone pasar de la confianza en la herramienta a la verificación del método.",
        [
            "Señalar violencia epistémica y asimetrías de opacidad",
            "Soberanía del Sur Global",
            "Humildad epistémica: etiquetar alta/media/estimación/NO ENCONTRADO",
        ],
    ),
    (
        "constructor-de-instrumentos",
        "Constructor de instrumentos",
        "Presenta suites de instrumentos con nombre propio, acrónimo, definición y criterio de refutación.",
        [
            "Acrónimo T*/R*/I*/F* con sufijo -FNBR cuando es propio",
            "Definición operativa y cómo se mide",
            "Criterio explícito de refutación empírica",
            "Ejemplos de corpus: IOF, TEF, FVFI, ISFD, HEF, TCH, AEGH, SEEH, Índice de Acoplamiento Transductivo",
        ],
    ),
]

AGENTS = [
    ("primary", "Agente ejecutor principal"),
    ("companion", "Agente par/compañero en la misma máquina"),
    ("grok", "Instancia externa (lee/escribe vía export/import JSON)"),
]
