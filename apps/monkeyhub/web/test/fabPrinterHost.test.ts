/**
 * MonkeyFab remembers the printer address that was reached (D6) and never the access code: what a check or
 * an upload reached is kept and offered next time, a refused storage only means being asked again.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { PRINTER_HOST_KEY, rememberedPrinterHost, rememberPrinterHost } from "../src/features/fab/printerHost.ts";

const memory = () => {
  const values = new Map<string, string>();
  return { values, getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
};

test("the address a check or an upload reached is offered next time, trimmed", () => {
  const store = memory();
  assert.equal(rememberedPrinterHost(store), "", "nothing is invented before anything was reached");
  rememberPrinterHost("  192.168.1.50 ", store);
  assert.equal(store.values.get(PRINTER_HOST_KEY), "192.168.1.50");
  assert.equal(rememberedPrinterHost(store), "192.168.1.50");
  rememberPrinterHost("192.168.1.77", store);
  assert.equal(rememberedPrinterHost(store), "192.168.1.77", "the latest address that worked wins");
});

test("an empty address is not kept over a good one", () => {
  const store = memory();
  rememberPrinterHost("192.168.1.50", store);
  rememberPrinterHost("   ", store);
  assert.equal(rememberedPrinterHost(store), "192.168.1.50");
});

test("storage that refuses reads as nothing remembered and never throws", () => {
  const refusing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(rememberedPrinterHost(refusing), "");
  assert.doesNotThrow(() => rememberPrinterHost("192.168.1.50", refusing));
  assert.equal(rememberedPrinterHost(null), "");
  assert.doesNotThrow(() => rememberPrinterHost("192.168.1.50", null));
});
