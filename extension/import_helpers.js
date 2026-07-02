(function(root) {
  const GOOGLE_COOKIE_URLS = [
    "https://accounts.google.com/",
    "https://www.google.com/",
    "https://myaccount.google.com/",
  ];
  const GOOGLE_COOKIE_ALLOWLIST = [
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
  ];
  const PROTOCOL_REQUIRED_COOKIE_NAMES = new Set(["SID", "HSID", "SSID", "APISID", "SAPISID"]);

  function toPluginEndpoint(value) {
    const raw = String(value || "").trim();
    if (!raw) {
      throw new Error("请先填写连接接口");
    }
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      throw new Error("连接接口必须是 http:// 或 https://");
    }
    return url.toString().replace(/\/$/, "");
  }

  function extractProjectId(tabUrl) {
    const url = new URL(String(tabUrl || ""));
    const match = url.pathname.match(/\/flow\/project\/([0-9a-f-]{20,})/i);
    if (!match) {
      throw new Error("请先打开 Google Flow 项目页面");
    }
    return match[1];
  }

  function normalizeCookieItem(item) {
    if (!item || typeof item !== "object") return null;
    const name = String(item.name || "").trim();
    const value = String(item.value || "").trim();
    if (!name || !value || !GOOGLE_COOKIE_ALLOWLIST.includes(name)) return null;
    return {
      name,
      value,
      domain: String(item.domain || ".google.com").trim() || ".google.com",
      path: String(item.path || "/").trim() || "/",
    };
  }

  function serializeGoogleCookies(cookies) {
    const seen = new Set();
    const normalized = [];
    for (const cookie of Array.isArray(cookies) ? cookies : []) {
      const item = normalizeCookieItem(cookie);
      if (!item) continue;
      const key = `${item.name}:${item.domain}:${item.path}`;
      if (seen.has(key)) continue;
      seen.add(key);
      normalized.push(item);
    }
    return normalized.length ? JSON.stringify(normalized) : "";
  }

  function parseCookieNames(raw) {
    const text = String(raw || "").trim();
    if (!text) return new Set();
    try {
      const data = JSON.parse(text);
      const items = Array.isArray(data) ? data : Array.isArray(data.cookies) ? data.cookies : [];
      return new Set(items.map((item) => String(item && item.name || "").trim()).filter(Boolean));
    } catch (e) {
      return new Set(
        text
          .split(";")
          .map((part) => part.trim().split("=", 1)[0])
          .filter(Boolean)
      );
    }
  }

  function hasProtocolGoogleCookies(raw) {
    const names = parseCookieNames(raw);
    for (const name of PROTOCOL_REQUIRED_COOKIE_NAMES) {
      if (names.has(name)) return true;
    }
    return false;
  }

  function buildAccountImportPayload({
    sessionToken,
    projectId,
    projectName,
    remark,
    routeKey,
    googleCookies,
    loginAccount,
  }) {
    const serializedCookies = String(googleCookies || "").trim();
    const protocolMode = hasProtocolGoogleCookies(serializedCookies) ? "protocol" : "session";
    const payload = {
      session_token: String(sessionToken || "").trim(),
      project_id: String(projectId || "").trim(),
      project_name: String(projectName || "").trim(),
      remark: String(remark || "").trim(),
      extension_route_key: String(routeKey || "").trim(),
      image_enabled: true,
      video_enabled: false,
      image_concurrency: -1,
      video_concurrency: 0,
      protocol_mode: protocolMode,
      auto_refresh_enabled: true,
      refresh_interval_minutes: 120,
    };
    if (serializedCookies) payload.google_cookies = serializedCookies;
    if (loginAccount) payload.login_account = String(loginAccount).trim();
    return payload;
  }

  const api = {
    GOOGLE_COOKIE_URLS,
    extractProjectId,
    toPluginEndpoint,
    serializeGoogleCookies,
    hasProtocolGoogleCookies,
    buildAccountImportPayload,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  root.Flow2ApiImportHelpers = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
