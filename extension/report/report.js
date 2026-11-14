// Full report for a sample run (report.html?sample=1&address=...). Real runs use the local server's own report page.
(() => {
  const VC = { contradicted: "#dc2626", close: "#d97706", confirmed: "#16a34a", cant_check: "#a3a3a3" };
  const VL = { contradicted: "Mismatch", close: "Mismatch", confirmed: "Match", cant_check: "Can't verify" };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const q = new URLSearchParams(location.search);
  const address = q.get("address") || "Sample address";
  const events = globalThis.RC_SAMPLE.events(address);
  const claims = (events.find((e) => e.type === "claims") || {}).claims || [];
  const verdicts = {};
  events.filter((e) => e.type === "verdict").forEach((e) => (verdicts[e.verdict.claim_id] = e.verdict));
  const done = events.find((e) => e.type === "done") || { counts: {}, headline: "" };
  const order = ["contradicted", "close", "confirmed", "cant_check"];
  const sorted = claims.slice().sort((a, b) => order.indexOf((verdicts[a.id] || {}).verdict) - order.indexOf((verdicts[b.id] || {}).verdict));
  document.getElementById("main").innerHTML = `
    <div class="eyebrow">Report</div>
    <div class="addr">${esc(address)}</div>
    <h1 id="headline">${esc(done.headline)}</h1>
    <div class="note">Sample result: the local checker was not running, so this is the built-in example.</div>
    <div class="counts">${[["contradicted", (done.counts.contradicted || 0) + (done.counts.close || 0)], ["confirmed", done.counts.confirmed || 0], ["cant_check", done.counts.cant_check || 0]].map(([k, n]) => `<div class="cnt"><b>${n}</b><span><i class="d" style="background:${VC[k]}"></i>${VL[k]}</span></div>`).join("")}</div>
    <h2>What the listing says</h2>
    ${sorted.map((c) => {
      const v = verdicts[c.id];
      if (!v) return "";
      return `<div class="claim"><i class="d" style="background:${VC[v.verdict]}"></i><div>
        <div class="q">${esc(c.quote)}${c.label ? `<small>${esc(c.label)}</small>` : ""}</div>
        <dl><dt>Listing says</dt><dd>${esc(v.claimed)}</dd><dt>We found</dt><dd>${esc(v.found)}</dd></dl>
        <p class="reason">${esc(v.reason)}</p></div><span class="vl" style="color:${VC[v.verdict]}">${VL[v.verdict]}</span></div>`;
    }).join("")}`;
  document.title = "Reality Check report · " + address;
})();
