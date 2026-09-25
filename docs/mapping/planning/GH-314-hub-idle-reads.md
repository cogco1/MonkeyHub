# GH-314

Issue: https://github.com/cogco1/MonkeyHub/issues/314
Base: `82192a41` (after batch G).

An idle Hub stops re-reading every open project: the project watcher re-derives which work copies exist only when the runs, their document records or the files in their copy workspaces change, and a read forwarded to Studio no longer wakes the watcher.

The Studio side (list_documents selecting its two record kinds) follows once artifacts.py is free.
