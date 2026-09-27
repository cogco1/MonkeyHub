# GH-373

Issue: https://github.com/cogco1/MonkeyHub/issues/373
Base: `b73521c2`.

A second launch of the MonkeyHub desktop on the same runtime root opens no second window (#373): the desktop takes a per-root single-instance lock before creating any window; when it is held, it brings the open window to the front, shows a short 'MonkeyHub is already running' notice and exits. Non-window calls (--version, --complete-update), the update trial handoff and independent explicit runtime roots keep working; hub.lock stays as the second guard.
