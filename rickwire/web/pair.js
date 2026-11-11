"use strict";
const $ = (id) => document.getElementById(id);
const code = $("code"), msg = $("msg"), nameEl = $("name");

try { if (localStorage.getItem("rw_still") === "1") document.body.classList.add("still"); } catch {}
const guess = /iPhone/.test(navigator.userAgent) ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android phone" : "Browser";
nameEl.value = guess;
const m = /^(\d{6})(?:\.([A-Za-z0-9_-]{16,}))?$/.exec(location.hash.slice(1));
const fromHash = m ? m[1] : "", pairKey = m && m[2] ? m[2] : "";
if (fromHash) code.value = fromHash;
history.replaceState(null, "", location.pathname);  // keep the code out of history

function say(text, cls = "") { msg.textContent = text; msg.className = "hint " + cls; }

async function post(path, body) {
  const r = await fetch(path, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-RickWire": "1" }, body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.detail || r.statusText); e.needCode = d.need_code; throw e; }
  return d;
}

async function pair() {
  say("Opening portal…");
  try {
    await post("/api/pair/complete", { code: code.value.trim(), name: nameEl.value.trim() || guess, key: pairKey });
    say("Paired! Wubba lubba dub dub!", "ok-text");
    setTimeout(() => location.replace("/"), 700);
  } catch (e) { say(e.message, "bad-text"); }
}

async function signIn(e) {
  e.preventDefault();
  $("signIn").disabled = true;
  say("Checking…");
  try {
    await post("/api/auth/login", { username: $("user").value, password: $("pass").value, code: $("otp").value.trim() });
    say("Signed in. Wubba lubba dub dub!", "ok-text");
    setTimeout(() => location.replace(sessionStorage.getItem("rw_after_login") || "/"), 500);
  } catch (err) {
    if (err.needCode) { $("otp").classList.remove("hidden"); $("otp").value = ""; $("otp").focus(); }
    say(err.message, "bad-text");
  } finally { $("signIn").disabled = false; }
}

async function boot() {
  let st = { login: false, pairing: true };
  try { st = await (await fetch("/api/auth/status", { credentials: "same-origin" })).json(); } catch {}
  $("pairBox").classList.toggle("hidden", !st.pairing);
  $("loginBox").classList.toggle("hidden", !st.login);
  $("orLine").classList.toggle("hidden", !(st.pairing && st.login));
  if (st.totp) $("otp").classList.remove("hidden");
  document.title = st.pairing ? (st.login ? "Pair or sign in · RickWire" : "Pair · RickWire") : "Sign in · RickWire";
  if (!st.pairing && !st.login) say("Remote access isn't set up. Turn it on in RickWire's Settings on your PC.", "bad-text");
  if (pairKey) { $("pairBox").classList.remove("hidden"); pair(); }   // QR from the PC: pairs at home or away
  else if (st.pairing && fromHash.length === 6) pair();
  else if (!st.pairing) $("user").focus();
}

$("go").onclick = pair;
$("loginBox").onsubmit = signIn;
code.addEventListener("input", () => { if (code.value.replace(/\D/g, "").length === 6 && fromHash.length !== 6) pair(); });
boot();
