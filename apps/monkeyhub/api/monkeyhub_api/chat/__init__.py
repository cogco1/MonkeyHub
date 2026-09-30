"""The Hub's conversations: project-bound chats run by the installed coding CLIs.

``store`` keeps each chat and runs its turns on the CLIs ``providers``
describes, with each tool call as one ``activity`` row, a message's attached
files and registered pages in ``media``, and what a turn is given before its
provider starts in ``turn_context``. ``acp_session`` holds one Codex ACP
session, ``skill_plugins`` hands the library project's skills to Claude, and
``turn_trace`` records each turn's content-free diagnostics for MonkeyMonitor.

``mcp_server`` is the stdio MCP server a CLI starts by its path. Its
``tool_calls`` reach the chat's own Studio, found and verified by
``preparation``, through ``transport``, within what ``studio_tool`` allows;
``judgments`` binds the Agent's judgments to the user's own words,
``visual_review`` holds the Agent's allowance of looks, and ``guides`` are the
words the Agent is given about these tools. ``routes`` serves the chat part of
the Hub API.
"""
