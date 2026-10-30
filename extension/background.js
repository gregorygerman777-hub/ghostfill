importScripts("common.js");

const COLORS = { up: "#2f9e6f", down: "#d4554a", flat: "#4b5d58", off: "#7a5a12" };

async function refreshBadge() {
  try {
    const s = await getJSON("/status");
    const pnl = s.today_pnl_usd ?? 0;
    const text = Math.abs(pnl) < 0.05 ? "0" : compactUsd(pnl);
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color: pnl > 0.05 ? COLORS.up : pnl < -0.05 ? COLORS.down : COLORS.flat });
    await chrome.action.setTitle({
      title: `solpaper (paper only)\nEquity $${s.equity_usd.toFixed(2)}\nToday ${compactUsd(pnl)} USD\n${s.open_positions.length} open position(s)`,
    });
  } catch {
    await chrome.action.setBadgeText({ text: "off" });
    await chrome.action.setBadgeBackgroundColor({ color: COLORS.off });
    await chrome.action.setTitle({ title: "solpaper: local API unreachable" });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("refresh", { periodInMinutes: 1 });
  refreshBadge();
});
chrome.runtime.onStartup.addListener(refreshBadge);
chrome.alarms.onAlarm.addListener((a) => a.name === "refresh" && refreshBadge());
chrome.runtime.onMessage.addListener((msg) => msg === "refresh" && refreshBadge());
