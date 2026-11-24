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
