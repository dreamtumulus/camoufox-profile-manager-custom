# Profile email addresses

A profile can carry the email address of the account it signs in with. The
profile list shows it in the **Email** column, after the name, and the search box
matches it along with the name and ID.

An address is written in one way only: **Import emails**, which checks each
profile's proxy first. Editing a profile never sets one, and a profile made from
another starts without one: a clone, a profile created from a template or a
spreadsheet, one imported from an archive or restored from a backup, and one
received through device synchronization. An exported archive or backup does
contain the source profile's address in its record, so treat those files as you
would the list itself.

## Only profiles created since this feature

Profiles that were already in the database when it was upgraded to a version
with email addresses never receive one. The upgrade marks every row it finds,
once, and nothing changes that mark afterwards: not an edit, a new proxy or a
fresh proxy check. Profiles created from then on can receive an address,
whichever way they are created (the form, a template or a batch, a spreadsheet,
a clone, an archive or backup, device synchronization). A clone or a restored
backup of an older profile is a new profile, so it can.

The import does not offer older profiles as targets and says how many it left
out; the server refuses them as well. The API reports the mark as
`email_allowed` on each profile.

## Import emails

Administrators open **Import emails** above the profile list.

1. **Paste addresses**, one per line, up to 100. Lines that are not addresses,
   repeat an earlier line, or name an address a profile already holds (in Trash
   too) are reported by line number and left out; the rest stay usable.
2. **Check proxies.** The proxy of each target profile is checked, a few at a
   time. Nothing has been written yet, and cancelling writes nothing. Checking
   stops early once enough profiles have passed to take every address.
3. **Confirm import.** The dialog shows which profile receives which address.
   Only confirming writes them.

Which profiles are targets:

- With rows selected, the selected profiles that have no address.
- With no selection, the profiles in the current (filtered) list that have no
  address. One import covers the first 100.
- In both cases, only profiles created since this feature (see above).

Addresses are handed out in list order to the profiles whose proxy passed. The
list starts newest first, so unless it is sorted otherwise the most recently
created profile takes the first address. A
profile that did not pass, or has no proxy, is skipped without using an address
up: the next profile that passed takes it. Addresses nobody took are listed with
a **Copy addresses** button; they are recorded nowhere and can be imported again.

A profile that already has an address is never a target and is never changed.

## What the server enforces

The dialog is a convenience; the rule is applied again when the addresses are
written, whatever sent the request. A profile is skipped, with the reason, unless
all of these hold:

- it exists, is not in Trash, and has no address yet;
- it was created since email addresses were introduced (`email_allowed`);
- it has a proxy, and that proxy's last check succeeded;
- that check is at most 30 minutes old (changing a proxy clears its check, so a
  check always belongs to the proxy the profile has now);
- the profile was not edited since the dialog read it;
- no other profile, in Trash or not, holds the address. Capital letters do not
  make two addresses different.

## Correcting an address

The import never replaces an address. To correct one, an administrator chooses
**Clear email** in the profile's row menu, then imports the right address.

## Run copies the address

**Run** on a profile that has an address copies it to the clipboard as the
browser starts, ready to paste into the sign-in page, and says so in a
notification. The copy happens at the click, not after the launch, because a
page may write the clipboard only while it is handling the click.

- A profile without an address leaves the clipboard alone.
- Starting several profiles at once with **Launch** copies nothing.
- If the address cannot be copied the browser still starts, and the notification
  shows the address so it can be copied by hand.

## Double-click copies the address

Double-clicking a row's **Email** cell copies that address and says so in a
notification, without starting the browser. A row without an address has
nothing to copy and leaves the clipboard alone.

## Spreadsheets

The Excel export has an **Email** column, last in the sheet. It is read-only: an
import ignores it, since a spreadsheet would otherwise go around the proxy check.

## API

All routes use `/api/v1`, require an administrator and return the standard
`ApiResponse` envelope. Profile responses include `email` (or `null`) and
`email_allowed`; neither is accepted by create or update.

| Method and path | Request / result |
| --- | --- |
| `POST /profiles/emails` | `{ "assignments": [{ "id", "row_version", "email" }] }`, 1–100 entries, each profile and each address at most once; returns `updated` profiles and `skipped` ID/reason pairs |
| `POST /profiles/emails/taken` | `{ "emails": [...] }`; returns `taken`: `email`, `profile_id`, `profile_name`, `in_trash` for each address a profile holds |
| `DELETE /profiles/{id}/email?row_version=N` | Removes the address; returns the profile. A stale version is 409 |

A malformed request (an invalid address, a repeated profile or address) is 422
and writes nothing. A well-formed one writes what it may and reports the rest in
`skipped`, so one refused profile does not discard the others.
