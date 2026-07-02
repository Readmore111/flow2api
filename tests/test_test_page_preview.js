const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("static/test.html", "utf8");
const scriptMatch = html.match(/<script>\s*([\s\S]*?)\s*<\/script>\s*<\/body>/);
assert(scriptMatch, "static/test.html script block not found");

const script = scriptMatch[1];
const start = script.indexOf("function cleanMediaUrlCandidate");
const end = script.indexOf("async function generate");
assert(start >= 0 && end > start, "media helper block not found");
assert(html.includes('id="imageCountSelect"'), "test page should expose image count selector");

const context = {
  URL,
  window: {
    location: {
      protocol: "https:",
      origin: "https://example.test",
    },
  },
  $: (id) => ({
    value: id === "baseUrl" ? "https://example.test" : "",
  }),
};
vm.createContext(context);
vm.runInContext(
  `${script.slice(start, end)}
this.extractMedia = extractMedia;
this.extractAssistantText = extractAssistantText;
this.extractMediaFromApiPayload = extractMediaFromApiPayload;
this.getGenerateCount = getGenerateCount;
`,
  context
);

context.$ = (id) => ({
  value: id === "imageCountSelect" ? "4" : "",
});
assert.equal(context.getGenerateCount(), 4, "image count selector should allow 4 images");
context.$ = (id) => ({
  value: id === "imageCountSelect" ? "9" : "",
});
assert.equal(context.getGenerateCount(), 1, "image count selector should clamp invalid values to 1");

const flowImageUrl = "https://flow-content.google/image/12345678-1234-1234-1234-123456789abc?token=abc";
assert.deepStrictEqual(
  Array.from(context.extractMedia(`完成 ${flowImageUrl}`).map((item) => item.url)),
  [flowImageUrl],
  "plain Flow image URLs without file extensions should preview"
);

const openAiPayload = {
  choices: [
    {
      message: {
        content: `![Generated Image](${flowImageUrl})`,
      },
    },
  ],
};
assert.strictEqual(
  context.extractAssistantText(openAiPayload),
  `![Generated Image](${flowImageUrl})`,
  "non-stream OpenAI message content should be readable"
);
assert.deepStrictEqual(
  Array.from(context.extractMediaFromApiPayload(openAiPayload).map((item) => item.url)),
  [flowImageUrl],
  "non-stream OpenAI image payload should preview"
);

const geminiPayload = {
  candidates: [
    {
      content: {
        parts: [
          {
            fileData: {
              mimeType: "image/png",
              fileUri: "/tmp/generated-image.png",
            },
          },
          {
            inlineData: {
              mimeType: "image/jpeg",
              data: "YWJjZA==",
            },
          },
        ],
      },
    },
  ],
};
assert.deepStrictEqual(
  Array.from(context.extractMediaFromApiPayload(geminiPayload).map((item) => item.url)),
  ["https://example.test/tmp/generated-image.png", "data:image/jpeg;base64,YWJjZA=="],
  "Gemini fileData and inlineData image parts should preview"
);

console.log("test page preview extraction checks passed");
