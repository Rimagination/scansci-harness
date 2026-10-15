"use strict";

const bridge = window.scansciBrowserToolbar;
const address = document.getElementById("address");
for (const name of ["back", "forward", "reload", "expand", "close"]) {
  document.getElementById(name).addEventListener("click", () => bridge.action(name));
}
document.getElementById("address-form").addEventListener("submit", (event) => {
  event.preventDefault();
  bridge.action("go", address.value);
  address.blur();
});
bridge.onState((state) => {
  if (!address.matches(":focus")) address.value = state.url || "";
  document.getElementById("back").disabled = !state.canGoBack;
  document.getElementById("forward").disabled = !state.canGoForward;
  const expand = document.getElementById("expand");
  expand.setAttribute("aria-label", state.expanded ? "还原并排" : "展开浏览器");
  expand.title = state.expanded ? "还原并排" : "展开浏览器";
  if (state.focusAddress) address.focus();
});
bridge.onTheme((palette) => {
  for (const name of ["canvas", "surface", "ink", "muted", "rule", "control", "accent"]) {
    if (/^#[0-9a-f]{6}$/i.test(palette[name])) document.documentElement.style.setProperty(`--${name}`, palette[name]);
  }
});
