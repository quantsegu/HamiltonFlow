"""Apache Ossie (0.2.0.dev0) <-> MetricFlow.

Supported on import: datasets backed by a table, single-column primary keys,
many-to-one relationships, fields (categorical/time), and metrics that are one
aggregate (SUM/COUNT/COUNT DISTINCT/AVG/MIN/MAX) over fields of one dataset, or a
ratio of two such aggregates. Everything else is listed in the report.

MetricFlow needs an aggregation time dimension on the dataset that owns a
measure; metrics on datasets without a time field (e.g. a star-schema fact whose
dates live in a date dimension) are reported as unsupported rather than guessed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

from .base import (
    Conversion,
    ConversionReport,
    node_relation,
    project_configuration,
    require_yaml,
)

OSSIE_VERSION = "0.2.0.dev0"
_AGG = {exp.Sum: "sum", exp.Avg: "average", exp.Min: "min", exp.Max: "max"}


def _expression(node: Any) -> tuple[str | None, str | None]:
    """Pick the ANSI_SQL expression (or first available). Returns (sql, dialect)."""
    dialects = node.get("dialects") if isinstance(node, dict) else node
    if isinstance(dialects, str):
        return dialects, "ANSI_SQL"
    if not dialects:
        return None, None
    by_dialect = {d["dialect"]: d["expression"] for d in dialects}
    if "ANSI_SQL" in by_dialect:
        return by_dialect["ANSI_SQL"], "ANSI_SQL"
    dialect, sql = next(iter(by_dialect.items()))
    return sql, dialect


def from_ossie(source: str | Path | dict[str, Any], time_spine: str = "main.time_spine") -> Conversion:
    """Convert an Ossie semantic model (path, YAML text or dict) to MetricFlow YAML."""
    if isinstance(source, dict):
        model = source
    else:
        text = Path(source).read_text() if Path(str(source)).exists() else str(source)
        model = require_yaml(text, "Ossie document")
    report = ConversionReport()
    models: dict[str, dict[str, Any]] = {}
    fields: dict[str, dict[str, str]] = {}  # dataset -> field name -> sql
    time_fields: dict[str, list[str]] = {}

    for ds in model.get("datasets", []):
        name = ds["name"]
        relation = node_relation(ds.get("source", ""))
        if relation is None:
            report.skip("dataset", name, f"source is not a plain table: {ds.get('source')!r}")
            continue
        sm: dict[str, Any] = {"name": name, "node_relation": relation, "entities": [], "dimensions": []}
        if ds.get("description"):
            sm["description"] = ds["description"]
        pk = ds.get("primary_key") or []
        if len(pk) == 1:
            sm["entities"].append({"name": name, "type": "primary", "expr": pk[0]})
        elif len(pk) > 1:
            report.skip("primary_key", name, f"composite key {pk} (MetricFlow entities are single-expression)")
        fields[name], time_fields[name] = {}, []
        for f in ds.get("fields", []):
            sql, dialect = _expression(f.get("expression"))
            if sql is None:
                report.skip("field", f"{name}.{f['name']}", "no expression")
                continue
            if dialect != "ANSI_SQL":
                report.skip("field", f"{name}.{f['name']}", f"only {dialect} expression available; used as-is")
            fields[name][f["name"]] = sql
            is_time = (f.get("dimension") or {}).get("is_time")
            if is_time is None:
                is_time = f.get("datatype") in {"Date", "Time", "DateTime", "DateTimeTz"}
            dim: dict[str, Any] = {"name": f["name"], "type": "time" if is_time else "categorical"}
            if sql != f["name"]:
                dim["expr"] = sql
            if is_time:
                dim["type_params"] = {"time_granularity": "day"}
                time_fields[name].append(f["name"])
            if f.get("description"):
                dim["description"] = f["description"]
            sm["dimensions"].append(dim)
        models[name] = sm
        report.converted.append(f"dataset:{name}")

    for rel in model.get("relationships", []):
        src, dst = rel["from"], rel["to"]
        if src not in models or dst not in models:
            report.skip("relationship", rel["name"], "references a skipped dataset")
            continue
        if len(rel["from_columns"]) != 1:
            report.skip("relationship", rel["name"], "composite keys unsupported")
            continue
        target = models[dst]
        if not any(e["type"] == "primary" for e in target["entities"]):
            target["entities"].append({"name": dst, "type": "primary", "expr": rel["to_columns"][0]})
        models[src]["entities"].append(
            {"name": dst if not any(e["name"] == dst for e in models[src]["entities"]) else rel["name"],
             "type": "foreign", "expr": rel["from_columns"][0]}
        )
        report.converted.append(f"relationship:{rel['name']}")

    for name, sm in models.items():  # key columns are entities, not dimensions
        keys = {e["name"] for e in sm["entities"]} | {e.get("expr") for e in sm["entities"]}
        sm["dimensions"] = [d for d in sm["dimensions"] if d["name"] not in keys]
        if not any(e["type"] == "primary" for e in sm["entities"]):
            sm["primary_entity"] = sm["name"]
        if time_fields[name]:
            sm["defaults"] = {"agg_time_dimension": time_fields[name][0]}

    metrics: list[dict[str, Any]] = []
    for metric in model.get("metrics", []):
        _convert_metric(metric, models, fields, time_fields, metrics, report)

    docs: list[dict[str, Any]] = []
    for sm in models.values():
        sm = {k: v for k, v in sm.items() if v not in ([], None)}
        docs.append({"semantic_model": sm})
    docs.extend({"metric": m} for m in metrics)
    docs.append(project_configuration(time_spine))
    return Conversion(docs, report)


def _owner(column: exp.Column, fields: dict[str, dict[str, str]]) -> str | None:
    if column.table:
        return column.table if column.table in fields else None
    owners = [d for d, f in fields.items() if column.name in f]
    return owners[0] if len(owners) == 1 else None


def _measure(node: exp.Expression, fields, report_name) -> tuple[str, str, str] | None:
    """(dataset, agg, sql_expr) for one aggregate node, or None."""
    agg = next((v for k, v in _AGG.items() if isinstance(node, k)), None)
    arg = node.this if agg else None
    if isinstance(node, exp.Count):
        arg = node.this
        if isinstance(arg, exp.Distinct):
            agg, arg = "count_distinct", arg.expressions[0]
        elif isinstance(arg, exp.Star):
            agg, arg = "sum", exp.Literal.number(1)
        else:
            agg = "count"
    if agg is None or arg is None:
        return None
    columns = list(arg.find_all(exp.Column))
    owners = {_owner(c, fields) for c in columns}
    if len(owners) > 1 or None in owners:
        return None
    dataset = owners.pop() if owners else None
    inlined = arg.transform(
        lambda n: sqlglot.parse_one(fields[dataset].get(n.name, n.name))
        if isinstance(n, exp.Column)
        else n
    )
    return dataset, agg, inlined.sql()


def _convert_metric(metric, models, fields, time_fields, out, report) -> None:
    name = metric["name"]
    sql, _ = _expression(metric.get("expression"))
    try:
        tree = sqlglot.parse_one(sql)
    except sqlglot.errors.SqlglotError as exc:
        report.skip("metric", name, f"unparseable expression: {exc}")
        return
    if tree.find(exp.Window):
        report.skip("metric", name, "window function")
        return
    parts: list[tuple[str, exp.Expression]]
    if isinstance(tree, exp.Div):
        parts = [("numerator", tree.left), ("denominator", tree.right)]
    else:
        parts = [("simple", tree)]
    measures = []
    for role, node in parts:
        found = _measure(node, fields, name)
        if found is None or found[0] is None:
            report.skip("metric", name, "not a single aggregate over one dataset's fields")
            return
        dataset, agg, expr = found
        if not time_fields.get(dataset):
            report.skip("metric", name, f"dataset {dataset!r} has no time field for MetricFlow's aggregation time dimension")
            return
        measures.append((role, dataset, agg, expr))
    names = []
    for role, dataset, agg, expr in measures:
        mname = name if role == "simple" else f"{name}_{role}"
        reserved = {e["name"] for m in models.values() for e in m["entities"]} | {
            d["name"] for m in models.values() for d in m["dimensions"]
        }
        if mname in reserved:  # MetricFlow forbids a measure sharing an entity/dimension name
            mname = f"{mname}_measure"
        models[dataset].setdefault("measures", []).append({"name": mname, "agg": agg, "expr": expr})
        names.append(mname)
    description = {"description": metric["description"]} if metric.get("description") else {}
    if len(measures) == 1:
        out.append({"name": name, "type": "simple", "type_params": {"measure": names[0]}, **description})
    else:
        for n in names:
            out.append({"name": n, "type": "simple", "type_params": {"measure": n}})
        out.append({"name": name, "type": "ratio", "type_params": {"numerator": {"name": names[0]}, "denominator": {"name": names[1]}}, **description})
    report.converted.append(f"metric:{name}")


# --------------------------------------------------------------------------


_AGG_SQL = {"sum": "SUM", "average": "AVG", "min": "MIN", "max": "MAX", "count": "COUNT"}


def _ds_expr(sql: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def to_ossie(manifest, name: str = "hamilton_flow_model") -> tuple[dict[str, Any], ConversionReport]:
    """Export a PydanticSemanticManifest to an Ossie document (dict) and a report."""
    report = ConversionReport()
    datasets, relationships, measure_sql = [], [], {}
    primary = {}
    for sm in manifest.semantic_models:
        for e in sm.entities:
            if e.type.value == "primary":
                primary[e.name] = sm.name
    for sm in manifest.semantic_models:
        rel = sm.node_relation
        source = ".".join(p for p in (rel.database, rel.schema_name, rel.alias) if p)
        ds: dict[str, Any] = {"name": sm.name, "source": source, "fields": []}
        if sm.description:
            ds["description"] = sm.description
        pk = [e.expr or e.name for e in sm.entities if e.type.value == "primary"]
        if pk:
            ds["primary_key"] = pk
        for d in sm.dimensions:
            is_time = d.type.value == "time"
            field: dict[str, Any] = {
                "name": d.name,
                "expression": _ds_expr(d.expr or d.name),
                "dimension": {"is_time": is_time},
            }
            if is_time:
                field["datatype"] = "Date"
            ds["fields"].append(field)
        datasets.append(ds)
        for e in sm.entities:
            if e.type.value == "foreign" and e.name in primary and primary[e.name] != sm.name:
                relationships.append({
                    "name": f"{sm.name}_to_{primary[e.name]}",
                    "from": sm.name, "to": primary[e.name],
                    "from_columns": [e.expr or e.name],
                    "to_columns": [next(
                        (x.expr or x.name for s in manifest.semantic_models if s.name == primary[e.name]
                         for x in s.entities if x.name == e.name and x.type.value == "primary"), e.name)],
                })
        for m in sm.measures:
            agg = m.agg.value
            raw = m.expr or m.name
            column = f"{sm.name}.{raw}" if raw.isidentifier() else raw
            sql = f"COUNT(DISTINCT {column})" if agg == "count_distinct" else (
                f"{_AGG_SQL[agg]}({column})" if agg in _AGG_SQL else None)
            if sql is None:
                report.skip("measure", f"{sm.name}.{m.name}", f"aggregation {agg} has no portable form")
            else:
                measure_sql[m.name] = (sm.name, sql)
    metrics = []
    for metric in manifest.metrics:
        kind = metric.type.value
        if kind == "simple" and metric.type_params.measure.name in measure_sql:
            sql = measure_sql[metric.type_params.measure.name][1]
        elif kind == "ratio":
            def ref(mm):
                target = next((x for x in manifest.metrics if x.name == mm.name), None)
                if target is not None and target.type.value == "simple":
                    return measure_sql.get(target.type_params.measure.name, (None, None))[1]
            n, d = ref(metric.type_params.numerator), ref(metric.type_params.denominator)
            sql = f"{n} / {d}" if n and d else None
        else:
            sql = None
        if sql is None:
            report.skip("metric", metric.name, f"{kind} metric has no Ossie export")
            continue
        entry: dict[str, Any] = {"name": metric.name, "expression": _ds_expr(sql)}
        if metric.description:
            entry["description"] = metric.description
        metrics.append(entry)
        report.converted.append(f"metric:{metric.name}")
    document: dict[str, Any] = {"version": OSSIE_VERSION, "name": name, "datasets": datasets}
    if relationships:
        document["relationships"] = relationships
    if metrics:
        document["metrics"] = metrics
    return document, report


def dump_ossie(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False)
