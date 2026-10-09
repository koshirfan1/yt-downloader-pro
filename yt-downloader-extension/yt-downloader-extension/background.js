import { sendUrls } from "./api.js";

function badge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
}

async function send(url) {
  if (!url || !/^https?:/i.test(url)) return badge("!", "#dc2626");
  try {
    await sendUrls([url]);
    badge("OK", "#16a34a");
  } catch (e) {
    console.warn("YT Downloader:", e.message);
    badge("!", "#dc2626");
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "send-to-ytdp",
      title: "YT Downloader mein bhejein",
      contexts: ["page", "link"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info) => send(info.linkUrl || info.pageUrl));

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "send-current") return;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  send(tab?.url);
});
