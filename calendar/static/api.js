async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: Object.keys(headers).length ? headers : opts.headers,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((x) => x.msg).join("; ") : data.error || res.statusText;
    throw new Error(msg);
  }
  return data;
}

async function apiForm(path, formData, method = "POST") {
  const res = await fetch(path, { method, credentials: "same-origin", body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}

function showError(el, err) {
  if (el) el.textContent = err.message || String(err);
}

function fmtLocal(iso) {
  if (!iso) return "—";
  const d = new Date(iso.length === 16 ? iso + ":00" : iso);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function counter(input, max, el) {
  const n = (input.value || "").length;
  el.textContent = max ? `${n} / ${max}` : String(n);
  el.style.color = max && n > max ? "var(--destructive)" : "var(--muted-foreground)";
}
