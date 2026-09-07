"""Corrélation logs ↔ traces (observabilité, phase 2 du plan d'intégration).

Un log qui ne porte pas le `trace_id` du span en cours ne peut pas être
retrouvé depuis Grafana → Tempo (« logs de ce span ») ni depuis Loki filtré
par trace — il reste un texte isolé, même une fois structuré en JSON.

Ce filtre lit le span actif via l'API OpenTelemetry (pas le SDK — un filtre
de logging tourne dans du code métier ordinaire, il n'a aucune raison de
dépendre du SDK) et l'injecte dans chaque `LogRecord`. `"0"*32`/`"0"*16`
(zéros hexadécimaux, format standard OTel pour « aucun span ») quand rien
n'est en cours — un log de démarrage avant toute requête, par exemple.
"""

import logging

from opentelemetry import trace


class TraceContextFilter(logging.Filter):
    """Ajoute `trace_id`/`span_id` (hexadécimal, format W3C) à chaque log."""

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        context = span.get_span_context()
        if context.is_valid:
            record.trace_id = format(context.trace_id, "032x")
            record.span_id = format(context.span_id, "016x")
        else:
            record.trace_id = "0" * 32
            record.span_id = "0" * 16
        return True
