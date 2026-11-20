/** Reading a pasted list of addresses and handing them to the profiles whose proxy passed. */

export const MAX_EMAIL_IMPORT = 100

// The same shape the server accepts (core/models.py normalize_email).
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export type EmailProblem =
  | { line: number; email: string; reason: 'invalid' | 'repeated' }
  | { line: number; email: string; reason: 'taken'; holder: string; inTrash: boolean }

export type ParsedEmails = {
  /** Nonempty lines, whether or not they can be used. */
  lines: number
  /** Addresses that can be imported, in the order they were pasted. */
  emails: string[]
  problems: EmailProblem[]
}

export type EmailHolder = { name: string; inTrash: boolean }

/** How two addresses are compared: the same mailbox whatever its capitals. */
export function emailKey(email: string): string {
  return email.normalize('NFC').trim().toLowerCase()
}

/**
 * One address per line. A line that cannot be imported is reported with its
 * number and left out; the rest stay usable, so one typo does not block a list.
 */
export function parseEmailLines(text: string, holders: ReadonlyMap<string, EmailHolder> = new Map()): ParsedEmails {
  const seen = new Set<string>()
  const emails: string[] = []
  const problems: EmailProblem[] = []
  let lines = 0
  text.split(/\r?\n/).forEach((raw, index) => {
    const email = raw.normalize('NFC').trim()
    if (!email) return
    lines += 1
    const line = index + 1
    const key = emailKey(email)
    const holder = holders.get(key)
    if (email.length > 254 || !EMAIL.test(email) || /\p{C}/u.test(email)) problems.push({ line, email, reason: 'invalid' })
    else if (holder) problems.push({ line, email, reason: 'taken', holder: holder.name, inTrash: holder.inTrash })
    else if (seen.has(key)) problems.push({ line, email, reason: 'repeated' })
    else {
      seen.add(key)
      emails.push(email)
    }
  })
  return { lines, emails, problems }
}

/**
 * Hand the addresses out in list order to the profiles whose proxy passed.
 *
 * A profile that did not pass is stepped over without using an address up, so
 * the next one that passed takes it. Addresses nobody took come back as
 * `unused`: nothing is recorded for them and they can be imported again.
 */
export function assignEmails(targetIds: readonly string[], passed: ReadonlySet<string>, emails: readonly string[]): {
  assigned: Map<string, string>
  unused: string[]
} {
  const assigned = new Map<string, string>()
  let next = 0
  for (const id of targetIds) {
    if (next >= emails.length) break
    if (passed.has(id) && !assigned.has(id)) assigned.set(id, emails[next++])
  }
  return { assigned, unused: emails.slice(next) }
}
