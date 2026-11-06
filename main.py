import ast
import csv
import io
import statistics
from typing import Any, Literal

from mistralai import agents

MAX_ROWS = 100_000

_DATASETS: dict[str, dict[str, Any]] = {}


def _parse_value(raw: str) -> Any:
    text = raw.strip()
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _get_dataset(ctx: agents.ToolCallbackContext) -> dict[str, Any]:
    dataset = _DATASETS.get(ctx.session.id)
    if dataset is None:
        raise agents.ToolError(
            "No hay ningún CSV cargado en esta sesión. Pide al usuario el enlace del archivo "
            "de Google Drive, descárgalo con el conector google_drive_mcp y carga el contenido "
            "con load_csv."
        )
    return dataset


def _require_column(dataset: dict[str, Any], column: str) -> None:
    if column not in dataset["columns"]:
        raise agents.ToolError(
            f"La columna '{column}' no existe. Columnas disponibles: {', '.join(dataset['columns'])}."
        )


@agents.tool
async def load_csv(
    ctx: agents.ToolCallbackContext,
    /,
    csv_text: str,
    source_name: str = "",
) -> dict[str, Any]:
    """Carga el contenido de un archivo CSV en la sesión actual y lo deja listo para analizar.

    csv_text: contenido completo del CSV descargado desde Google Drive con el conector
    google_drive_mcp. source_name: nombre o enlace del archivo, para mostrarlo al usuario.
    """
    if not csv_text or not csv_text.strip():
        raise agents.ToolError("El contenido del CSV está vacío.")
    try:
        dialect = csv.Sniffer().sniff(csv_text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
    if not reader.fieldnames:
        raise agents.ToolError("El CSV no tiene fila de encabezado.")
    columns = list(reader.fieldnames)
    rows: list[dict[str, Any]] = []
    for raw_row in reader:
        if len(rows) >= MAX_ROWS:
            raise agents.ToolError(
                f"El CSV supera el límite de {MAX_ROWS} filas; no se cargó."
            )
        rows.append({key: _parse_value(value or "") for key, value in raw_row.items()})
    _DATASETS[ctx.session.id] = {
        "source": source_name or "CSV cargado",
        "columns": columns,
        "rows": rows,
    }
    return {
        "source": source_name or "CSV cargado",
        "row_count": len(rows),
        "columns": columns,
    }


@agents.tool
async def describe_columns(ctx: agents.ToolCallbackContext, /) -> dict[str, Any]:
    """Describe cada columna del CSV cargado: tipo inferido, nulos, valores únicos y estadísticas."""
    dataset = _get_dataset(ctx)
    rows = dataset["rows"]
    description: list[dict[str, Any]] = []
    for column in dataset["columns"]:
        values = [row.get(column) for row in rows]
        non_null = [v for v in values if v is not None]
        numeric = [v for v in non_null if isinstance(v, (int, float)) and not isinstance(v, bool)]
        entry: dict[str, Any] = {
            "column": column,
            "type": "numeric" if numeric and len(numeric) == len(non_null) else "text",
            "null_count": len(values) - len(non_null),
            "unique_count": len(set(map(repr, non_null))),
        }
        if entry["type"] == "numeric" and numeric:
            entry.update(
                min=min(numeric),
                max=max(numeric),
                mean=round(statistics.fmean(numeric), 4),
            )
        elif non_null:
            counts: dict[str, int] = {}
            for v in non_null:
                counts[repr(v)] = counts.get(repr(v), 0) + 1
            entry["top_values"] = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        description.append(entry)
    return {"source": dataset["source"], "row_count": len(rows), "columns": description}


@agents.tool
async def filter_rows(
    ctx: agents.ToolCallbackContext,
    /,
    column: str,
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains"],
    value: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Filtra filas del CSV cargado según una condición sobre una columna.

    operator: eq (igual), ne (distinto), gt/gte/lt/lte (comparaciones numéricas),
    contains (el texto contiene el valor). value: valor de comparación como texto.
    limit: máximo de filas devueltas en la muestra.
    """
    dataset = _get_dataset(ctx)
    _require_column(dataset, column)
    probe = _parse_value(value)
    matched: list[dict[str, Any]] = []
    for row in dataset["rows"]:
        cell = row.get(column)
        if operator == "eq":
            ok = cell == probe or (cell is not None and repr(cell) == repr(probe))
        elif operator == "ne":
            ok = not (cell == probe or (cell is not None and repr(cell) == repr(probe)))
        elif operator == "contains":
            ok = cell is not None and value.lower() in str(cell).lower()
        else:
            if not isinstance(cell, (int, float)) or not isinstance(probe, (int, float)):
                raise agents.ToolError(
                    f"El operador '{operator}' requiere valores numéricos, pero la columna "
                    f"'{column}' o el valor '{value}' no lo son."
                )
            ok = {
                "gt": cell > probe,
                "gte": cell >= probe,
                "lt": cell < probe,
                "lte": cell <= probe,
            }[operator]
        if ok:
            matched.append(row)
    return {
        "column": column,
        "operator": operator,
        "value": value,
        "matched_count": len(matched),
        "returned_count": min(limit, len(matched)),
        "rows": matched[: max(0, limit)],
    }


@agents.tool
async def group_aggregate(
    ctx: agents.ToolCallbackContext,
    /,
    group_by: list[str],
    value_column: str,
    agg: Literal["count", "sum", "mean", "min", "max", "median"] = "count",
) -> dict[str, Any]:
    """Calcula un agregado por grupos sobre el CSV cargado.

    group_by: columna(s) por las que agrupar. value_column: columna numérica a agregar
    (solo necesaria salvo para agg='count'). agg: count, sum, mean, min, max o median.
    """
    dataset = _get_dataset(ctx)
    for column in group_by:
        _require_column(dataset, column)
    if agg != "count":
        _require_column(dataset, value_column)
    groups: dict[tuple, list[float]] = {}
    for row in dataset["rows"]:
        key = tuple(repr(row.get(column)) for column in group_by)
        if agg == "count":
            groups.setdefault(key, []).append(1.0)
        else:
            v = row.get(value_column)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                groups.setdefault(key, []).append(float(v))
    results: list[dict[str, Any]] = []
    for key, vals in groups.items():
        record: dict[str, Any] = dict(
            zip(group_by, [ast.literal_eval(k) for k in key])
        )
        if agg == "count":
            record[agg] = len(vals)
        elif agg == "sum":
            record[agg] = round(sum(vals), 4)
        elif agg == "mean":
            record[agg] = round(statistics.fmean(vals), 4)
        elif agg == "min":
            record[agg] = min(vals)
        elif agg == "max":
            record[agg] = max(vals)
        else:
            record[agg] = statistics.median(vals)
        results.append(record)
    results.sort(key=lambda r: r[agg], reverse=True)
    return {
        "source": dataset["source"],
        "group_by": group_by,
        "value_column": None if agg == "count" else value_column,
        "agg": agg,
        "group_count": len(results),
        "rows": results[:100],
    }


agent = agents.Agent(
    model="mistral-large-4",
    name="csv-analyst",
    instructions=(
        "Eres un agente conversacional de análisis de datos. El usuario te da un enlace a un "
        "archivo CSV en su Google Drive y te hace preguntas en lenguaje natural sobre sus datos.\n"
        "\n"
        "Flujo de trabajo obligatorio:\n"
        "1. Con el conector google_drive_mcp, localiza el archivo a partir del enlace o nombre "
        "que te dio el usuario (búsqueda por nombre o extracción del ID del enlace) y descarga su "
        "contenido como texto.\n"
        "2. Carga ese contenido con la herramienta load_csv. El archivo queda asociado a esta "
        "sesión y se recuerda durante toda la conversación.\n"
        "3. Responde las preguntas del usuario usando SOLO las herramientas fijas describe_columns, "
        "filter_rows y group_aggregate. Nunca inventes números: cada cifra de tu respuesta debe venir "
        "de una llamada a una herramienta.\n"
        "4. Cuando respondas, cita los números reales y menciona el archivo analizado. Si la pregunta "
        "es ambigua, interpreta la intención razonable usando lo ya cargado en la sesión.\n"
        "\n"
        "La sesión recuerda el CSV cargado y toda la conversación anterior, así que las preguntas de "
        "seguimiento no necesitan que el usuario repita el enlace ni el contexto.\n"
        "No tienes entorno de ejecución: no puedes ejecutar código arbitrario, solo las herramientas "
        "fijas y el conector de Google Drive. Responde siempre en español."
    ),
    tools=[load_csv, describe_columns, filter_rows, group_aggregate],
    connectors=[agents.connector("google_drive_mcp")],
)
