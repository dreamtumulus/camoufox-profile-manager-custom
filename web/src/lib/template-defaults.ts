/** Starting values for a new template: the OS of this computer and a name built from it. */

export type TemplateOs = 'windows' | 'macos' | 'linux'

/**
 * The operating system this interface is running on, from its user agent.
 *
 * Phones and tablets are not offered as template systems, and Android's user
 * agent also says "Linux", so anything that is not a desktop falls back to
 * Windows, the default a template had before detection.
 */
export function detectTemplateOs(userAgent: string | null | undefined): TemplateOs {
  const agent = userAgent ?? ''
  if (/Android|iPhone|iPad|iPod/i.test(agent)) return 'windows'
  if (/Windows/i.test(agent)) return 'windows'
  if (/Macintosh|Mac OS X/i.test(agent)) return 'macos'
  if (/Linux|X11|CrOS/i.test(agent)) return 'linux'
  return 'windows'
}

/** `base`, or the first of "base 2", "base 3"… that no saved template uses. */
export function uniqueTemplateName(base: string, taken: readonly string[]): string {
  const used = new Set(taken.map((name) => name.trim().toLowerCase()))
  if (!used.has(base.toLowerCase())) return base
  for (let index = 2; ; index++) {
    const candidate = `${base} ${index}`
    if (!used.has(candidate.toLowerCase())) return candidate
  }
}
