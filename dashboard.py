"""Analytics dashboard — stores MCP client events in Postgres and serves a dashboard UI."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("molttravel.dashboard")

DASHBOARD_KEY = os.environ.get("DASHBOARD_KEY", "")
DATABASE_URL = os.environ.get("ANALYTICS_DATABASE_URL", "")

_pool = None


async def get_pool():
    global _pool
    if _pool is None and DATABASE_URL:
        import asyncpg
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
        await _init_schema()
    return _pool


async def _init_schema():
    pool = _pool
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS client_events (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                event_type TEXT NOT NULL,
                client_ip TEXT,
                client_name TEXT,
                client_version TEXT,
                user_agent TEXT,
                session_id TEXT,
                tool_name TEXT,
                tool_args JSONB,
                protocol_version TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON client_events (ts DESC);
            CREATE INDEX IF NOT EXISTS idx_events_client ON client_events (client_name);
            CREATE INDEX IF NOT EXISTS idx_events_type ON client_events (event_type);
        """)
    log.info("Analytics schema initialized")


async def record_event(
    event_type: str,
    client_ip: str = "",
    client_name: str = "",
    client_version: str = "",
    user_agent: str = "",
    session_id: str = "",
    tool_name: str = "",
    tool_args: dict | None = None,
    protocol_version: str = "",
):
    pool = await get_pool()
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO client_events
                   (event_type, client_ip, client_name, client_version,
                    user_agent, session_id, tool_name, tool_args, protocol_version)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                event_type, client_ip, client_name, client_version,
                user_agent, session_id, tool_name,
                json.dumps(tool_args) if tool_args else None,
                protocol_version,
            )
    except Exception as e:
        log.warning("Failed to record event: %s", e)


async def query_events(days: int = 7, limit: int = 200) -> list[dict]:
    pool = await get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, ts, event_type, client_ip, client_name, client_version,
                      user_agent, session_id, tool_name, tool_args
               FROM client_events
               WHERE ts > NOW() - INTERVAL '1 day' * $1
               ORDER BY ts DESC LIMIT $2""",
            days, limit,
        )
        return [
            {
                "id": r["id"],
                "ts": r["ts"].isoformat(),
                "event_type": r["event_type"],
                "client_ip": r["client_ip"],
                "client_name": r["client_name"],
                "client_version": r["client_version"],
                "user_agent": r["user_agent"],
                "session_id": r["session_id"],
                "tool_name": r["tool_name"],
                "tool_args": json.loads(r["tool_args"]) if r["tool_args"] else None,
            }
            for r in rows
        ]


async def query_stats(days: int = 7) -> dict:
    pool = await get_pool()
    if not pool:
        return {}
    async with pool.acquire() as conn:
        interval = f"{days} days"

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM client_events WHERE ts > NOW() - $1::interval", interval
        )
        sessions = await conn.fetchval(
            "SELECT COUNT(*) FROM client_events WHERE event_type='initialize' AND ts > NOW() - $1::interval", interval
        )
        tool_calls = await conn.fetchval(
            "SELECT COUNT(*) FROM client_events WHERE event_type='tool_call' AND ts > NOW() - $1::interval", interval
        )

        by_client = await conn.fetch(
            """SELECT client_name, COUNT(*) as cnt
               FROM client_events WHERE event_type='initialize' AND ts > NOW() - $1::interval
               GROUP BY client_name ORDER BY cnt DESC LIMIT 20""", interval
        )
        by_tool = await conn.fetch(
            """SELECT tool_name, COUNT(*) as cnt
               FROM client_events WHERE event_type='tool_call' AND ts > NOW() - $1::interval
               GROUP BY tool_name ORDER BY cnt DESC LIMIT 20""", interval
        )
        by_day = await conn.fetch(
            """SELECT DATE(ts) as day, COUNT(*) FILTER (WHERE event_type='initialize') as sessions,
                      COUNT(*) FILTER (WHERE event_type='tool_call') as tool_calls
               FROM client_events WHERE ts > NOW() - $1::interval
               GROUP BY DATE(ts) ORDER BY day""", interval
        )
        unique_ips = await conn.fetchval(
            """SELECT COUNT(DISTINCT client_ip)
               FROM client_events WHERE event_type='initialize' AND ts > NOW() - $1::interval""", interval
        )

        return {
            "total_events": total,
            "sessions": sessions,
            "tool_calls": tool_calls,
            "unique_ips": unique_ips,
            "by_client": [{"name": r["client_name"] or "unknown", "count": r["cnt"]} for r in by_client],
            "by_tool": [{"name": r["tool_name"] or "?", "count": r["cnt"]} for r in by_tool],
            "by_day": [{"day": str(r["day"]), "sessions": r["sessions"], "tool_calls": r["tool_calls"]} for r in by_day],
        }


def _check_auth(headers: dict) -> bool:
    """Check dashboard auth from query string or Authorization header."""
    if not DASHBOARD_KEY:
        return True  # no key set = open (for dev)
    return False  # auth is checked via query param in the ASGI handler


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>moltravel analytics</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { background: #0a0a0a; color: #e5e5e5; font-family: ui-monospace, monospace; }
  .card { background: #141414; border: 1px solid #262626; border-radius: 12px; }
  .stat-value { font-size: 2rem; font-weight: 700; color: #fff; }
  .stat-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #737373; }
  table { width: 100%; }
  th { text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: #737373; padding: 8px 12px; border-bottom: 1px solid #262626; }
  td { padding: 8px 12px; border-bottom: 1px solid #1a1a1a; font-size: 0.85rem; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 9999px; font-size: 0.7rem; font-weight: 600; }
  .badge-init { background: #1e3a5f; color: #60a5fa; }
  .badge-tool { background: #1a3a2a; color: #4ade80; }
  .badge-list { background: #3a2a1a; color: #fbbf24; }
  .refresh-dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; display: inline-block; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
</style>
</head>
<body class="p-6 max-w-6xl mx-auto">
  <div class="flex items-center justify-between mb-8">
    <div>
      <h1 class="text-xl font-bold text-white">moltravel analytics</h1>
      <p class="text-xs text-neutral-500 mt-1">MCP server client tracking</p>
    </div>
    <div class="flex items-center gap-3">
      <select id="days" class="bg-neutral-800 border border-neutral-700 rounded px-3 py-1.5 text-sm" onchange="loadAll()">
        <option value="1">Last 24h</option>
        <option value="7" selected>Last 7 days</option>
        <option value="30">Last 30 days</option>
      </select>
      <span class="refresh-dot"></span>
      <span class="text-xs text-neutral-500" id="lastRefresh"></span>
    </div>
  </div>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div class="card p-4"><div class="stat-value" id="statSessions">-</div><div class="stat-label">Sessions</div></div>
    <div class="card p-4"><div class="stat-value" id="statToolCalls">-</div><div class="stat-label">Tool Calls</div></div>
    <div class="card p-4"><div class="stat-value" id="statUniqueIPs">-</div><div class="stat-label">Unique IPs</div></div>
    <div class="card p-4"><div class="stat-value" id="statTotal">-</div><div class="stat-label">Total Events</div></div>
  </div>

  <div class="grid md:grid-cols-2 gap-4 mb-6">
    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-3 text-neutral-400">Activity</h3>
      <canvas id="activityChart" height="160"></canvas>
    </div>
    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-3 text-neutral-400">Clients</h3>
      <canvas id="clientChart" height="160"></canvas>
    </div>
  </div>

  <div class="grid md:grid-cols-2 gap-4 mb-6">
    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-3 text-neutral-400">Top Tools</h3>
      <div id="toolList"></div>
    </div>
    <div class="card p-4">
      <h3 class="text-sm font-semibold mb-3 text-neutral-400">Top Clients</h3>
      <div id="clientList"></div>
    </div>
  </div>

  <div class="card p-4">
    <h3 class="text-sm font-semibold mb-3 text-neutral-400">Recent Events</h3>
    <div class="overflow-x-auto">
      <table>
        <thead><tr><th>Time</th><th>Type</th><th>Client</th><th>IP</th><th>Tool</th><th>Args</th></tr></thead>
        <tbody id="eventsTable"></tbody>
      </table>
    </div>
  </div>

<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
let activityChart, clientChart;

async function api(path) {
  const days = document.getElementById('days').value;
  const res = await fetch(`/analytics/api/${path}?days=${days}&key=${KEY}`);
  if (!res.ok) { document.body.innerHTML = '<div class="p-12 text-center text-red-400 text-lg">Unauthorized</div>'; throw new Error('unauthorized'); }
  return res.json();
}

function timeAgo(iso) {
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400) return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}

function badgeClass(type) {
  if (type === 'initialize') return 'badge-init';
  if (type === 'tool_call') return 'badge-tool';
  return 'badge-list';
}

function renderBar(items, maxVal) {
  return items.map(i => `
    <div class="flex items-center gap-2 mb-1.5">
      <span class="text-xs w-40 truncate">${i.name}</span>
      <div class="flex-1 h-4 bg-neutral-800 rounded overflow-hidden">
        <div class="h-full bg-blue-600 rounded" style="width:${Math.max(2, i.count/maxVal*100)}%"></div>
      </div>
      <span class="text-xs text-neutral-500 w-8 text-right">${i.count}</span>
    </div>
  `).join('');
}

async function loadStats() {
  const s = await api('stats');
  document.getElementById('statSessions').textContent = s.sessions;
  document.getElementById('statToolCalls').textContent = s.tool_calls;
  document.getElementById('statUniqueIPs').textContent = s.unique_ips;
  document.getElementById('statTotal').textContent = s.total_events;

  const maxTool = Math.max(1, ...s.by_tool.map(t=>t.count));
  document.getElementById('toolList').innerHTML = renderBar(s.by_tool.slice(0,10), maxTool);

  const maxClient = Math.max(1, ...s.by_client.map(c=>c.count));
  document.getElementById('clientList').innerHTML = renderBar(s.by_client.slice(0,10), maxClient);

  // Activity chart
  const labels = s.by_day.map(d => d.day.slice(5));
  const sessData = s.by_day.map(d => d.sessions);
  const toolData = s.by_day.map(d => d.tool_calls);

  if (activityChart) activityChart.destroy();
  activityChart = new Chart(document.getElementById('activityChart'), {
    type: 'bar', data: {
      labels, datasets: [
        { label: 'Sessions', data: sessData, backgroundColor: '#3b82f6' },
        { label: 'Tool Calls', data: toolData, backgroundColor: '#4ade80' },
      ]
    }, options: { responsive: true, plugins: { legend: { labels: { color: '#737373', font: { size: 10 } } } }, scales: { x: { ticks: { color: '#525252' }, grid: { display: false } }, y: { ticks: { color: '#525252' }, grid: { color: '#1a1a1a' } } } }
  });

  // Client pie chart
  if (clientChart) clientChart.destroy();
  const colors = ['#3b82f6','#4ade80','#f59e0b','#ef4444','#a855f7','#06b6d4','#f97316','#ec4899'];
  clientChart = new Chart(document.getElementById('clientChart'), {
    type: 'doughnut', data: {
      labels: s.by_client.map(c=>c.name),
      datasets: [{ data: s.by_client.map(c=>c.count), backgroundColor: colors }]
    }, options: { responsive: true, plugins: { legend: { position: 'right', labels: { color: '#a3a3a3', font: { size: 10 }, padding: 8 } } } }
  });
}

async function loadEvents() {
  const events = await api('events');
  document.getElementById('eventsTable').innerHTML = events.map(e => `
    <tr>
      <td class="text-neutral-500 text-xs whitespace-nowrap">${timeAgo(e.ts)}</td>
      <td><span class="badge ${badgeClass(e.event_type)}">${e.event_type}</span></td>
      <td>${e.client_name || '-'}${e.client_version ? '/'+e.client_version : ''}</td>
      <td class="text-neutral-500 text-xs font-mono">${e.client_ip || '-'}</td>
      <td>${e.tool_name || '-'}</td>
      <td class="text-neutral-600 text-xs max-w-[200px] truncate">${e.tool_args ? JSON.stringify(e.tool_args).slice(0,80) : '-'}</td>
    </tr>
  `).join('');
}

async function loadAll() {
  await Promise.all([loadStats(), loadEvents()]);
  document.getElementById('lastRefresh').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>"""


async def handle_dashboard_request(scope, receive, send):
    """Handle /analytics/* routes."""
    path = scope.get("path", "")
    headers = dict(scope.get("headers", []))
    query = scope.get("query_string", b"").decode()

    # Parse query params
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)

    # Auth check
    if DASHBOARD_KEY and params.get("key") != DASHBOARD_KEY:
        await send({"type": "http.response.start", "status": 401, "headers": [
            [b"content-type", b"text/plain"],
        ]})
        await send({"type": "http.response.body", "body": b"Unauthorized"})
        return

    if path == "/analytics" or path == "/analytics/":
        body = DASHBOARD_HTML.encode()
        await send({"type": "http.response.start", "status": 200, "headers": [
            [b"content-type", b"text/html; charset=utf-8"],
            [b"content-length", str(len(body)).encode()],
        ]})
        await send({"type": "http.response.body", "body": body})

    elif path == "/analytics/api/stats":
        days = int(params.get("days", "7"))
        stats = await query_stats(days=days)
        body = json.dumps(stats).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ]})
        await send({"type": "http.response.body", "body": body})

    elif path == "/analytics/api/events":
        days = int(params.get("days", "7"))
        limit = min(int(params.get("limit", "200")), 500)
        events = await query_events(days=days, limit=limit)
        body = json.dumps(events).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ]})
        await send({"type": "http.response.body", "body": body})

    else:
        await send({"type": "http.response.start", "status": 404, "headers": [
            [b"content-type", b"text/plain"],
        ]})
        await send({"type": "http.response.body", "body": b"Not found"})
