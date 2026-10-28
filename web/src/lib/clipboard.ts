/**
 * Copy text to the clipboard from a click handler.
 *
 * `navigator.clipboard` exists only in a secure context (https, or
 * localhost). The hub is normally opened as plain http on the LAN
 * (`http://<hub-host>:8765/app/`), where it is undefined, so every copy
 * button would fail there while working fine on the hub machine itself.
 * The fallback is the old selection + `execCommand("copy")` path, which
 * browsers still honour inside a user gesture.
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // permission denied or document not focused: try the fallback
    }
  }
  return copyViaSelection(text);
}

function copyViaSelection(text: string): boolean {
  if (typeof document === "undefined" || typeof document.execCommand !== "function") return false;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  // off-screen but still selectable; display:none would make it unselectable
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  const active = document.activeElement as HTMLElement | null;
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  area.remove();
  active?.focus?.();
  return ok;
}
