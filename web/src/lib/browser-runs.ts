/**
 * Running and stopping a profile's browser, the same wherever a row has the button.
 *
 * The profiles page and the auto-onboarding page both call these, so a launch
 * is asked for, refused and reported in one way. Each page keeps its own
 * state of which rows are running and which are busy.
 */
import { browserCloseFailure, profilesAPI } from './api'
import { copyText } from './clipboard'
import type { Translator } from './i18n'
import type { ProfileSyncBinding } from './sync-api'

type Toast = (kind: 'ok' | 'error' | 'info', title: string, detail?: string) => void

/**
 * Put a row's address on the clipboard.
 *
 * Called straight from the click, before a launch request is awaited: a page
 * may write the clipboard only while it is handling the click, and a launch
 * takes seconds. It never decides the launch; a refused copy is only said.
 */
export function copyEmailOnClick(email: string | null | undefined, t: Translator, toast: Toast): void {
  if (!email) return
  void copyText(email).then((copied) => {
    if (copied) toast('ok', t('Email copied'), email)
    else toast('error', t('Email not copied'), t('Copy it from the Email column by hand: {email}', { email }))
  })
}

/** Ask for a profile's browser. False, with the reason shown, when it did not start. */
export async function launchBrowser(profileId: string, t: Translator, toast: Toast): Promise<boolean> {
  try {
    await profilesAPI.startProfile(profileId)
    return true
  } catch (err) {
    // The manager's own launch refusals are fixed sentences with translations;
    // anything else falls through t() unchanged.
    toast('error', t('Could not launch browser'), err instanceof Error ? t(err.message) : String(err))
    return false
  }
}

/** Close a profile's browser. False, with the reason shown, when its exit could not be confirmed. */
export async function stopBrowser(profileId: string, t: Translator, toast: Toast): Promise<boolean> {
  try {
    await profilesAPI.closeProfile(profileId)
    return true
  } catch (err) {
    toast('error', t('Could not close browser'), browserCloseFailure(err, t) ?? String(err))
    return false
  }
}

/** Whether synchronization keeps this profile from being launched here. */
export function syncBlocksLaunch(binding: ProfileSyncBinding | undefined): boolean {
  return !!binding && (binding.phase !== 'held' || binding.lifecycle?.state === 'trashed' || binding.local_trashed === true)
}
