// Aurora Community — a tiny hosted feed for shared findings.
//
// One Cloudflare Worker + one KV namespace. The desktop app POSTs a shared
// finding to /share; the app's Community tab and the marketing landing page
// both read /feed. This is the single source of truth that makes "community"
// actually communal — and the bot-proof usage signal (count of shares +
// unique sharers) that clones/views can't give you.
//
// Routes:
//   GET    /             -> service info / health
//   GET    /feed?limit=N -> newest shared findings (default 30, max 100)
//   POST   /share        -> submit {title, detail?, method?, dataset?, severity?, run_id?, confidence?}
//   POST   /ping         -> OPT-IN count only: {kind: "diagnostics"|"update-check", v?, os?}
//                           No IP, no id, no payload beyond those three fields. The
//                           desktop only calls this when the user flips the
//                           default-OFF toggle (or clicks "Check for updates").
//   GET    /stats        -> the same aggregates we see, public. Radical transparency:
//                           if we count it, anyone can read it.
//   GET    /latest       -> newest GitHub release (cached 10 min) — the update
//                           check's data source, counted as an update-check ping.
//   DELETE /finding/:id  -> admin takedown (Authorization: Bearer <ADMIN_TOKEN>)
//
// Storage: one KV entry per finding under key  f:<invertedTs>:<rand>  so a
// prefix list returns newest-first with no server-side sort. Each entry
// self-expires after 90 days, so the feed prunes itself.
//
// Privacy: only the fields above are stored. The desktop only ever sends the
// finding text + cited method + dataset NAME — never the raw rows.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};
const TTL_SECONDS = 90 * 24 * 60 * 60;   // findings self-prune after 90 days
const RATE_LIMIT  = 20;                   // shares per IP per minute
const CAPS = { title: 300, detail: 1000, method: 200, dataset: 200, run_id: 160 };
const SEVERITIES = new Set(["crit", "warn", "info"]);
// Tiny starter denylist — obvious abuse only. Expand or swap for a real
// moderation pass later.
const BANNED = ["nigger", "faggot", "cunt", "kike", "retard"];

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS, ...extra },
  });
}
const clamp = (s, n) => String(s == null ? "" : s).slice(0, n);
// Strip control chars (keep \t \n \r), then trim.
const clean = (s) => clamp(s, 4000).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "").trim();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    if (path === "/" && request.method === "GET") {
      return json({ ok: true, service: "aurora-community", routes: ["/feed", "/share"] });
    }

    // ---- READ: GET /feed ----
    if (path === "/feed" && request.method === "GET") {
      let limit = parseInt(url.searchParams.get("limit") || "30", 10);
      if (!Number.isFinite(limit)) limit = 30;
      limit = Math.max(1, Math.min(100, limit));
      const list = await env.FINDINGS.list({ prefix: "f:", limit });
      const items = [];
      for (const k of list.keys) {
        const v = await env.FINDINGS.get(k.name, "json");
        if (v && !v.hidden) items.push(v);
      }
      return json({ ok: true, count: items.length, items }, 200,
        { "Cache-Control": "public, max-age=30" });
    }

    // ---- WRITE: POST /share ----
    if (path === "/share" && request.method === "POST") {
      const ip = request.headers.get("CF-Connecting-IP") || "anon";
      const rlKey = "rl:" + ip;
      const count = parseInt((await env.FINDINGS.get(rlKey)) || "0", 10);
      if (count >= RATE_LIMIT) return json({ ok: false, error: "rate limited — slow down" }, 429);

      let body;
      try { body = await request.json(); } catch { return json({ ok: false, error: "invalid json" }, 400); }
      const title = clean(body.title);
      if (!title) return json({ ok: false, error: "title required" }, 400);

      const blob = (title + " " + clean(body.detail)).toLowerCase();
      if (BANNED.some((w) => blob.includes(w))) return json({ ok: false, error: "rejected by content filter" }, 422);

      const sev = String(body.severity || "info").toLowerCase();
      const now = Date.now();
      const rand = Math.random().toString(36).slice(2, 8);
      const id = (1e15 - now).toString().padStart(16, "0") + ":" + rand;
      const rec = {
        id,
        ts: new Date(now).toISOString(),
        title: clamp(title, CAPS.title),
        detail: clamp(clean(body.detail), CAPS.detail),
        method: clamp(clean(body.method), CAPS.method),
        dataset: clamp(clean(body.dataset), CAPS.dataset),
        severity: SEVERITIES.has(sev) ? sev : "info",
        run_id: clamp(clean(body.run_id), CAPS.run_id),
        confidence: typeof body.confidence === "number" ? body.confidence : null,
        hidden: false,
      };
      await env.FINDINGS.put("f:" + id, JSON.stringify(rec), { expirationTtl: TTL_SECONDS });
      await env.FINDINGS.put(rlKey, String(count + 1), { expirationTtl: 60 });
      return json({ ok: true, id, shared_at: rec.ts });
    }

    // ---- OPT-IN COUNT: POST /ping ----
    if (path === "/ping" && request.method === "POST") {
      let body;
      try { body = await request.json(); } catch { body = {}; }
      const kind = body.kind === "update-check" ? "update-check" : "diagnostics";
      const v = clamp(clean(body.v), 40) || "unknown";
      const os = clamp(clean(body.os), 30) || "unknown";
      const day = new Date().toISOString().slice(0, 10);
      const key = "p:" + day;
      const cur = (await env.FINDINGS.get(key, "json")) || { n: 0, kinds: {}, versions: {}, os: {} };
      cur.n += 1;
      cur.kinds[kind] = (cur.kinds[kind] || 0) + 1;
      cur.versions[v] = (cur.versions[v] || 0) + 1;
      cur.os[os] = (cur.os[os] || 0) + 1;
      await env.FINDINGS.put(key, JSON.stringify(cur), { expirationTtl: 400 * 24 * 3600 });
      return json({ ok: true });
    }

    // ---- PUBLIC AGGREGATES: GET /stats ----
    if (path === "/stats" && request.method === "GET") {
      const days = [];
      const now = Date.now();
      for (let i = 0; i < 30; i++) {
        const day = new Date(now - i * 86400000).toISOString().slice(0, 10);
        const rec = await env.FINDINGS.get("p:" + day, "json");
        if (rec) days.push({ day, ...rec });
      }
      const shares = await env.FINDINGS.list({ prefix: "f:", limit: 1000 });
      return json({
        ok: true,
        note: "everything Aurora counts, public — opt-in pings and feed shares only; no IPs, no ids, no payloads",
        shares_on_feed: shares.keys.length,
        pings_by_day: days,
      }, 200, { "Cache-Control": "public, max-age=300" });
    }

    // ---- UPDATE CHECK: GET /latest ----
    if (path === "/latest" && request.method === "GET") {
      let rel = await env.FINDINGS.get("gh:latest", "json");
      if (!rel) {
        const r = await fetch("https://api.github.com/repos/FantasyLab-ai/aurora/releases/latest", {
          headers: { "User-Agent": "aurora-community-worker", "Accept": "application/vnd.github+json" },
        });
        if (r.ok) {
          const j = await r.json();
          rel = { tag: j.tag_name, url: j.html_url, published_at: j.published_at };
          await env.FINDINGS.put("gh:latest", JSON.stringify(rel), { expirationTtl: 600 });
        }
      }
      // an explicit user click — counted as an update-check ping, same public bucket
      const day = new Date().toISOString().slice(0, 10);
      const key = "p:" + day;
      const cur = (await env.FINDINGS.get(key, "json")) || { n: 0, kinds: {}, versions: {}, os: {} };
      cur.n += 1;
      cur.kinds["update-check"] = (cur.kinds["update-check"] || 0) + 1;
      await env.FINDINGS.put(key, JSON.stringify(cur), { expirationTtl: 400 * 24 * 3600 });
      return json(rel ? { ok: true, ...rel } : { ok: false, error: "release info unavailable" }, rel ? 200 : 502);
    }

    // ---- ADMIN: DELETE /finding/:id (takedown) ----
    if (path.startsWith("/finding/") && request.method === "DELETE") {
      const auth = request.headers.get("Authorization") || "";
      if (!env.ADMIN_TOKEN || auth !== "Bearer " + env.ADMIN_TOKEN) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      const id = decodeURIComponent(path.slice("/finding/".length));
      await env.FINDINGS.delete("f:" + id);
      return json({ ok: true, deleted: id });
    }

    return json({ ok: false, error: "not found" }, 404);
  },
};
