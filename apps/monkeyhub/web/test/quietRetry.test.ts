import assert from "node:assert/strict";
import test from "node:test";

import { nextQuietTry, QUIET_RETRIES, tried } from "../src/app/quietRetry.ts";

test("a failed read is tried again twice by itself, then the person is asked, and counting starts over after a quiet spell", () => {
  tried.clear();
  assert.equal(nextQuietTry("board_open", 0), QUIET_RETRIES[0]);
  assert.equal(nextQuietTry("board_open", 1_500), QUIET_RETRIES[1]);
  assert.equal(nextQuietTry("board_open", 5_000), null, "the button now");
  assert.equal(nextQuietTry("layout_load", 5_000), QUIET_RETRIES[0], "each place counts its own tries");
  assert.equal(nextQuietTry("board_open", 5_000 + 31_000), QUIET_RETRIES[0], "a place that has not failed for a while starts over");
});
