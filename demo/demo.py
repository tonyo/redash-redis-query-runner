#!/usr/bin/env python3
"""Idempotent Redash demo: data source, saved queries, bar chart, dashboard.

Stdlib only. Run via `mise run demo`. Reads ADMIN_PASSWORD from ./.env (never printed).
Env overrides: REDASH_URL (default http://127.0.0.1:5000), ADMIN_EMAIL.
"""

from redash_api import BASE, api, check, login, run_result

DS_NAME = "Redis demo"
DASH_NAME = "Redis demo"
PREFIX = "Redis demo: "


# ---- data source ---------------------------------------------------------


def ensure_data_source(name=None, extra=None):
    name = name or DS_NAME
    opts = {"host": "data-redis", "port": 6379, "db": 0} | (extra or {})
    found = next((d for d in api("GET", "/api/data_sources") if d["name"] == name), None)
    if found:
        ds = api(
            "POST",
            f"/api/data_sources/{found['id']}",
            {"name": name, "type": "redis", "options": opts},
        )
        print(f"  data source '{name}' exists (id {ds['id']})")
    else:
        ds = api("POST", "/api/data_sources", {"name": name, "type": "redis", "options": opts})
        print(f"  created data source '{name}' (id {ds['id']})")
    check(ds["type"] == "redis", "data source type is redis")
    res = api("POST", f"/api/data_sources/{ds['id']}/test")
    check(res and res.get("ok") is True, "data source connection test passes")
    return ds["id"]


# ---- queries -------------------------------------------------------------


def ensure_query(ds_id, name, text, description=""):
    existing = {q["name"]: q for q in api("GET", "/api/queries?page_size=250")["results"]}
    if name in existing:
        q = api(
            "POST",
            f"/api/queries/{existing[name]['id']}",
            {"query": text, "data_source_id": ds_id, "description": description},
        )
        print(f"  query '{name}' exists (id {q['id']})")
    else:
        q = api(
            "POST",
            "/api/queries",
            {
                "name": name,
                "query": text,
                "data_source_id": ds_id,
                "description": description,
                "options": {},
            },
        )
        print(f"  created query '{name}' (id {q['id']})")
    return api("GET", f"/api/queries/{q['id']}")


def execute(query):
    data, err = run_result(f"/api/queries/{query['id']}/results", {"max_age": 0})
    check(err is None, f"'{query['name']}' executes without error")
    return data


def types(data):
    return {c["name"]: c["type"] for c in data["columns"]}


# ---- visualizations / dashboard -----------------------------------------

CHART_OPTIONS = {
    "globalSeriesType": "column",
    "sortX": False,  # keep result order (ascending score); True sorts by region name
    "legend": {"enabled": False},
    "xAxis": {"type": "-", "labels": {"enabled": True}, "title": {"text": "Region"}},
    "yAxis": [
        {"type": "linear", "title": {"text": "Revenue"}},
        {"type": "linear", "opposite": True},
    ],
    "series": {"stacking": None, "error_y": {"type": "data", "visible": True}},
    "seriesOptions": {"score": {"type": "column", "yAxis": 0, "zIndex": 0, "index": 0}},
    "columnMapping": {"member": "x", "score": "y"},
    "valuesOptions": {},
    "showDataLabels": True,
    "numberFormat": "0,0[.]00",
    "textFormat": "",
    "direction": {"type": "counterclockwise"},
    "missingValuesAsZero": True,
}


def ensure_chart(query):
    name = "Revenue by region"
    vis = next((v for v in query["visualizations"] if v["name"] == name), None)
    body = {
        "query_id": query["id"],
        "type": "CHART",
        "name": name,
        "description": "",
        "options": CHART_OPTIONS,
    }
    if vis:
        vis = api("POST", f"/api/visualizations/{vis['id']}", body)
        print(f"  chart '{name}' exists (id {vis['id']})")
    else:
        vis = api("POST", "/api/visualizations", body)
        print(f"  created chart '{name}' (id {vis['id']})")
    return vis["id"]


def table_vis(query):
    v = next((v for v in query["visualizations"] if v["type"] == "TABLE"), None)
    check(v, f"'{query['name']}' has a default table visualization")
    return v["id"]


def ensure_dashboard():
    found = next(
        (
            d
            for d in api("GET", "/api/dashboards?page_size=250")["results"]
            if d["name"] == DASH_NAME
        ),
        None,
    )
    if not found:
        found = api("POST", "/api/dashboards", {"name": DASH_NAME})
        print(f"  created dashboard '{DASH_NAME}'")
    else:
        print(f"  dashboard '{DASH_NAME}' exists")
    return api("GET", f"/api/dashboards/{found['id']}")


def ensure_widget(dash, vis_id, pos):
    present = {w["visualization"]["id"] for w in dash["widgets"] if w.get("visualization")}
    if vis_id in present:
        return False
    api(
        "POST",
        "/api/widgets",
        {
            "dashboard_id": dash["id"],
            "visualization_id": vis_id,
            "text": "",
            "width": 1,
            "options": {
                "position": {"col": pos[0], "row": pos[1], "sizeX": pos[2], "sizeY": pos[3]}
            },
        },
    )
    return True


def main():
    print("Login")
    login()

    print("Data source")
    ds_id = ensure_data_source()

    # the seeded ACL user (seed/seed.redis) can read but not write
    ro_id = ensure_data_source(
        DS_NAME + " (read-only)", {"username": "readonly", "password": "readonly"}
    )

    print("Queries")
    q_zset = ensure_query(
        ds_id, PREFIX + "revenue by region", "ZRANGE sales:revenue_by_region 0 -1 WITHSCORES"
    )
    q_hash = ensure_query(ds_id, PREFIX + "product 1001", "HGETALL product:1001")
    q_list = ensure_query(ds_id, PREFIX + "recent orders", "LRANGE recent:orders 0 -1")
    q_strm = ensure_query(ds_id, PREFIX + "events", "XRANGE events - +")
    q_info = ensure_query(ds_id, PREFIX + "server info", "INFO server")

    print("Execute and assert column types")
    d = execute(q_zset)
    t = types(d)
    check(t.get("member") == "string", f"zset member column is string (got {t.get('member')})")
    check(
        t.get("score") in ("float", "integer"),
        f"zset score column is numeric (got {t.get('score')})",
    )
    scores = [r["score"] for r in d["rows"]]
    check(
        all(isinstance(s, (int, float)) and not isinstance(s, bool) for s in scores),
        "score values are JSON numbers",
    )
    check(
        len(scores) == 6 and scores == sorted(scores),
        f"scores come back in numeric ascending order ({len(scores)} rows)",
    )
    check(
        sorted(scores, key=str) != scores,
        "numeric order differs from lexical order (so types matter)",
    )
    check(sorted(scores, reverse=True)[0] == 12000, "largest score is 12000")

    d = execute(q_hash)
    t = types(d)
    cols = list(t)
    check(d["rows"], "hash query returns a row")
    check("stock" in t and "price" in t, f"hash has stock and price columns (columns: {cols})")
    check(t["stock"] == "integer", f"stock is integer (got {t['stock']})")
    check(t["price"] == "float", f"price is float (got {t['price']})")
    check(
        d["rows"][0]["stock"] == 42 and d["rows"][0]["price"] == 249.99,
        "stock=42 and price=249.99 as numbers",
    )

    d = execute(q_list)
    check(len(d["rows"]) == 3, "list query returns 3 rows")
    d = execute(q_strm)
    check(len(d["rows"]) == 3, "stream query returns 3 entries")
    d = execute(q_info)
    check(len(d["rows"]) > 0, "INFO server returns rows")

    print("Failing query")
    _, err = run_result(
        "/api/query_results", {"data_source_id": ds_id, "query": "NOSUCHCMD", "max_age": 0}
    )
    check(
        err and "Redis error" in err and "unknown command" in err.lower(),
        "NOSUCHCMD surfaces the Redis error text",
    )

    d = run_result("/api/query_results", {"data_source_id": ro_id, "query": "DBSIZE", "max_age": 0})
    check(d[0] is not None, "read-only user can read")
    _, err = run_result(
        "/api/query_results", {"data_source_id": ro_id, "query": "SET demo:ro 1", "max_age": 0}
    )
    check(err and "no permissions" in err, "read-only user cannot write")

    print("Visualizations")
    chart_id = ensure_chart(q_zset)

    print("Dashboard")
    dash = ensure_dashboard()
    layout = [
        (chart_id, (0, 0, 6, 8)),
        (table_vis(q_hash), (0, 8, 3, 6)),
        (table_vis(q_list), (3, 8, 3, 6)),
        (table_vis(q_strm), (0, 14, 6, 7)),
        (table_vis(q_info), (0, 21, 6, 10)),
    ]
    added = sum(ensure_widget(dash, v, pos) for v, pos in layout)
    print(f"  added {added} widget(s)")
    if dash.get("is_draft"):
        api("POST", f"/api/dashboards/{dash['id']}", {"is_draft": False})
    dash = api("GET", f"/api/dashboards/{dash['id']}")
    check(not dash["is_draft"], "dashboard is published")
    vis_ids = [w["visualization"]["id"] for w in dash["widgets"] if w.get("visualization")]
    check(
        len(vis_ids) == len(set(vis_ids)) == len(layout),
        f"dashboard has {len(layout)} widgets, no duplicates",
    )
    check(chart_id in vis_ids, "dashboard contains the bar chart")

    print(f"\nDashboard: {BASE}/dashboards/{dash['id']}-{dash['slug']}")


if __name__ == "__main__":
    main()
