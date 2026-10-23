// Browser tests reach a control by the command it renders (data-command), not by its label or
// position, so moving a control between bars or menus does not break the test (#300).

const quoted = (value) => JSON.stringify(String(value));

/**
 * The controls in `scope` that render command `id`; with `arg`, only the one for that item
 * (data-command-arg). Like getByRole, hidden controls are left out unless `includeHidden`.
 */
export function cmd(scope, id, { arg, includeHidden = false } = {}) {
  const selector = `[data-command=${quoted(id)}]${arg === undefined ? "" : `[data-command-arg=${quoted(arg)}]`}`;
  const located = scope.locator(selector);
  return includeHidden ? located : located.filter({ visible: true });
}

/** A control's name as a person reads it: its aria-label, else its text, whitespace collapsed. */
export async function commandLabel(locator) {
  return locator.evaluate((element) =>
    (element.getAttribute("aria-label") ?? element.textContent ?? "").replace(/\s+/g, " ").trim());
}

/** Waits until the control shows `label`, for tests that check what a control says (locale). */
export async function waitForCommandLabel(locator, label, { timeout = 30_000 } = {}) {
  const deadline = Date.now() + timeout;
  let seen;
  for (;;) {
    seen = await commandLabel(locator.first()).catch(() => undefined);
    if (seen === label) return;
    if (Date.now() > deadline) throw new Error(`${locator} shows ${quoted(seen)}, not ${quoted(label)}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}
