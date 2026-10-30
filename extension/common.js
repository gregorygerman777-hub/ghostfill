// Shared by the popup and the background worker. Read-only GET requests to the local API.
const DEFAULT_API = "http://localhost:8000";

async function apiBase() {
  const { api } = await chrome.storage.sync.get({ api: DEFAULT_API });
  return api.replace(/\/+$/, "");
}

async function getJSON(path) {
  const base = await apiBase();
  const r = await fetch(base + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} returned ${r.status}`);
  return r.json();
}

function compactUsd(v) {
  if (v == null) return "";
  const a = Math.abs(v);
  const s = a >= 1000 ? (a / 1000).toFixed(a >= 10000 ? 0 : 1) + "k" : a >= 100 ? a.toFixed(0) : a.toFixed(1);
  return (v < 0 ? "-" : v > 0 ? "+" : "") + s;
}
