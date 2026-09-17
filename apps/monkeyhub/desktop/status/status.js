window.renderDesktopStatus = (status) => {
  document.getElementById("title").textContent = status.title;
  document.getElementById("detail").textContent = status.detail;
  document.getElementById("log").textContent = status.log ? `诊断日志：${status.log}` : "";
  document.title = `MonkeyHub · ${status.title}`;
};
