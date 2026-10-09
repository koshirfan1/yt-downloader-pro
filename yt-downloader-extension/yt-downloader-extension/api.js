// App (yt_downloader_pro.py) se baat karne ka code. App sirf 127.0.0.1 par sunti hai.
const PORTS = Array.from({ length: 10 }, (_, i) => 8765 + i);

export async function getToken() {
  const { token = "" } = await chrome.storage.local.get("token");
  return token;
}

export async function setToken(value) {
  await chrome.storage.local.set({ token: value.trim() });
}

async function fetchTimeout(url, options = {}, ms = 1200) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...options, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

// App ka port dhoondta hai (8765-8774) aur yaad rakhta hai
export async function findPort() {
  const { port } = await chrome.storage.local.get("port");
  const order = port ? [port, ...PORTS.filter((p) => p !== port)] : PORTS;
  for (const p of order) {
    try {
      const r = await fetchTimeout(`http://127.0.0.1:${p}/ping`);
      const j = await r.json();
      if (j.app === "yt-downloader-pro") {
        await chrome.storage.local.set({ port: p });
        return p;
      }
    } catch (_) { /* agla port */ }
  }
  return null;
}

async function post(path, body) {
  const port = await findPort();
  if (!port) throw new Error("App nahi mili. Pehle yt_downloader_pro.py chalayein.");
  const token = await getToken();
  if (!token) throw new Error("Token set nahi. App se Copy karke yahan paste karein.");
  const r = await fetchTimeout(
    `http://127.0.0.1:${port}${path}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Token": token },
      body: JSON.stringify(body),
    },
    15000
  );
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `Error ${r.status}`);
  return j;
}

export function sendUrls(urls, options = {}) {
  return post("/add", { urls, ...options });
}

// Usi browser window/profile ke YouTube cookies (incognito window ho to wahan ke)
async function collectYoutubeCookies(tabId) {
  let storeId;
  try {
    const stores = await chrome.cookies.getAllCookieStores();
    storeId = stores.find((s) => tabId != null && s.tabIds.includes(tabId))?.id;
  } catch (_) { /* default store */ }
  const query = { domain: "youtube.com" };
  if (storeId) query.storeId = storeId;
  return chrome.cookies.getAll(query);
}

export async function sendCookies(tabId) {
  const cookies = await collectYoutubeCookies(tabId);
  if (!cookies.length) throw new Error("YouTube ki koi cookie nahi mili. Pehle YouTube par login karein.");
  const slim = cookies.map((c) => ({
    domain: c.domain, name: c.name, value: c.value, path: c.path,
    secure: c.secure, httpOnly: c.httpOnly, expirationDate: c.expirationDate,
  }));
  return post("/cookies", { cookies: slim });
}
