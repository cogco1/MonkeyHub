import assert from "node:assert/strict";
import test from "node:test";
import { QuickTickets, settleElevations } from "../src/features/stage/quickDrawing.ts";

test("an answer for the model on screen before it changed is not shown as this model's (S15)", () => {
  const tickets = new QuickTickets();
  const onA = tickets.issue();
  assert.ok(tickets.isCurrent(onA));
  tickets.expire();  // the person opened model B while A's plan was being drawn
  assert.equal(tickets.isCurrent(onA), false, "A's plan, arriving late, is not written over B's page");
  const onB = tickets.issue();
  assert.ok(tickets.isCurrent(onB));
  tickets.expire(); tickets.expire();  // back to A and on again: still not the request that is current
  assert.equal(tickets.isCurrent(onB), false);
});

test("four elevations drawn together keep the three drawn when one fails, and name the one that did not", () => {
  const views = [{ label: "南立面", direction: "south" }, { label: "北立面", direction: "north" },
    { label: "西立面", direction: "west" }, { label: "东立面", direction: "east" }];
  const refused = new Error("the worker stopped");
  const results: PromiseSettledResult<string>[] = [{ status: "fulfilled", value: "front.png" }, { status: "rejected", reason: refused },
    { status: "fulfilled", value: "left.png" }, { status: "fulfilled", value: "right.png" }];
  const settled = settleElevations(results, views, (document) => `page:${document}`);
  assert.deepEqual(settled.pages, [{ page: "page:front.png", label: "南立面" }, { page: "page:left.png", label: "西立面" },
    { page: "page:right.png", label: "东立面" }]);
  assert.deepEqual(settled.failed, [{ label: "北立面", direction: "north", error: refused }]);
});
