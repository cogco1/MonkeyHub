# GH-334

Issue: https://github.com/cogco1/MonkeyHub/issues/334
Base: `676630d2`.

Keys and sign-ins are entered where they are used: the AI Render page takes the Gemini key, the Conversations page takes a Coding Plan endpoint and token and opens Codex / Claude sign-in. Keys go into the Windows Credential Manager, write-only over HTTP (status and source only, never the value); an environment variable still wins and is named.
