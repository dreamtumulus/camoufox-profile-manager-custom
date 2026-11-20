/**
 * Copy text to the clipboard. Call it directly from a click handler, before
 * anything is awaited: a page may write the clipboard only while it is handling
 * a person's gesture.
 *
 * The selection route goes first because it finishes inside that gesture. The
 * asynchronous clipboard API is stricter in the desktop app's embedded web view
 * than in a browser, and trying it first would leave the fallback to run after
 * the gesture is over, when it is refused too.
 */
export function copyText(value: string): Promise<boolean> {
  if (copyBySelection(value)) return Promise.resolve(true)
  const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard
  if (!clipboard?.writeText) return Promise.resolve(false)
  return clipboard.writeText(value).then(() => true, () => false)
}

function copyBySelection(value: string): boolean {
  if (typeof document === 'undefined') return false
  const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const area = document.createElement('textarea')
  area.value = value
  area.readOnly = true
  area.setAttribute('aria-hidden', 'true')
  area.tabIndex = -1
  // Off screen but still selectable; display:none would copy nothing.
  area.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0'
  document.body.appendChild(area)
  let copied = false
  try {
    area.select()
    area.setSelectionRange(0, value.length)
    copied = document.execCommand('copy')
  } catch {
    copied = false
  } finally {
    area.remove()
    focused?.focus({ preventScroll: true })
  }
  return copied
}
