import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { parseLyricTokens } from "../assets/src/utils/lyricTokens.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cases = JSON.parse(
  fs.readFileSync(path.join(root, "specs", "lyric_tokenization_cases.json"), "utf8"),
);

for (const example of cases) {
  test(`Web lyric tokenization: ${example.name}`, () => {
    assert.deepEqual(parseLyricTokens(example.text, example.mode), example.tokens);
  });
}

test("Web lyric tokenization rejects unsupported modes", () => {
  assert.throws(() => parseLyricTokens("あ", "unknown"), RangeError);
});
