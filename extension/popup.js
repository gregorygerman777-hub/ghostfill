const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (v) => v == null ? "n/a" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v) => v == null ? "n/a" : (v > 0 ? "+" : "") + usd(v);
const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "";
const ago = (s) => s == null ? "no data" : s < 90 ? Math.round(s) + "s ago" : Math.round(s / 60) + "m ago";
const short = (m) => m.slice(0, 4) + "..." + m.slice(-4);

function render(s) {
  $("equity").textContent = usd(s.equity_usd);
  for (const [id, v] of [["today", s.today_pnl_usd], ["unrealized", s.unrealized_pnl_usd], ["realized", s.realized_pnl_usd]]) {
    $(id).textContent = signed(v);
    $(id).className = "num " + cls(v);
  }
  const age = s.data_age_seconds;
  $("dot").className = "dot " + (age == null ? "" : age < 120 ? "ok" : "stale");
  $("age").textContent = ago(age);

  $("tokens").innerHTML = (s.tokens || []).map((t) => {
    const sc = t.screen;
    const verdict = !sc ? '<span class="chip">pending</span>' : sc.passed ? '<span class="chip good">eligible</span>' : '<span class="chip crit">blocked</span>';
    const sig = t.signal ? `<span class="chip ${t.signal.target > 0 ? "good" : ""}">${t.signal.target > 0 ? "long" : "flat"}</span>` : '<span class="chip">no bar</span>';
    const why = sc && !sc.passed ? sc.reasons[0] : short(t.mint);
    return `<div class="item"><div><div class="name">${esc(t.label || short(t.mint))}</div><div class="sub" title="${esc(sc ? sc.reasons.join("; ") : t.mint)}">${esc(why)}</div></div>${verdict}${sig}</div>`;
  }).join("") || '<p class="empty">No tokens configured.</p>';

  const pos = s.open_positions || [];
  $("pos-label").textContent = `Open positions (${pos.length})`;
  const label = (m) => ((s.tokens || []).find((t) => t.mint === m) || {}).label || short(m);
  $("positions").innerHTML = pos.map((p) =>
    `<div class="item"><div><div class="name">${esc(label(p.mint))}</div><div class="sub">entry ${p.entry_price.toPrecision(4)} / mark ${p.mark_price.toPrecision(4)}</div></div><span></span><span class="num ${cls(p.unrealized_pnl_usd)}">${signed(p.unrealized_pnl_usd)}</span></div>`
  ).join("") || '<p class="empty">None. Entries need a long signal and a passing screen.</p>';
}

async function load() {
  try {
    render(await getJSON("/status"));
    $("error").hidden = true;
  } catch (e) {
    $("dot").className = "dot down";
    $("age").textContent = "offline";
    $("error").hidden = false;
    $("error").textContent = `Cannot reach ${await apiBase()}. Start the service (docker compose up -d) or change the API address below.`;
  }
  chrome.runtime.sendMessage("refresh");
}

$("open").addEventListener("click", async () => chrome.tabs.create({ url: (await apiBase()) + "/" }));
$("settings").addEventListener("submit", async (e) => {
  e.preventDefault();
  await chrome.storage.sync.set({ api: $("api").value.trim() });
  load();
});
apiBase().then((a) => ($("api").value = a));
load();
setInterval(load, 5000);
