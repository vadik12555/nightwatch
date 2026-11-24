// Small progressive enhancements shared by all pages. No framework needed.
(() => {
  "use strict";

  // <button data-copy="#selector"> copies the text of the target element.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const target = document.querySelector(button.dataset.copy);
    if (!target) return;

    const text = target.textContent.trim();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API needs HTTPS or localhost: fall back to selecting the text.
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("copy");
    }
    const original = button.textContent;
    button.textContent = "Скопировано ✓";
    setTimeout(() => { button.textContent = original; }, 1800);
  });

  // <form data-confirm="Question?"> asks before submitting destructive actions.
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-confirm]");
    if (form && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
    }
  });
})();

// --- AI helpers (stage 3) -------------------------------------------------------------------
(() => {
  "use strict";

  // Suggestion chips fill the chat textarea.
  document.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-fill]");
    if (!chip) return;
    const textarea = document.querySelector("textarea[name=question]");
    if (textarea) {
      textarea.value = chip.dataset.fill;
      textarea.focus();
    }
  });

  // Enter sends, Shift+Enter makes a new line.
  document.addEventListener("keydown", (event) => {
    const textarea = event.target.closest("textarea[data-submit-on-enter]");
    if (textarea && event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (textarea.value.trim()) textarea.form.requestSubmit();
    }
  });

  // Close the AI drawer with its ✕ button or Escape.
  const closeDrawer = () => {
    const panel = document.getElementById("ai-panel");
    if (panel) panel.innerHTML = "";
  };
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-close-drawer]")) closeDrawer();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });

  // Keep the chat scrolled to the newest message.
  const scrollChat = () => {
    const log = document.getElementById("chat-log");
    if (log) log.scrollTop = log.scrollHeight;
  };
  document.addEventListener("htmx:afterSwap", scrollChat);
  window.addEventListener("load", scrollChat);
})();
