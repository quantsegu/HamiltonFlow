"""LookML -> MetricFlow (basic, explicit about gaps). Requires ``pip install lkml``.

Converted: views with ``sql_table_name``; string/number/yesno/date dimensions;
``dimension_group: type: time``; ``primary_key``; measures of type sum, count,
average, min, max, count_distinct, median; many_to_one / one_to_one explore joins
whose ``sql_on`` is ``${a.x} = ${b.y}``.

Reported as unsupported (never silently dropped): derived tables, liquid/templated
SQL, ``type: number``/``list``/running-total measures, filtered measures, tier/bin/
location dimensions, durations, one_to_many joins, parameters, access filters,
``sql_always_where`` and table calculations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..errors import ManifestError
from .base import Conversion, ConversionReport, node_relation, project_configuration

_MEASURES = {
    "sum": "sum",
    "average": "average",
    "min": "min",
    "max": "max",
    "count_distinct": "count_distinct",
    "median": "median",
    "count": "count",
}
_CATEGORICAL = {"string", "number", "yesno", "zipcode"}
_REF = re.compile(r"\$\{([^}]+)\}")
_LIQUID = re.compile(r"\{%|\{\{|_user_attributes|_filters\[")
_JOIN_ON = re.compile(r"^\s*\$\{(\w+)\.(\w+)\}\s*=\s*\$\{(\w+)\.(\w+)\}\s*$")
_GRAIN = ["day", "week", "month", "quarter", "year"]


class _Unsupported(Exception):
    pass


def _load(source: str | Path) -> dict[str, list[dict[str, Any]]]:
    try:
        import lkml
    except ImportError as exc:  # pragma: no cover
        raise ManifestError("LookML import needs the 'lkml' package: pip install lkml") from exc
    path = Path(str(source))
    texts = (
        [p.read_text() for p in sorted(path.rglob("*.lkml"))]
        if path.is_dir()
        else [path.read_text()]
        if path.exists()
        else [str(source)]
    )
    merged: dict[str, list] = {"views": [], "explores": []}
    for text in texts:
        try:
            parsed = lkml.load(text)
        except Exception as exc:
            raise ManifestError(f"Cannot parse LookML: {exc}") from exc
        merged["views"] += parsed.get("views", [])
        merged["explores"] += parsed.get("explores", [])
    return merged


def _sql(raw: str, view: str, local: dict[str, str]) -> str:
    """Resolve ``${TABLE}.col`` and same-view ``${field}`` references."""
    if _LIQUID.search(raw):
        raise _Unsupported("liquid/templated SQL")

    def sub(match: re.Match) -> str:
        ref = match.group(1)
        if ref == "TABLE":
            return ""
        name = ref.split(".")[-1] if ref.split(".")[0] in (view, "") else None
        if ref.count(".") == 0 or ref.split(".")[0] == view:
            name = ref.split(".")[-1]
            if name in local:
                return f"({local[name]})"
        raise _Unsupported(f"reference to {ref!r} outside this view")

    return _REF.sub(sub, raw).replace("..", ".").lstrip(".").strip().replace(" .", " ")


def _clean_table(raw: str) -> str:
    return raw.replace("`", "").replace('"', "").strip()


def from_lookml(source: str | Path, time_spine: str = "main.time_spine") -> Conversion:
    data = _load(source)
    report = ConversionReport()
    models: dict[str, dict[str, Any]] = {}
    exprs: dict[str, dict[str, str]] = {}
    times: dict[str, list[str]] = {}
    metrics: list[dict[str, Any]] = []

    for view in data["views"]:
        name = view["name"]
        for key in ("derived_table", "parameters", "filters"):
            if key in view:
                report.skip("view", name, f"{key} not supported" if key != "derived_table" else "derived_table")
        if "sql_table_name" not in view:
            if "derived_table" not in view:
                report.skip("view", name, "no sql_table_name")
            continue
        relation = node_relation(_clean_table(view["sql_table_name"]))
        if relation is None:
            report.skip("view", name, f"sql_table_name not a plain table: {view['sql_table_name']!r}")
            continue
        sm: dict[str, Any] = {"name": name, "node_relation": relation, "entities": [], "dimensions": [], "measures": []}
        local: dict[str, str] = {}
        exprs[name], times[name] = local, []
        for dim in view.get("dimensions", []):
            local[dim["name"]] = dim.get("sql", "${TABLE}." + dim["name"])
        for grp in view.get("dimension_groups", []):
            local[grp["name"]] = grp.get("sql", "${TABLE}." + grp["name"])
        for dim in view.get("dimensions", []):
            dname, dtype = dim["name"], dim.get("type", "string")
            try:
                sql = _sql(local[dname], name, {k: v for k, v in local.items() if k != dname})
            except _Unsupported as exc:
                report.skip("dimension", f"{name}.{dname}", str(exc))
                continue
            local[dname] = sql
            if dim.get("primary_key") == "yes":
                sm["entities"].append({"name": name, "type": "primary", "expr": sql})
                continue
            if dtype in _CATEGORICAL:
                entry = {"name": dname, "type": "categorical"}
            elif dtype == "date":
                entry = {"name": dname, "type": "time", "type_params": {"time_granularity": "day"}}
                times[name].append(dname)
            else:
                report.skip("dimension", f"{name}.{dname}", f"type: {dtype}")
                continue
            if sql != dname:
                entry["expr"] = sql
            sm["dimensions"].append(entry)
        for grp in view.get("dimension_groups", []):
            gname = grp["name"]
            if grp.get("type") != "time":
                report.skip("dimension_group", f"{name}.{gname}", f"type: {grp.get('type')} (durations unsupported)")
                continue
            try:
                sql = _sql(local[gname], name, {k: v for k, v in local.items() if k != gname})
            except _Unsupported as exc:
                report.skip("dimension_group", f"{name}.{gname}", str(exc))
                continue
            frames = grp.get("timeframes") or ["date", "week", "month", "quarter", "year"]
            grain = "day" if {"raw", "time", "date"} & set(frames) else next(
                (g for g in _GRAIN if g in frames or g + "s" in frames), "day")
            local[gname] = sql
            entry = {"name": gname, "type": "time", "type_params": {"time_granularity": grain}}
            if sql != gname:
                entry["expr"] = sql
            sm["dimensions"].append(entry)
            times[name].append(gname)
        models[name] = sm
        report.converted.append(f"view:{name}")

    for view in data["views"]:
        name = view["name"]
        if name not in models:
            continue
        for m in view.get("measures", []):
            mname, mtype = m["name"], m.get("type")
            if any(k.startswith("filters") or k == "filter" for k in m):
                report.skip("measure", f"{name}.{mname}", "filtered measure")
                continue
            if mtype not in _MEASURES:
                report.skip("measure", f"{name}.{mname}", f"type: {mtype} (table calc / derived measure)")
                continue
            if not times[name]:
                report.skip("measure", f"{name}.{mname}", "view has no date/time dimension for aggregation")
                continue
            try:
                if mtype == "count" and "sql" not in m:
                    sql, agg = "1", "sum"
                else:
                    sql = _sql(m.get("sql", "1"), name, exprs[name])
                    agg = _MEASURES[mtype]
            except _Unsupported as exc:
                report.skip("measure", f"{name}.{mname}", str(exc))
                continue
            reserved = {e["name"] for m in models.values() for e in m["entities"]} | {
                d["name"] for m in models.values() for d in m["dimensions"]
            } | set(models)
            measure_name = f"{mname}_measure" if mname in reserved else mname
            models[name]["measures"].append({"name": measure_name, "agg": agg, "expr": sql})
            if "defaults" not in models[name]:
                models[name]["defaults"] = {"agg_time_dimension": times[name][0]}
            metrics.append({"name": mname, "type": "simple", "type_params": {"measure": measure_name}})
            report.converted.append(f"measure:{name}.{mname}")

    for explore in data["explores"]:
        for key in ("sql_always_where", "access_filters", "always_filter", "table_calculations"):
            if key in explore:
                report.skip("explore", explore["name"], f"{key} not applied")
        base = explore.get("from", explore["name"])
        for join in explore.get("joins", []):
            target = join.get("from", join["name"])
            relationship = join.get("relationship", "many_to_one")
            where = f"{explore['name']}->{join['name']}"
            if base not in models or target not in models:
                report.skip("join", where, "references an unconverted view")
                continue
            if relationship not in ("many_to_one", "one_to_one"):
                report.skip("join", where, f"relationship: {relationship}")
                continue
            match = _JOIN_ON.match(join.get("sql_on", ""))
            if not match:
                report.skip("join", where, "sql_on is not a simple ${a.x} = ${b.y} equality")
                continue
            sides = {match.group(1): match.group(2), match.group(3): match.group(4)}
            names = {explore["name"]: base, join["name"]: target}
            fk = sides.get(explore["name"])
            pk = sides.get(join["name"])
            if fk is None or pk is None:
                report.skip("join", where, "sql_on does not reference both sides")
                continue
            dest = models[target]
            if not any(e["type"] == "primary" for e in dest["entities"]):
                dest["entities"].append({"name": target, "type": "primary", "expr": exprs[target].get(pk, pk)})
            models[base]["entities"].append(
                {"name": target, "type": "foreign", "expr": exprs[base].get(fk, fk)}
            )
            report.converted.append(f"join:{where}")

    docs = []
    for sm in models.values():
        keys = {e["name"] for e in sm["entities"]}
        sm["dimensions"] = [d for d in sm["dimensions"] if d["name"] not in keys]
        if not any(e["type"] == "primary" for e in sm["entities"]):
            sm["primary_entity"] = sm["name"]
        docs.append({"semantic_model": {k: v for k, v in sm.items() if v not in ([], None)}})
    docs.extend({"metric": m} for m in metrics)
    docs.append(project_configuration(time_spine))
    return Conversion(docs, report)
