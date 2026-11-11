"use strict";
// Android share sheet -> RickWire (Web Share Target). Adds immediately; falls back to the queue page.
(async () => {
  const q = new URLSearchParams(location.search);
  const text = [q.get("url"), q.get("text"), q.get("title")].filter(Boolean).join("\n");
  const msg = document.getElementById("msg");
  history.replaceState(null, "", "/share");
  if (!text) { location.replace("/"); return; }
  try {
    const r = await fetch("/api/jobs", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-RickWire": "1" },
      body: JSON.stringify({ text, mode: "auto" }),
    });
    if (r.status === 401) {   // keep what was shared so it's waiting in the box after pairing / signing in
      try { sessionStorage.setItem("rw_shared", text); } catch {}
      location.replace("/pair.html"); return;
    }
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    msg.textContent = `✓ ${d.length} item${d.length > 1 ? "s" : ""} queued. Wubba lubba dub dub!`;
    setTimeout(() => location.replace("/"), 1500);
  } catch (e) {
    msg.textContent = `Aw jeez: ${e.message}`;
    try { sessionStorage.setItem("rw_shared", text); } catch {}
  }
})();
