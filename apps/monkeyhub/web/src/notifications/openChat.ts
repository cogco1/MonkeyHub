/**
 * Opening a conversation from a notice (#300).
 *
 * The page that shows conversations answers `monkeyhub:open-chat` by opening
 * `detail.chatId` in place and calling `event.preventDefault()`. When nothing
 * on this page has done so within 300 ms, the Hub is loaded on that
 * conversation instead, through the `?chatId=` route it already reads.
 */

export const OPEN_CHAT_EVENT = "monkeyhub:open-chat";
export const OPEN_CHAT_WAIT_MS = 300;

export interface OpenChatDetail {
  chatId: string;
  /** The conversation's project folder, so a listener can open it without another read. */
  projectDir: string;
}

export function openChat(chatId: string, projectDir: string): void {
  // Cancelable, or a listener's preventDefault() would not register.
  const event = new CustomEvent<OpenChatDetail>(OPEN_CHAT_EVENT, { detail: { chatId, projectDir }, cancelable: true });
  window.dispatchEvent(event);
  if (event.defaultPrevented) return;
  window.setTimeout(() => {
    if (event.defaultPrevented) return;
    const url = new URL(window.location.href);
    // Already showing it in the conversation view: nothing to load.
    if (url.searchParams.get("chatId") === chatId && url.searchParams.get("view") !== "fab") return;
    // A tool route would restore its own project over the conversation, and
    // Fabrication shows no conversation: load the conversation alone.
    url.searchParams.delete("runtimeId");
    url.searchParams.delete("view");
    url.searchParams.set("chatId", chatId);
    window.location.assign(url.toString());
  }, OPEN_CHAT_WAIT_MS);
}
