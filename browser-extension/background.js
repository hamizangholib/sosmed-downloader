"use strict";

importScripts("shared.js");

const MAX_CANDIDATES = 250;
const SAVEFLOW_HOSTS = new Set([
  "download.xsaintz.my.id",
  "hamizangholib.github.io",
  "localhost",
  "127.0.0.1",
]);

let sessions = {};
const ready = chrome.storage.session.get("sessions").then((stored) => {
  sessions = stored.sessions || {};
});

function isSaveflowPage(url) {
  try {
    return SAVEFLOW_HOSTS.has(new URL(url).hostname);
  } catch {
    return false;
  }
}

async function persist() {
  await chrome.storage.session.set({ sessions });
}

function mergeCandidates(session, incoming, baseUrl) {
  const existing = new Map((session.candidates || []).map((candidate) => [candidate.url, candidate]));
  for (const raw of incoming || []) {
    const candidate = SaveflowMedia.normalizeCandidate(raw, baseUrl);
    if (!candidate) continue;
    const previous = existing.get(candidate.url);
    existing.set(candidate.url, {
      ...previous,
      ...candidate,
      thumbnail: candidate.thumbnail || previous?.thumbnail || null,
    });
    if (existing.size >= MAX_CANDIDATES) break;
  }
  session.candidates = [...existing.values()];
}

async function pushResults(sourceTabId) {
  const session = sessions[sourceTabId];
  if (!session) return;
  await chrome.action.setBadgeText({ tabId: Number(sourceTabId), text: String(session.candidates.length || "") });
  await chrome.action.setBadgeBackgroundColor({ tabId: Number(sourceTabId), color: "#4b8ef1" });
  chrome.tabs.sendMessage(session.saveflowTabId, {
    type: "SAVEFLOW_HELPER_RESULTS",
    sourceUrl: session.sourceUrl,
    pageUrl: session.pageUrl || session.sourceUrl,
    title: session.title || "Detected media",
    candidates: session.candidates || [],
  }).catch(() => {});
  chrome.tabs.sendMessage(Number(sourceTabId), {
    type: "SAVEFLOW_HELPER_STATUS",
    count: session.candidates.length,
  }).catch(() => {});
}

async function openSource(url, saveflowTabId) {
  const normalized = SaveflowMedia.asHttpUrl(url);
  if (!normalized) throw new Error("Only public HTTP or HTTPS links can be scanned.");
  // Create the tab first, persist its session, and only then navigate. This
  // prevents the document-start content script from racing the session write.
  const tab = await chrome.tabs.create({ active: true });
  sessions[tab.id] = {
    saveflowTabId,
    sourceUrl: normalized,
    pageUrl: normalized,
    title: "Detected media",
    candidates: [],
  };
  await persist();
  await chrome.tabs.update(tab.id, { url: normalized });
  return tab.id;
}

async function handleMessage(message, sender) {
  await ready;

  if (message?.type === "SAVEFLOW_HELPER_OPEN") {
    if (!sender.tab?.id || !isSaveflowPage(sender.tab.url)) {
      throw new Error("This request did not come from Saveflow.");
    }
    const sourceTabId = await openSource(message.url, sender.tab.id);
    return { ok: true, sourceTabId };
  }

  if (message?.type === "SAVEFLOW_HELPER_CANDIDATES") {
    const sourceTabId = sender.tab?.id;
    const session = sessions[sourceTabId];
    if (!session) return { ok: false };
    session.pageUrl = SaveflowMedia.asHttpUrl(message.pageUrl, session.sourceUrl) || session.pageUrl;
    session.title = String(message.title || session.title).slice(0, 160);
    mergeCandidates(session, message.candidates, session.pageUrl);
    await persist();
    await pushResults(sourceTabId);
    return { ok: true, count: session.candidates.length };
  }

  if (message?.type === "SAVEFLOW_HELPER_DOWNLOAD") {
    if (!sender.tab?.id || !isSaveflowPage(sender.tab.url)) {
      throw new Error("Downloads can only be requested from Saveflow.");
    }
    const url = SaveflowMedia.asHttpUrl(message.url);
    if (!url) throw new Error("That media URL is invalid.");
    const filename = SaveflowMedia.safeFilename(message.filename);
    const downloadId = await chrome.downloads.download({ url, filename, saveAs: true });
    return { ok: true, downloadId };
  }

  if (message?.type === "SAVEFLOW_HELPER_RETURN") {
    const session = sessions[message.tabId || sender.tab?.id];
    if (!session) return { ok: false };
    await chrome.tabs.update(session.saveflowTabId, { active: true });
    return { ok: true };
  }

  if (message?.type === "SAVEFLOW_HELPER_SESSION") {
    const tabId = message.tabId || sender.tab?.id;
    return { ok: true, session: sessions[tabId] || null };
  }

  return { ok: false };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    ready.then(async () => {
      const session = sessions[details.tabId];
      if (!session) return;
      const candidate = SaveflowMedia.normalizeCandidate({
        url: details.url,
        source: "network",
      }, session.pageUrl);
      if (!candidate || candidate.kind === "unknown") return;
      mergeCandidates(session, [candidate], session.pageUrl);
      await persist();
      await pushResults(details.tabId);
    });
  },
  { urls: ["http://*/*", "https://*/*"] },
);

chrome.tabs.onRemoved.addListener((tabId) => {
  ready.then(async () => {
    if (sessions[tabId]) {
      delete sessions[tabId];
      await persist();
      return;
    }
    let changed = false;
    for (const [sourceTabId, session] of Object.entries(sessions)) {
      if (session.saveflowTabId === tabId) {
        delete sessions[sourceTabId];
        changed = true;
      }
    }
    if (changed) await persist();
  });
});
