"use strict";

const status = document.getElementById("status");
const media = document.getElementById("media");
const returnButton = document.getElementById("returnButton");
let activeTabId = null;

chrome.tabs.query({ active: true, currentWindow: true }).then(([tab]) => {
  if (!tab?.id) return;
  activeTabId = tab.id;
  return chrome.runtime.sendMessage({ type: "SAVEFLOW_HELPER_SESSION", tabId: tab.id });
}).then((response) => {
  const session = response?.session;
  if (!session) return;
  const candidates = session.candidates || [];
  status.textContent = candidates.length
    ? `${candidates.length} media candidates detected on this tab.`
    : "No media yet. Interact with the page, then check again.";
  media.hidden = !candidates.length;
  for (const candidate of candidates.slice(0, 8)) {
    const item = document.createElement("li");
    item.title = candidate.url;
    item.textContent = `${candidate.kind} · ${new URL(candidate.url).hostname}`;
    media.appendChild(item);
  }
  returnButton.hidden = false;
});

returnButton.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "SAVEFLOW_HELPER_RETURN", tabId: activeTabId }).then(() => window.close());
});
