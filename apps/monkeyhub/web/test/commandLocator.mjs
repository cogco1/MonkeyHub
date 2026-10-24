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

/**
 * What finding a control by its label used to check on the side: within `scope` (one element),
 * every visible control that names a command has a readable name and an interactive role, and
 * every visible button, menu item and tab names a command. `ignore` lists selectors of subtrees owned elsewhere
 * (Excalidraw's own UI, the Hub shell). Returns the problems as text, so a test asserts it is empty;
 * it also catches a `data-command={expression}` that renders nothing, which the static scan cannot see.
 */
export async function commandProblems(scope, { ignore = [] } = {}) {
  return scope.evaluate((root, ignored) => {
    const visible = (element) => {
      const box = element.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && getComputedStyle(element).visibility !== "hidden";
    };
    const skipped = (element) => ignored.some((selector) => element.closest(selector));
    const name = (element) => (element.getAttribute("aria-label")
      || (element.getAttribute("aria-labelledby") ?? "").split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? "").join(" ")
      || element.textContent || element.getAttribute("title") || "").replace(/\s+/g, " ").trim();
    const interactive = (element) => ["BUTTON", "A", "SUMMARY"].includes(element.tagName)
      || /^(button|link|tab|option|treeitem|menuitem|menuitemcheckbox|menuitemradio|switch|checkbox|radio)$/.test(element.getAttribute("role") ?? "");
    const problems = [];
    for (const element of root.querySelectorAll("[data-command]")) {
      if (!visible(element) || skipped(element)) continue;
      const id = element.getAttribute("data-command");
      if (!id) problems.push(`empty data-command: ${element.outerHTML.slice(0, 100)}`);
      else if (!name(element)) problems.push(`${id}: no readable name`);
      else if (!interactive(element)) problems.push(`${id}: a ${element.tagName.toLowerCase()} without an interactive role`);
    }
    for (const element of root.querySelectorAll("button, [role=menuitem], [role=menuitemcheckbox], [role=menuitemradio], [role=tab]")) {
      if (visible(element) && !skipped(element) && !element.hasAttribute("data-command")) {
        problems.push(`${element.getAttribute("role") ?? "button"} without data-command: ${name(element) || element.outerHTML.slice(0, 100)}`);
      }
    }
    return problems;
  }, ignore);
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
