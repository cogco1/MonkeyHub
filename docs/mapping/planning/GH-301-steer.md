# GH-301 mid-turn steer

Issue: https://github.com/cogco1/MonkeyHub/issues/301
Base: `e183a69a`.

The architect can send a message while the Agent works. The Hub records it as an interjection and hands it to the running turn: Claude reads it from its stream-json stdin at the next step; Codex takes it through the adapter's steering or, where that is unavailable, cancels the current step and continues with it as the next prompt. A message that arrives as the turn ends becomes the next prompt. Stop keeps working.
