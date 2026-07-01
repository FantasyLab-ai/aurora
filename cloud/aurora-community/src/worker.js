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
