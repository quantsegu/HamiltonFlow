"""Builder model: a JSON-friendly spec that compiles to native MetricFlow YAML.

The Builder UI edits this spec; ``compile_documents`` turns it into the documents
MetricFlow reads, and ``spec_from_manifest`` goes the other way so an existing
project opens in the UI. Metrics can reference other metrics (ratio, derived), so
metrics can be stacked on metrics.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .errors import ManifestError, RequestError
from .interchange.base import node_relation, project_configuration

NAME = re.compile(r"^(?!.*__)[a-z][a-z0-9_]*[a-z0-9]$")
TABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")
AGGREGATIONS = ["sum", "count", "count_distinct", "average", "min", "max", "median", "sum_boolean"]
METRIC_TYPES = ["simple", "ratio", "derived", "cumulative"]
GRAINS = ["day", "week", "month", "quarter", "year"]
SPEC_FILE = "model.json"
YAML_FILE = "model.yaml"


class SpecError(RequestError):
    """The builder spec is structurally invalid (reported per field)."""


def _fail(path: str, message: str) -> None:
    raise SpecError(f"{path}: {message}", details={"field": path})


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not NAME.match(value):
        _fail(path, "use at least 2 lowercase letters, digits or single underscores, starting with a letter and not ending in an underscore")
    return value


def _expr_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def metric_ref(own: str, names: list, value: Any, path: str) -> str:
    if value not in names or value == own:
        _fail(path, "pick another existing metric")
    return value


def compile_documents(spec: dict[str, Any], time_spine: str = "main.time_spine") -> list[dict]:
    """Validate structure and compile the spec to MetricFlow documents."""
    if not isinstance(spec, dict):
        _fail("spec", "must be an object")
    apply_filter_rows(spec)
    seen: dict[str, str] = {}

    def unique(kind: str, name: str, path: str) -> None:
        if name in seen:
            _fail(path, f"{kind} name {name!r} is already used by a {seen[name]}")
        seen[name] = kind

    docs: list[dict] = []
    measure_owner: dict[str, str] = {}
    for i, sm in enumerate(spec.get("semantic_models", [])):
        where = f"semantic_models[{i}]"
        name = _name(sm.get("name"), f"{where}.name")
        table = sm.get("table", "")
        relation = node_relation(table) if TABLE.match(table or "") else None
        if relation is None:
            _fail(f"{where}.table", "expected [database.]schema.table")
        out: dict[str, Any] = {"name": name, "node_relation": relation}
        if _expr_or_none(sm.get("description")):
            out["description"] = sm["description"].strip()
        entities, dims, measures = [], [], []
        for j, e in enumerate(sm.get("entities", [])):
            ename = _name(e.get("name"), f"{where}.entities[{j}].name")
            if e.get("type") not in ("primary", "unique", "foreign", "natural"):
                _fail(f"{where}.entities[{j}].type", "primary, unique, foreign or natural")
            item = {"name": ename, "type": e["type"]}
            if _expr_or_none(e.get("expr")):
                item["expr"] = e["expr"].strip()
            entities.append(item)
        time_dims = []
        for j, d in enumerate(sm.get("dimensions", [])):
            dname = _name(d.get("name"), f"{where}.dimensions[{j}].name")
            item = {"name": dname, "type": "time" if d.get("type") == "time" else "categorical"}
            if _expr_or_none(d.get("expr")):
                item["expr"] = d["expr"].strip()
            if item["type"] == "time":
                grain = d.get("granularity", "day")
                if grain not in GRAINS:
                    _fail(f"{where}.dimensions[{j}].granularity", f"one of {GRAINS}")
                item["type_params"] = {"time_granularity": grain}
                time_dims.append(dname)
            dims.append(item)
        for j, m in enumerate(sm.get("measures", [])):
            mname = _name(m.get("name"), f"{where}.measures[{j}].name")
            if m.get("agg") not in AGGREGATIONS:
                _fail(f"{where}.measures[{j}].agg", f"one of {AGGREGATIONS}")
            item = {"name": mname, "agg": m["agg"]}
            if _expr_or_none(m.get("expr")):
                item["expr"] = m["expr"].strip()
            measures.append(item)
            measure_owner[mname] = name
        if not any(e["type"] == "primary" for e in entities):
            out["primary_entity"] = name
        out["entities"] = entities
        if dims:
            out["dimensions"] = dims
        if measures:
            out["measures"] = measures
            agg_time = sm.get("agg_time_dimension") or (time_dims[0] if time_dims else None)
            if agg_time not in time_dims:
                _fail(f"{where}.agg_time_dimension", "measures need a time dimension in the same model")
            out["defaults"] = {"agg_time_dimension": agg_time}
        docs.append({"semantic_model": out})
        for kind, items in (("entity", entities), ("dimension", dims), ("measure", measures)):
            for it in items:
                if kind == "measure":
                    unique("measure", it["name"], f"{where}.measures")
    metric_names = [m.get("name") for m in spec.get("metrics", [])]
    for i, m in enumerate(spec.get("metrics", [])):
        where = f"metrics[{i}]"
        name = _name(m.get("name"), f"{where}.name")
        kind = m.get("type")
        if kind not in METRIC_TYPES:
            _fail(f"{where}.type", f"one of {METRIC_TYPES}")
        out = {"name": name, "type": kind}
        for key in ("description", "label"):
            if _expr_or_none(m.get(key)):
                out[key] = m[key].strip()
        if _expr_or_none(m.get("filter")):
            out["filter"] = m["filter"].strip()

        if kind in ("simple", "cumulative"):
            measure = m.get("measure")
            if measure not in measure_owner:
                _fail(f"{where}.measure", "pick a measure defined in a semantic model")
            if kind == "simple":
                out["type_params"] = {"measure": measure}
            else:
                cumulative = {}
                if _expr_or_none(m.get("window")):
                    cumulative["window"] = m["window"].strip()
                if m.get("grain_to_date"):
                    if m["grain_to_date"] not in GRAINS:
                        _fail(f"{where}.grain_to_date", f"one of {GRAINS}")
                    cumulative["grain_to_date"] = m["grain_to_date"]
                out["type_params"] = {"measure": measure}
                if cumulative:
                    out["type_params"]["cumulative_type_params"] = cumulative
        elif kind == "ratio":
            out["type_params"] = {
                "numerator": {"name": metric_ref(name, metric_names, m.get("numerator"), f"{where}.numerator")},
                "denominator": {"name": metric_ref(name, metric_names, m.get("denominator"), f"{where}.denominator")},
            }
        else:
            expr = _expr_or_none(m.get("expr"))
            inputs = m.get("inputs") or []
            if not expr or not inputs:
                _fail(f"{where}.expr", "give an expression and at least one input metric")
            refs = []
            for j, inp in enumerate(inputs):
                ref = {"name": metric_ref(name, metric_names, inp.get("name"), f"{where}.inputs[{j}].name")}
                if _expr_or_none(inp.get("alias")):
                    ref["alias"] = _name(inp["alias"], f"{where}.inputs[{j}].alias")
                if _expr_or_none(inp.get("offset_window")):
                    ref["offset_window"] = inp["offset_window"].strip()
                refs.append(ref)
            out["type_params"] = {"expr": expr, "metrics": refs}
        docs.append({"metric": out})
    docs.append(project_configuration(time_spine))
    return docs


def to_yaml(documents: list[dict]) -> str:
    return yaml.safe_dump_all(documents, sort_keys=False)


def check(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Return a list of issues ({"level","message"}) without saving anything."""
    from .nodes import load_manifest

    try:
        documents = compile_documents(spec)
    except SpecError as exc:
        return [{"level": "error", "message": exc.message}]
    if not spec.get("metrics"):
        return [{"level": "info", "message": "Add at least one metric to produce a queryable model."}]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / YAML_FILE).write_text(to_yaml(documents))
        try:
            load_manifest(tmp)
        except ManifestError as exc:
            return [{"level": "error", "message": exc.message}]
    return []


def save(project: Path, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Persist the spec and generated YAML if (and only if) the model is valid."""
    issues = check(spec)
    if any(i["level"] == "error" for i in issues):
        return issues
    project.mkdir(parents=True, exist_ok=True)
    import json

    (project / SPEC_FILE).write_text(json.dumps(spec, indent=2))
    (project / YAML_FILE).write_text(to_yaml(compile_documents(spec)))
    return issues


def load_spec(project: Path) -> dict[str, Any]:
    import json

    path = project / SPEC_FILE
    if path.exists():
        return json.loads(path.read_text())
    return {"semantic_models": [], "metrics": []}


def spec_from_manifest(manifest) -> dict[str, Any]:
    """Open an existing MetricFlow manifest in the builder."""
    models = []
    for sm in manifest.semantic_models:
        rel = sm.node_relation
        table = ".".join(p for p in (rel.database, rel.schema_name, rel.alias) if p)
        item: dict[str, Any] = {
            "name": sm.name,
            "table": table,
            "description": sm.description or "",
            "entities": [
                {"name": e.name, "type": e.type.value, "expr": e.expr or ""} for e in sm.entities
            ],
            "dimensions": [
                {
                    "name": d.name,
                    "type": d.type.value,
                    "expr": d.expr or "",
                    "granularity": d.type_params.time_granularity.value if d.type_params else "day",
                }
                for d in sm.dimensions
            ],
            "measures": [{"name": m.name, "agg": m.agg.value, "expr": m.expr or ""} for m in sm.measures],
        }
        if sm.defaults and sm.defaults.agg_time_dimension:
            item["agg_time_dimension"] = sm.defaults.agg_time_dimension
        models.append(item)
    metrics = []
    for m in manifest.metrics:
        tp = m.type_params
        item = {"name": m.name, "type": m.type.value, "description": m.description or ""}
        if m.filter is not None and m.filter.where_filters:
            item["filter"] = m.filter.where_filters[0].where_sql_template
        if m.type.value in ("simple", "cumulative") and tp.measure:
            item["measure"] = tp.measure.name
            cum = getattr(tp, "cumulative_type_params", None)
            if cum:
                item["window"] = str(cum.window) if cum.window else ""
                item["grain_to_date"] = cum.grain_to_date.value if cum.grain_to_date else ""
        elif m.type.value == "ratio":
            item["numerator"], item["denominator"] = tp.numerator.name, tp.denominator.name
        elif m.type.value == "derived":
            item["expr"] = tp.expr
            item["inputs"] = [
                {"name": r.name, "alias": r.alias or "", "offset_window": str(r.offset_window or "")}
                for r in tp.metrics or []
            ]
        else:
            continue
        metrics.append(item)
    return {"semantic_models": models, "metrics": metrics}


# ---------------------------------------------------------------------------
# Guided metric creation: filters, reachable dimensions, quick metrics
# ---------------------------------------------------------------------------

FILTER_OPS = {
    "eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    "in": "IN", "contains": "LIKE", "null": "IS NULL", "notnull": "IS NOT NULL",
}
_QUALIFIED = re.compile(r"^[a-z][a-z0-9_]*(__[a-z][a-z0-9_]*)+$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
QUICK_AGGS = {"sum", "average", "min", "max", "median", "count", "count_distinct", "count_rows"}


def _literal(value: str) -> str:
    value = str(value).strip()
    if _NUMBER.match(value) or value.lower() in ("true", "false"):
        return value.lower()
    return "'" + value.replace("'", "''") + "'"


def render_filter(rows: list[dict[str, str]]) -> str:
    """Turn UI filter rows into a MetricFlow where-clause (AND-joined)."""
    parts = []
    for i, row in enumerate(rows or []):
        dim, op = row.get("dimension", ""), row.get("op", "eq")
        if not _QUALIFIED.match(dim or ""):
            _fail(f"filter[{i}].dimension", "pick a dimension")
        if op not in FILTER_OPS:
            _fail(f"filter[{i}].op", f"one of {sorted(FILTER_OPS)}")
        left = "{{ Dimension('" + dim + "') }}"
        value = row.get("value", "")
        if op in ("null", "notnull"):
            parts.append(f"{left} {FILTER_OPS[op]}")
            continue
        if str(value).strip() == "":
            _fail(f"filter[{i}].value", "enter a value")
        if op == "in":
            items = [v for v in (s.strip() for s in str(value).split(",")) if v]
            parts.append(f"{left} IN ({', '.join(_literal(v) for v in items)})")
        elif op == "contains":
            parts.append(f"{left} LIKE {_literal('%' + str(value).strip() + '%')}")
        else:
            parts.append(f"{left} {FILTER_OPS[op]} {_literal(value)}")
    return " AND ".join(parts)


def _entity_prefix(sm: dict[str, Any]) -> str:
    primary = next((e["name"] for e in sm.get("entities", []) if e.get("type") == "primary"), None)
    return primary or sm.get("primary_entity") or sm["name"]


def reachable_dimensions(spec: dict[str, Any], model_name: str) -> list[dict[str, str]]:
    """Dimensions usable in a filter on a measure of ``model_name``, with their qualified names."""
    models = {m["name"]: m for m in spec.get("semantic_models", [])}
    if model_name not in models:
        return []
    home = models[model_name]
    out = [
        {"name": f"{_entity_prefix(home)}__{d['name']}", "label": d["name"], "type": d.get("type", "categorical"), "model": model_name}
        for d in home.get("dimensions", []) if d.get("name")
    ]
    for entity in home.get("entities", []):
        if entity.get("type") != "foreign":
            continue
        for other in models.values():
            if other["name"] != model_name and any(
                e.get("type") == "primary" and e["name"] == entity["name"] for e in other.get("entities", [])
            ):
                out += [
                    {"name": f"{entity['name']}__{d['name']}", "label": f"{entity['name']} → {d['name']}", "type": d.get("type", "categorical"), "model": other["name"]}
                    for d in other.get("dimensions", []) if d.get("name")
                ]
    return out


def quick_metric(spec: dict[str, Any], columns: list[dict[str, str]], request: dict[str, Any]) -> dict[str, Any]:
    """Add a simple metric from 'aggregate this column', creating model/measure as needed.

    ``columns`` describes the table's columns (needed only to draft a model).
    Returns {"spec", "metric", "measure", "model_created"}; the input spec is not mutated.
    """
    import copy

    from .sources import suggest

    spec = copy.deepcopy(spec)
    table, agg = request.get("table", ""), request.get("agg", "")
    if agg not in QUICK_AGGS:
        _fail("agg", f"one of {sorted(QUICK_AGGS)}")
    column = request.get("column", "")
    if agg != "count_rows" and column not in {c["name"] for c in columns}:
        _fail("column", "pick a column of the table")
    name = _name(request.get("name"), "name")
    if any(m.get("name") == name for m in spec.get("metrics", [])):
        _fail("name", f"a metric called {name!r} already exists")
    model = next((m for m in spec.get("semantic_models", []) if m.get("table") == table), None)
    created = model is None
    if created:
        model = suggest(table, columns)["semantic_model"]
        model.pop("measures", None)
        used = {m["name"] for m in spec.get("semantic_models", [])}
        if model["name"] in used:
            model["name"] += "_model"
        spec.setdefault("semantic_models", []).append(model)
    time_dims = [d["name"] for d in model.get("dimensions", []) if d.get("type") == "time"]
    if not time_dims:
        _fail("table", f"{table} has no date or timestamp column; MetricFlow needs one to aggregate measures over time")
    model.setdefault("agg_time_dimension", time_dims[0])
    if agg == "count_rows":
        measure, expr, mfagg = f"{model['name']}_rows", "1", "sum"
    else:
        measure, expr, mfagg = f"{agg}_{column.lower()}", column, agg
    existing = next((x for x in model.setdefault("measures", []) if x.get("agg") == mfagg and x.get("expr") == expr), None)
    if existing:
        measure = existing["name"]
    else:
        taken = {x["name"] for m in spec["semantic_models"] for x in m.get("measures", [])}
        base, n = measure, 2
        while measure in taken:
            measure, n = f"{base}_{n}", n + 1
        model["measures"].append({"name": measure, "agg": mfagg, "expr": expr})
    rows = request.get("filter_rows") or []
    metric = {"name": name, "type": "simple", "measure": measure, "description": request.get("description", "")}
    if rows:
        metric["filter_rows"] = rows
        metric["filter"] = render_filter(rows)
    spec.setdefault("metrics", []).append(metric)
    return {"spec": spec, "metric": name, "measure": measure, "model_created": created}


def apply_filter_rows(spec: dict[str, Any]) -> dict[str, Any]:
    """Regenerate ``filter`` text for metrics edited through the filter builder."""
    for m in spec.get("metrics", []):
        if m.get("filter_rows"):
            m["filter"] = render_filter(m["filter_rows"])
    return spec
