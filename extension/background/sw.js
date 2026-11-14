// Realty Check service worker: talks to the local checker (content scripts can't reliably reach localhost
// from https pages) and relays its server-sent Events back to the tab over a port. Holds no keys.
const DEFAULT_SERVER = "http://127.0.0.1:8787";
const server = async () => {
  const r = await chrome.storage.local.get("rcServer");
  return String((r && r.rcServer) || DEFAULT_SERVER).replace(/\/+$/, "");
};

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "rc-run") return;
  const ctrl = new AbortController();
  let alive = true;
  port.onDisconnect.addListener(() => { alive = false; ctrl.abort(); });
  const post = (m) => { if (alive) try { port.postMessage(m); } catch (e) { alive = false; } };
  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "check") return;
    let res;
    try {
      res = await fetch((await server()) + "/check", {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(msg.snapshot), signal: ctrl.signal,
      });
    } catch (e) { post({ type: "unreachable" }); return; }
    if (!res.ok || !res.body) { post({ type: "unreachable", status: res.status }); return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const flush = (chunk) => {
      const data = chunk.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
      if (!data) return;
      try { post({ type: "event", event: JSON.parse(data) }); } catch (e) { /* skip a malformed line */ }
    };
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, "\n");
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) { flush(buf.slice(0, i)); buf = buf.slice(i + 2); }
      }
      if (buf.trim()) flush(buf);
    } catch (e) { /* aborted or dropped */ }
    post({ type: "end" });
  });
});

const toDataUrl = async (res) => {
  const type = res.headers.get("content-type") || "image/png";
  const bytes = new Uint8Array(await res.arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return `data:${type};base64,${btoa(s)}`;
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "openReport") {
    // report_url may be a path ("/report/r1") or already absolute ("http://127.0.0.1:8787/report/r1").
    server().then((s) => {
      const r = String(msg.report_url || "/");
      chrome.tabs.create({ url: /^https?:\/\//.test(r) ? r : s + r, index: sender.tab ? sender.tab.index + 1 : undefined });
    });
  } else if (msg.type === "openSampleReport") {
    const url = chrome.runtime.getURL("report/report.html") + "?sample=1&address=" + encodeURIComponent(msg.address || "")
      + (/^#[A-Za-z0-9_%-]{1,80}$/.test(String(msg.hash || "")) ? msg.hash : ""); // #claim-<id>, #mismatch, #extra
    chrome.tabs.create({ url, index: sender.tab ? sender.tab.index + 1 : undefined });
  } else if (msg.type === "img") {
    // Proof images: fetched here and handed back as data URLs, so the host page never loads from localhost.
    (async () => {
      try {
        const p = String(msg.path || "");
        const s = await server();
        const url = p.startsWith("/") ? s + p : p.startsWith(s + "/") ? p : null; // only the checker's own images
        if (!url) return reply({});
        const res = await fetch(url);
        reply(res.ok ? { dataUrl: await toDataUrl(res) } : {});
      } catch (e) { reply({}); }
    })();
    return true;
  }
  return false;
});
