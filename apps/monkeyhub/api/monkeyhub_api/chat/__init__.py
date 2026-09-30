"""The Hub's conversations: project-bound chats run by the installed coding CLIs.

``store`` keeps each chat and runs its turns, and is also the stdio MCP server a
CLI starts by its path; ``acp_session`` holds one Codex ACP session,
``skill_plugins`` hands the library project's skills to Claude, and
``turn_trace`` records each turn's content-free diagnostics for MonkeyMonitor.
"""
