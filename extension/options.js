const DEFAULT_SETTINGS = {
  serverUrl: "wss://niktokfurniture.com/captcha_ws",
  apiKey: "d9149b7622983a32655afc9f14845283b2fba16b78e21351",
  routeKey: "",
  clientLabel: "",
  connectionUrl: "https://www.niktokfurniture.com/api/plugin/update-token",
  connectionToken: "GTousjmreANGl4rix-l10DtQVXesdXR608T1JXtttAk"
};

const $ = (id) => document.getElementById(id);
const helpers = globalThis.Flow2ApiImportHelpers;

function normalizeSettings(values) {
  return {
    serverUrl: (values.serverUrl || DEFAULT_SETTINGS.serverUrl).trim(),
    apiKey: (values.apiKey || "").trim(),
    routeKey: (values.routeKey || "").trim(),
    clientLabel: (values.clientLabel || "").trim(),
    connectionUrl: (values.connectionUrl || DEFAULT_SETTINGS.connectionUrl).trim(),
    connectionToken: (values.connectionToken || "").trim()
  };
}

function setStatus(message, isError = false) {
  const status = $("status");
  status.textContent = message;
  status.style.color = isError ? "#b91c1c" : "#065f46";
}

function isValidWsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "ws:" || url.protocol === "wss:";
  } catch (e) {
    return false;
  }
}

function loadSettings() {
  chrome.storage.local.get(DEFAULT_SETTINGS, (stored) => {
    const settings = normalizeSettings(stored);
    $("serverUrl").value = settings.serverUrl;
    $("apiKey").value = settings.apiKey;
    $("routeKey").value = settings.routeKey;
    $("clientLabel").value = settings.clientLabel;
    $("connectionUrl").value = settings.connectionUrl;
    $("connectionToken").value = settings.connectionToken;
  });
}

function readFormSettings() {
  return normalizeSettings({
    serverUrl: $("serverUrl").value,
    apiKey: $("apiKey").value,
    routeKey: $("routeKey").value,
    clientLabel: $("clientLabel").value,
    connectionUrl: $("connectionUrl").value,
    connectionToken: $("connectionToken").value
  });
}

function saveSettings() {
  const settings = readFormSettings();

  if (!isValidWsUrl(settings.serverUrl)) {
    setStatus("WebSocket URL 必须以 ws:// 或 wss:// 开头。", true);
    return;
  }
  if (!settings.apiKey) {
    setStatus("请填写 Flow2API API Key。", true);
    return;
  }

  try {
    helpers.toPluginEndpoint(settings.connectionUrl);
  } catch (e) {
    setStatus(e.message, true);
    return;
  }

  chrome.storage.local.set(settings, () => {
    if (chrome.runtime.lastError) {
      setStatus(`保存失败：${chrome.runtime.lastError.message}`, true);
      return;
    }
    setStatus("已保存，后台连接会自动重连。");
  });
}

function queryTabs(queryInfo) {
  return new Promise((resolve, reject) => {
    chrome.tabs.query(queryInfo, (tabs) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(tabs || []);
    });
  });
}

function findProjectTab(tabs) {
  const candidates = [];
  for (const tab of tabs) {
    try {
      const projectId = helpers.extractProjectId(tab.url || "");
      candidates.push({ tab, projectId });
    } catch (e) {
      // Ignore non-project Labs tabs.
    }
  }
  candidates.sort((a, b) => (b.tab.lastAccessed || 0) - (a.tab.lastAccessed || 0));
  if (!candidates.length) {
    throw new Error("请先打开一个 Google Flow 项目页面。");
  }
  return candidates[0];
}

function getLabsSessionToken() {
  return new Promise((resolve, reject) => {
    chrome.cookies.get({
      url: "https://labs.google/",
      name: "__Secure-next-auth.session-token"
    }, (cookie) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!cookie || !cookie.value) {
        reject(new Error("没有读取到 Google Labs 登录 Cookie，请确认当前 Chrome 已登录 Flow。"));
        return;
      }
      resolve(cookie.value);
    });
  });
}

function getCookiesForUrl(url) {
  return new Promise((resolve, reject) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(cookies || []);
    });
  });
}

async function getGoogleCookies() {
  const collected = [];
  for (const url of helpers.GOOGLE_COOKIE_URLS || []) {
    try {
      collected.push(...await getCookiesForUrl(url));
    } catch (e) {
      // Missing host permission or an unreadable Google domain should not block ST import.
    }
  }
  return helpers.serializeGoogleCookies(collected);
}

async function importCurrentFlowAccount() {
  const settings = readFormSettings();
  const endpoint = helpers.toPluginEndpoint(settings.connectionUrl);
  if (!settings.connectionToken) {
    throw new Error("请填写 Account Import Token。");
  }

  const tabs = await queryTabs({ url: "https://labs.google/*" });
  const { tab, projectId } = findProjectTab(tabs);
  const sessionToken = await getLabsSessionToken();
  const googleCookies = await getGoogleCookies();
  const projectName = `Imported ${new Date().toLocaleString()}`;
  const remark = settings.clientLabel
    ? `Imported by Chrome Extension (${settings.clientLabel})`
    : "Imported by Chrome Extension";
  const importPayload = helpers.buildAccountImportPayload({
    sessionToken,
    projectId,
    projectName,
    remark,
    routeKey: settings.routeKey,
    googleCookies,
  });

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${settings.connectionToken}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(importPayload)
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.success) {
    throw new Error(data.detail || data.message || `导入失败：HTTP ${response.status}`);
  }

  return {
    action: data.action || "imported",
    message: data.message || `已导入 ${tab.url}`,
    protocolMode: importPayload.protocol_mode,
  };
}

async function handleImportClick() {
  const button = $("importBtn");
  button.disabled = true;
  const oldText = button.textContent;
  button.textContent = "导入中...";
  setStatus("正在读取当前 Flow 账号并导入账号池...");

  try {
    const result = await importCurrentFlowAccount();
    const protocolHint = result.protocolMode === "protocol"
      ? "已同步 Google Cookies，协议自动刷新已启用"
      : "未读取到 Google Cookies，已按仅 ST 模式导入";
    setStatus(`${result.message}（${result.action}）。${protocolHint}。`);
  } catch (e) {
    setStatus(e.message || "导入失败", true);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  $("saveBtn").addEventListener("click", saveSettings);
  $("importBtn").addEventListener("click", handleImportClick);
});
