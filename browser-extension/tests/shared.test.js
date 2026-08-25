"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const media = require("../shared.js");

test("classifies common media without discarding signed queries", () => {
  assert.equal(media.classify("https://cdn.example/video.mp4?token=abc"), "video");
  assert.equal(media.classify("https://cdn.example/photo.webp"), "image");
  assert.equal(media.classify("https://cdn.example/master.m3u8"), "stream");
  assert.equal(media.asHttpUrl("https://cdn.example/video.mp4?token=abc#part"), "https://cdn.example/video.mp4?token=abc");
});

test("rejects non-http and ordinary page assets", () => {
  assert.equal(media.normalizeCandidate({ url: "blob:https://example.com/id", kind: "video" }), null);
  assert.equal(media.normalizeCandidate({ url: "https://example.com/app.js" }), null);
  assert.equal(media.normalizeCandidate({ url: "file:///tmp/video.mp4", kind: "video" }), null);
  assert.equal(media.normalizeCandidate({ url: "http://127.0.0.1/video.mp4", kind: "video" }), null);
  assert.equal(media.normalizeCandidate({ url: "http://192.168.1.10/video.mp4", kind: "video" }), null);
});

test("keeps a trusted DOM hint even when the URL has no extension", () => {
  const candidate = media.normalizeCandidate({
    url: "/media?id=42",
    kind: "image",
    trustedHint: true,
    source: "dom-image",
  }, "https://example.com/post");
  assert.equal(candidate.url, "https://example.com/media?id=42");
  assert.equal(candidate.kind, "image");
});

test("builds the official TVID MP4 fallback request from a signed HLS URL", () => {
  const request = media.tvidFallbackRequest(
    "https://api.tvid.app/api/public-hls/abc_123/master.m3u8?token=x&sess=sess_456",
  );
  assert.deepEqual(request, {
    url: "https://api.tvid.app/api/public/abc_123/fallback",
    body: {
      failed_url: "https://api.tvid.app/api/public-hls/abc_123/master.m3u8?token=x&sess=sess_456",
      sess: "sess_456",
    },
  });
  assert.equal(media.tvidFallbackRequest("https://cdn.example/master.m3u8"), null);
});
