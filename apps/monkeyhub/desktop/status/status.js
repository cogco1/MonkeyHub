window.renderDesktopStatus = (status) => {
  document.getElementById("title").textContent = status.title;
  document.getElementById("detail").textContent = status.detail;
  document.getElementById("log").textContent = status.log ? `诊断日志：${status.log}` : "";
  document.title = `MonkeyHub · ${status.title}`;
};

// #354: the Windows 11 title row. The desktop host defines this bridge before any page
// script runs and pushes hover, press, maximized and active state; a browser never has it.
(() => {
  const shell = window.__monkeyhubDesktop;
  const bar = document.querySelector(".titlebar");
  if (!bar || !shell || shell.version !== 1 || typeof shell.subscribe !== "function") return;
  const root = document.documentElement;
  const paint = (state) => {
    const on = Boolean(state && state.titleBar);
    bar.hidden = !on;
    root.toggleAttribute("data-desktop-titlebar", on);
    if (!on) return;
    root.style.setProperty("--desktop-titlebar-height", `${shell.titleBarHeight}px`);
    root.style.setProperty("--desktop-caption-button-width", `${shell.captionButtonWidth}px`);
    root.style.setProperty("--desktop-caption-width", `${shell.captionButtonWidth * shell.captionButtons}px`);
    bar.dataset.active = String(state.active);
    bar.dataset.maximized = String(state.maximized);
    for (const button of bar.querySelectorAll(".titlebar__button")) {
      button.dataset.hover = String(state.hover === button.dataset.button);
      button.dataset.pressed = String(state.pressed === button.dataset.button);
    }
  };
  paint(shell.state);
  shell.subscribe(paint);
})();
