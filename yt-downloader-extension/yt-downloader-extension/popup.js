import { findPort, getToken, setToken, sendUrls, sendCookies } from "./api.js";

const $ = (id) => document.getElementById(id);
const QUALITIES = ["Best available", "4K (2160p)", "1440p", "Full HD (1080p)", "HD (720p)", "480p", "360p", "Audio only (MP3)"];

function msg(text, kind = "") {
  $("msg").textContent = text;
  $("msg").className = "msg " + kind;
}

async function busy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { msg(e.message, "err"); } finally { btn.disabled = false; }
}

async function init() {
  $("quality").append(new Option("Quality: app wali default", ""));
  QUALITIES.forEach((q) => $("quality").append(new Option(q, q)));

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("page").textContent = tab?.title || tab?.url || "";

  const token = await getToken();
  $("token").value = token;
  if (!token) $("settings").open = true;

  const port = await findPort();
  $("dot").className = "dot " + (port ? "ok" : "bad");
  $("status").textContent = port ? `App connected (port ${port})` : "App nahi mili - yt_downloader_pro.py chalayein";

  $("send").onclick = () => busy($("send"), async () => {
    if (!tab?.url || !/^https?:/i.test(tab.url)) throw new Error("Is page ko download nahi kiya ja sakta.");
    await sendUrls([tab.url], { quality: $("quality").value || undefined, playlist: $("playlist").checked });
    msg("Queue mein add ho gaya. App mein progress dekhein.", "ok");
  });

  $("cookies").onclick = () => busy($("cookies"), async () => {
    const r = await sendCookies(tab?.id);
    if (r.problems?.length) msg(`${r.count} cookies bheji, magar: ${r.problems.join("; ")}`, "err");
    else msg(`${r.count} cookies app ko mil gayin. App mein ab cookies.txt set hai.`, "ok");
  });

  $("save").onclick = async () => {
    await setToken($("token").value);
    msg("Token save ho gaya.", "ok");
    init();
  };
}

init();
