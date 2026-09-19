# Computer use

MonkeyControl drives a Windows desktop the way a person demonstrating something would:
it names the element it is about to touch, shows it, does one thing, checks the thing it
promised would happen, and leaves a receipt. It exists so a method can be *shown* —
a recorded, replayable demonstration of an architectural tool being operated — and so an
agent can operate an application MonkeyHub has no API for.

**The core rule: nothing here is project state.** MonkeyControl writes receipts,
screenshots and recordings under one diagnostics directory its caller supplies, and
nothing else, ever. It never writes a P036 record, never opens a project, and never
becomes an alternative way to change a design. A model that wants to change a building
still goes through the Studio APIs the chat tools already expose. Code:
`monkeycontrol/` (registry module `monkeycontrol`), composed by the Hub in
`apps/monkeyhub/api/monkeyhub_api/computer_tools.py` (`hub.shell`).

## One action in, one receipt out

`ComputerAction@1` is a declarative action. It names a semantic target — a control type,
a name, an automation id — never a screen coordinate, because a coordinate proves
nothing about what was clicked.

| Key | Meaning |
| --- | --- |
| `schema` | `"ComputerAction@1"`, optional but checked when present |
| `step_id` | Your own id for this step; the runtime numbers unnamed steps `s-0001` |
| `intent` | One sentence, at most 200 characters, saying what this step is for |
| `application` | Process name, `.exe` optional (`notepad`); a launch may give a full path |
| `window` | Python regular expression the window title must match |
| `target` | `{controlType, name, nameRegex, automationId, className, index, bounds, backend}` — at least one property. `bounds` is honoured only with `backend: "visual-fallback"` |
| `action.type` | `launch`, `click`, `double_click`, `right_click`, `invoke`, `set_value`, `type`, `keypress`, `drag`, `scroll`, `wait`, `screenshot`, `highlight` |
| `action.button` | `left` (default), `right`, `middle` |
| `action.text` | Text for `type` and `set_value`; `set_value` may clear a field with `""` |
| `action.sensitive` | `true` records the text only as its length, and masks that text in every receipt, timeline note and overlay label this runtime writes from then on |
| `action.keys` | For `keypress`, e.g. `ctrl+s` |
| `action.to` | The destination target of a `drag` |
| `action.delta` | Wheel ticks for `scroll`, non-zero |
| `action.ms` | Milliseconds for `wait` |
| `action.command` | `[executable, argument...]` for `launch` |
| `verification` | `{expect, title, target, state, path, timeout_ms}` — see below |
| `capture` | `true` keeps a before and after screenshot even without a recording |

A verification is the only thing that turns an action that *ran* into an action that
*worked*. `expect` is `window` (a title matched), `element` (a target resolved, and any
declared `state` — `value` contains, `enabled`/`toggled` equal — held), `absent` (a window
or element is gone) or `file` (a `path` exists). It is polled until `timeout_ms`
(default 5000, maximum 60000); the first check happens before any waiting, so
`timeout_ms: 0` is one honest look.

`ComputerActionReceipt@1` is what comes back, and what is appended to the trace — for
every attempt, including every refusal, because a refusal that leaves no trace is
indistinguishable from an action nobody asked for.

| Key | Meaning |
| --- | --- |
| `schema` | `"ComputerActionReceipt@1"` |
| `step_id`, `intent`, `application`, `mode` | Carried from the action and the runtime |
| `window` | `{handle, title, pid, process, bounds}` actually found, or null |
| `target.requested` | The target as the action declared it |
| `target.resolved` | `{controlType, name, automationId, className, bounds, runtimeId, backend}`, or null |
| `backend` | `windows-uia`, `visual-fallback`, or `none` when nothing was resolved |
| `fallback` | Whether the resolution came from the fallback backend |
| `action` | The action as recorded, with sensitive text replaced by its length |
| `execution` | `{point, started_at, duration_ms}` — the point actually aimed at |
| `verification` | `{expect, status, detail, duration_ms, state?}`; `status` is `passed`, `failed` or `skipped` |
| `status` | `succeeded`, `failed` or `refused` |
| `refusal` | `{code, message, candidates?}` when there is one |
| `screenshots` | `{before, after}` paths inside the trace directory, or nulls |
| `digest` | Canonical digest over every other key, so an edited trace line says so |

Redaction rewrites prose only. A `message`, a `detail` and a read-back `state` value are
replaced wherever the sensitive text appears in them; `refusal.code`,
`verification.expect` and `verification.status` keep their words, because they are what a
caller matches on — typing `LOST` into a field must not turn `FOCUS_LOST` into a
redaction notice.

It is not only the step that typed. An application repeats what was typed into it —
Windows 11 titles a Notepad tab after the first line of the document, and the file dialog
names its file name box after what is in it — so the secret arrives back through the
*next* step's `window.title`, `target.resolved.name`, candidates and verification detail.
A runtime therefore remembers every sensitive text it has typed and masks it out of
everything it writes for the rest of its session: any string carrying one becomes
`<redacted N chars>` whole, in receipts (before the digest is taken over them), in
`timeline.ndjson` and in the labels and verdict chips drawn on screen. Only `schema`,
`step_id`, `status`, `backend`, `mode`, `action.type`, `refusal.code`,
`verification.expect` and `verification.status` are exempt, for the reason above. The
frames of a recording are the raw screen and still show whatever the screen showed.

## Refusal codes

| Code | What happened |
| --- | --- |
| `ACTION_INVALID` | The payload is not a `ComputerAction@1`; this one raises rather than answering a receipt |
| `APP_NOT_ALLOWED` | The application, or a launch's executable, is not in the allow-list |
| `WINDOW_NOT_FOUND` | No window of that application matched |
| `TARGET_UNRESOLVED` | No element matched the declared properties |
| `TARGET_AMBIGUOUS` | Several did, and no `index` chose between them |
| `FOCUS_LOST` | Another process held the foreground when the keystroke or click was due |
| `BACKEND_UNAVAILABLE` | A backend this step needs is not installed (Pillow for overlays, for example) |
| `HOST_ERROR` | A PowerShell host died or would not answer; the step is `failed`, not `refused`, because what reached the screen is unknown |
| `VERIFY_FAILED` | The declared post-condition did not hold; the step is `failed` |
| `RECORDING_ACTIVE` / `RECORDING_NOT_ACTIVE` | A recording was already running, or was not |

## Modes

`fast` does the least that achieves the action: a UI Automation pattern where one exists,
a click where it does not. `demo` is for being watched — it outlines the target, moves the
pointer visibly, holds the highlight for `highlight_ms`, and shows the verification verdict
as a chip next to the rectangle it is about. Both write the same receipt; the mode is
recorded in it.

## Permission: the policy file and four layers

Computer use is off until a file on this machine says otherwise. The Hub reads
`diagnostics/monkeycontrol/policy.json` under its runtime root (`LOCALAPPDATA/MonkeyHub`
by default) on **every** request, so enabling it is editing a file, not restarting the Hub:

```json
{ "enabled": true, "allowedProcesses": ["notepad", "explorer"], "mode": "demo" }
```

Absent, unreadable, invalid and `"enabled": false` all mean the same thing, and the Hub
answers `403 COMPUTER_USE_NOT_ENABLED` naming that path. The Hub only ever reads it;
nothing in the product writes it for you.

1. **Permission.** The policy file, and only it, turns computer use on for this machine.
   The MCP tools are advertised in every conversation — a route that explains itself is
   better than a tool that is not there — but Claude's `--allowedTools` for a headless
   turn includes them only where the policy enables them, because there is nobody to ask.
2. **Allow-list.** Every action's application, and a `launch`'s executable, is checked
   against `allowedProcesses` before anything is resolved. A bare executable name is
   matched by its basename — `notepad.exe` is `notepad` — but a command that names a
   place on disk is not, because `D:\anything\notepad.exe` is not the notepad whoever
   wrote the list meant: it is allowed only by appearing in that list as that exact path
   (compared normalised and case-insensitively), and refused `APP_NOT_ALLOWED` otherwise.
   The Hub's policy file spells its entries as process names, so a full path can only be
   allow-listed by a CLI script's own policy. Inspecting is held to the same list —
   reading a window tree is still reading somebody's screen — and `inspect --app X`
   allow-lists X for that one call.
3. **Focus guard.** Before a keystroke or a click, the runtime focuses the window and
   reads the foreground back. If another process holds it, the step is refused
   `FOCUS_LOST` rather than typed into whatever appeared. (`focus_guard` can be turned
   off in a script's own policy; the Hub does not.)
4. **Evidence.** Every attempt appends one receipt with a digest over its own content,
   including refusals. Sensitive text leaves the package only as its length — it is
   redacted in the action, in the verification detail and state, and in the refusal that
   quotes them, before the digest is taken — and it stays masked in every later receipt,
   timeline note and overlay label for the life of the runtime, because the screen goes
   on repeating it. Raw frames are the screen itself and are not masked.

## Two hosts, one desktop

The desktop is reached through two long-lived PowerShell workers spoken to in JSON lines:

- `monkeycontrol/hosts/execution_host.ps1` — **MTA**, holds UI Automation and `SendInput`.
- `monkeycontrol/hosts/presentation_host.ps1` — **STA**, holds the WPF overlay and screen
  capture. The overlay window is click-through, never activated, and excluded from
  capture, so it can never change what the next click reaches and never appears in a frame.

They are separate processes because the apartment models are incompatible and because a
host that dies must not take the runtime with it. When one dies, only *observations*
(`windows`, `inspect`, `read`, `foreground`, `screenshot`, `monitor`, …) are asked again;
nothing that reached the screen is ever replayed. Element runtime ids are dropped with the
host that issued them.

## The trace, a recording, and replaying it

Everything lands under the caller's trace directory — for the Hub,
`<runtime root>/diagnostics/monkeycontrol/`:

```text
actions.ndjson                    one ComputerActionReceipt@1 per line, canonical JSON
shots/<sha256>.png                before/after captures, stored once per content
recordings/<name>/frames/000001.png
recordings/<name>/frames.ndjson   index, timeline second and the screen bounds of each frame
recordings/<name>/timeline.ndjson what happened, at its own second: highlight, click, verify, …
recordings/<name>/manifest.json   name, interval, frame count, region, the steps it covers, video
recordings/<name>/raw.mp4         through ffmpeg when it is installed, else raw.gif
recordings/<name>/overlays/<projection>/  frames re-drawn after the fact
```

Frames are the **raw** screen. Nothing is drawn on them while recording, which is what
makes a recording replayable: `render` re-draws the same frames under a projection —
`clean` (pointer only), `presentation` (the step, the target and the verdict) or
`developer` (the backend, the automation id, the bounds and every timeline event). The
timeline keeps seconds rather than frame numbers, so a recording taken at any interval can
carry any projection. A recording follows the monitor the acted-on window is on by
default (`--record-region virtual` keeps the whole desktop); each frame records the
rectangle it actually captured, so a replayed overlay still lands on the right pixels.

## The CLI

```powershell
python -m monkeycontrol inspect --app notepad --trace-dir DIR [--window REGEX] [--depth 6]
python -m monkeycontrol run SCRIPT.json --trace-dir DIR [--mode demo] [--allow explorer]
    [--record demo-01] [--record-interval 250] [--record-region window-monitor|virtual]
    [--stop-on-failure]
python -m monkeycontrol render RECORDINGS/<name> [--projection presentation] [--out DIR]
```

A script is a `ComputerActionScript@1` document: `{"schema": ..., "policy": {...},
"actions": [...]}`, where the policy carries `allowed_processes`, `mode`, `highlight_ms`
and `focus_guard`. `${TEMP}` in a path, a command or typed text is replaced with this
machine's temporary directory, so a demo script names no user folder. The CLI prints one
line per action and exits **0** when every action succeeded, **1** when one did not,
**2** when the action or script is invalid, **3** when the runtime refused the request
itself. `monkeycontrol/demos/notepad_explorer.json` is a worked example.

## Through MonkeyHub

The Hub owns the lifecycle: one `ComputerService` per Hub, built on first use, rebuilt
when the policy's allow-list or mode changed (closing the old runtime first), and closed
with the Hub's other children on shutdown.

| Request | Result |
| --- | --- |
| `POST /api/computer/inspect` `{application, window?, depth?}` | The window's element tree: `{application, window, windows, nodes, truncated}` |
| `POST /api/computer/actions` `{action, mode?}` | One `ComputerActionReceipt@1` |
| `POST /api/computer/recordings` `{command: "start"\|"stop", name?}` | The started recording, or the finished manifest |

A receipt is a `200` body whatever it says, refusals included: an agent has to read the
refusal to do anything about it. Only two outcomes become statuses of their own —
`422 COMPUTER_ACTION_INVALID` for a payload that is not a `ComputerAction@1`, and
`409 COMPUTER_ACTION_REFUSED` (carrying MonkeyControl's own code) for a refusal with no
receipt to carry it: a recording already running, a recording that is not, a backend that
will not start. A machine whose policy does not enable computer use answers
`403 COMPUTER_USE_NOT_ENABLED`.

The chat MCP tools `computer_inspect`, `computer_action` and `computer_record` proxy those
three routes and check nothing a second time. In the transcript a receipt reads as the
sentence it is — `CLICK — Save ✓`, or `REFUSED APP_NOT_ALLOWED — open the folder` — and the
diagnostics journal records the three names with `request_kind: "computer"`, without ever
copying a tool title that might carry private text.

## What this slice does not do

- **Windows only.** UI Automation, `SendInput` and WPF; there is no macOS or Linux host.
- **No browser provider.** Driving a page through its DOM is a reserved interface, not an
  implementation; a browser is currently just another window.
- **No vision target proposal.** A target is named by UI Automation properties. The
  `visual-fallback` backend accepts explicit bounds from a caller who has already decided;
  nothing here looks at a screenshot and proposes what to click.
- **`raw.mp4` needs ffmpeg** on the PATH or in `MONKEYCONTROL_FFMPEG`; without it a
  recording keeps its frames and a sampled `raw.gif`.
- **No destructive-key policy.** The allow-list says which applications may be driven, not
  which keystrokes are dangerous inside them: `keypress` will send what it is given, and an
  allow-listed application's own destructive commands are reachable. Keep the list narrow.
- **One at a time, and three minutes through a conversation.** The Hub holds one runtime
  behind one lock, so computer requests are served one after another: while a step is
  running, every other computer call waits, and so does the Hub's shutdown, which closes
  that runtime. `action.ms` allows a `wait` of up to 600 000 ms, but a call made through
  the MCP tools travels over `chat._request_json`, whose limit is 180 s — a longer step
  ends that tool call while the action itself carries on and still writes its receipt.
  Long waits belong in a script run by the CLI, which has no such limit.
- **Overlay projections need Pillow.** Without it, `render` refuses by name
  (`BACKEND_UNAVAILABLE`) rather than failing to import.
