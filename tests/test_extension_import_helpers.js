const assert = require("node:assert/strict");

const {
  extractProjectId,
  buildAccountImportPayload,
  serializeGoogleCookies,
  hasProtocolGoogleCookies,
  toPluginEndpoint,
} = require("../extension/import_helpers.js");

assert.equal(
  extractProjectId("https://labs.google/fx/zh/tools/flow/project/cb11c2e0-4724-46e8-9e3b-5f892a49e282"),
  "cb11c2e0-4724-46e8-9e3b-5f892a49e282",
);

assert.equal(
  extractProjectId("https://labs.google/fx/tools/flow/project/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee?x=1"),
  "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
);

assert.throws(
  () => extractProjectId("https://labs.google/fx/zh/tools/flow"),
  /请先打开 Google Flow 项目页面/,
);

assert.equal(
  toPluginEndpoint("https://niktokfurniture.com/api/plugin/update-token"),
  "https://niktokfurniture.com/api/plugin/update-token",
);

assert.throws(
  () => toPluginEndpoint("wss://niktokfurniture.com/captcha_ws"),
  /连接接口必须是 http:\/\/ 或 https:\/\//,
);

const googleCookies = [
  { name: "SID", value: "sid-value", domain: ".google.com", path: "/" },
  { name: "HSID", value: "hsid-value", domain: ".google.com", path: "/" },
  { name: "NID", value: "ignored", domain: ".google.com", path: "/" },
  { name: "SID", value: "sid-value", domain: ".google.com", path: "/" },
];

const serializedCookies = serializeGoogleCookies(googleCookies);
const parsedCookies = JSON.parse(serializedCookies);
assert.deepEqual(
  parsedCookies.map((cookie) => cookie.name),
  ["SID", "HSID"],
);
assert.equal(hasProtocolGoogleCookies(serializedCookies), true);
assert.equal(hasProtocolGoogleCookies("NID=value"), false);

assert.deepEqual(
  buildAccountImportPayload({
    sessionToken: "st",
    projectId: "project-id",
    projectName: "Project",
    remark: "Remark",
    routeKey: "route-1",
    googleCookies: serializedCookies,
  }),
  {
    session_token: "st",
    project_id: "project-id",
    project_name: "Project",
    remark: "Remark",
    extension_route_key: "route-1",
    image_enabled: true,
    video_enabled: false,
    image_concurrency: -1,
    video_concurrency: 0,
    protocol_mode: "protocol",
    google_cookies: serializedCookies,
    auto_refresh_enabled: true,
    refresh_interval_minutes: 120,
  },
);

assert.equal(
  buildAccountImportPayload({
    sessionToken: "st",
    projectId: "project-id",
    googleCookies: "",
  }).protocol_mode,
  "session",
);
