// A walkthrough fails when the screen shows the service's own words: nobody should read a validation message,
// a seat id, a route or a traceback to find out what to do next. Developer mode shows them on purpose, so the
// check is for the ordinary screen.

/** What gives a service refusal away on screen, and what it is called in the failure message. */
export const RAW_ERROR_PATTERNS = [
  ["Value error", /Value error/i],
  ["Extra inputs", /Extra inputs/i],
  ["a seat id", /seat '/i],
  ["an API route", /\/api\//],
  ["Traceback", /Traceback/],
];

/** The raw-looking text a page shows right now, one line per match, each with the words around it. */
export async function rawErrorsOnScreen(page) {
  const text = await page.evaluate(() => document.body.innerText);
  const found = [];
  for (const [name, pattern] of RAW_ERROR_PATTERNS) {
    const match = pattern.exec(text);
    if (match) found.push(`${name}: …${text.slice(Math.max(0, match.index - 30), match.index + 60).replace(/\s+/g, " ")}…`);
  }
  return found;
}

/** Fail when the page shows any of them; `where` says which step of the walkthrough was on screen. */
export async function assertPlainScreen(page, where) {
  const found = await rawErrorsOnScreen(page);
  if (found.length) throw new Error(`The screen shows the service's own words at "${where}":\n  ${found.join("\n  ")}`);
}
