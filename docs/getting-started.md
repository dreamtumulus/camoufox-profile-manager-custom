# Getting started

[Русский](getting-started.ru.md)

## Download and open

Open the [releases page](https://github.com/dreamtumulus/camoufox-profile-manager-custom/releases)
and choose a desktop installer from a release's **Assets**. Python, Node.js and a
terminal are unnecessary for desktop packages. Installer builds are attached by
the release workflow; older releases may only contain Python packages.

| Computer | File | Installation |
| --- | --- | --- |
| Mac with Apple Silicon (M1 or newer) | `camoufox-pm-macos-arm64.dmg` | Open the disk image, drag the app into Applications, then open it there. |
| Mac with Intel processor | `camoufox-pm-macos-x86_64.dmg` | Open the disk image, drag the app into Applications, then open it there. |
| Windows 10/11, 64-bit Intel/AMD | `camoufox-pm-windows-x86_64-setup.exe` | Run the installer, then open the app from the Start menu. Installs for your account. |
| Ubuntu 22.04+ or compatible Debian, 64-bit Intel/AMD | `camoufox-pm-linux-x86_64.deb` | Open with the system software installer, then launch from Applications. |

If Microsoft WebView2 is missing on Windows, the installer downloads it through
Microsoft’s official runtime bootstrapper; this needs internet access. If WebView2
is still unavailable, fix the connection or ask your administrator, then choose
**Retry**. **Cancel** stops before replacing the manager’s existing files.

Linux also has a portable `.tar.gz`: extract the complete folder and open its
`camoufox-pm` executable. Keep the `_internal` directory beside it. The desktop
webview needs system graphics libraries; use the `.deb` where possible because
it declares those dependencies. Other Linux distributions have not been validated.

Project installers are unsigned, and macOS builds are not notarized. Certificates
and notarization are not planned or required for release. Download from the
official release and compare the SHA-256 sidecar before deciding to open it;
Gatekeeper or SmartScreen may display a warning. Managed
computers may require your administrator's approval. Do not turn off your system's
security features. See [signing status and limitations](signing.md).

## First profile

The upcoming **0.7.0** adds Russian UI and the shorter setup checklist described
below; the current 0.6.0 installers have English and Simplified Chinese. Choose
**Language** at the bottom of the sidebar (also available before login). The
manager remembers your choice when local storage is available. Interface language
never changes a profile's fingerprint languages, timezone, cookies or settings.

1. Open **赵露思 上号系统**. It keeps your data separately from the installed app.
2. The matching Camoufox browser is included and provisioned automatically on
   first launch. Settings retains a repair option for an incomplete installation.
3. Choose **Create first profile** or **New profile**. Give it a name you recognize.
4. Leave the advanced fingerprint settings at their defaults. Add a proxy only if
   you already have one; check it before relying on its location.
5. Click **Run**. Your profile opens in a separate Camoufox browser window. Close
   that window or click **Stop** when finished. Closing the manager also stops its
   running browser sessions.

Your profile retains its cookies, history and fingerprint between launches. A
clone represents a different machine by default. Regenerating a fingerprint
changes the apparent machine: read the confirmation before doing it.

If browser installation fails, check the connection, free disk space and write
permissions, then choose **Retry installation**. You can create a profile while
the browser downloads, but launching requires a completed installation. Dismissing
the setup banner does not remove **Settings → Browser setup**.

If setup reports a GitHub request limit (HTTP 403/429), wait before retrying or
expand **GitHub download authentication (optional)** in Browser setup. Create a
personal access token with public repository access, paste it into **GitHub
token**, then retry installation. No write or private repository access is
needed. The manager uses the token only for that attempt's GitHub API release
lookup; it is not saved or sent to archive, add-on or location-data downloads.
The field clears when installation starts. A later retry needs the token again.
An invalid/expired token, a permission refusal and a rate limit have separate
messages; authentication cannot remove all GitHub or network restrictions.
See [GitHub authentication](https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api)
and [public release access](https://docs.github.com/en/rest/releases/releases#list-releases).
Source installations can also use upstream's `GITHUB_TOKEN` environment variable.

For a proxy, select its protocol and enter `host:port` in the Server field;
enter its username and password in their own fields. SOCKS5 authentication is
supported. HTTP CONNECT and TLS-to-proxy HTTPS are different protocols: use the
provider's documented type, without silently downgrading HTTPS. Use **Check proxy**
before launch. A responding proxy can still need location resources: choose
**Settings → Browser setup → Retry download** if the diagnostic asks for them.
See the [protocol and authentication guide](proxy-support.md).

In **Proxy library**, select saved proxies and choose **Batch check**. With no
selection, the current search results are used. Review the listed proxies before
starting; one batch accepts up to 100 proxies with one to four simultaneous
checks. Closing the dialog cancels waiting checks, while checks already started
may finish and save their results to the library.

## Protect and recover your data

To keep a copy of a profile, open its menu in the profile list and choose
**Export…**. The archive holds the profile's settings, pinned fingerprint and
browser data. Stop the profile first, and keep the file outside this computer.
**Import a profile archive** in the profile list brings it back as a separate
profile, so the current one is preserved.

The manager makes no copies on its own. Earlier versions backed up every profile
once a day into the data folder's `backups` directory; that feature was removed
to save disk space, and the archives it left are deleted the next time the app
starts. Export the profiles you want a copy of.

Deleting a profile moves it to **Trash**. Use **Restore** there to bring it back.
**Delete permanently** asks you to type its name and removes its browser data;
recovery then requires an archive you exported earlier. Protect archives as
carefully as the profiles: they are unencrypted and can contain signed-in
sessions, cookies, history and proxy passwords. Profile archives do not carry
login accounts, sessions for this manager, or global application settings.
Importing a profile archive does not require the original encryption key;
imported proxy passwords are encrypted with the current key. Keep the original
database and key when preserving the whole installation.
Trash has no automatic expiry; restoring keeps profile schedules paused.

**Settings → App updates → Check for updates** checks GitHub for a newer release.
Stop running profiles before preparing an update.
**Prepare update** offers the release downloads; it copies no profile data.
Choose the installer for your computer. The release link is checked to belong to this project; this is not
a cryptographic verification of the installer. Download it, close the manager, and install
it over the existing app. The app does not replace its executable automatically.

## Where data lives

The desktop app uses the same folder regardless of where you launch it:

| System | Default data folder |
| --- | --- |
| macOS | `~/Library/Application Support/Camoufox Profile Manager/` |
| Windows | `%LOCALAPPDATA%\Camoufox Profile Manager\` |
| Linux | `$XDG_DATA_HOME/camoufox-profile-manager/` or `~/.local/share/camoufox-profile-manager/` |

Keep the whole folder, including `profiles.db`, browser data and
`secret.key`. A fresh desktop install generates this encryption key; losing it
can make encrypted proxy passwords unreadable. Reinstalling the app or uninstalling
the Windows program keeps the separate data folder. CLI/source installations
continue to use `data/profiles.db` unless configured otherwise; their existing
data is not silently moved into the desktop folder.

Most of a used profile's folder is cache the browser can rebuild, so the manager
keeps it bounded. Each browser may keep at most 50 MB of cached pages
(`CPM_BROWSER_CACHE_MB`; `0` removes the limit), and its startup cache is deleted
when it closes, which makes the next start slightly slower. The first start
after upgrading also trims closed profiles that grew before there was a limit.
Cookies, logins, site storage and history are not part of any cache and are
never touched by this.

The manager's server listens on this computer only and chooses an available
local port. If it says **already open**, switch to its existing window. For a
startup failure, `desktop.log` in the data folder records output from packaged
windowed builds. Do not share logs or profile archives publicly without checking for
private information.

## From source (developers)

Install [uv](https://docs.astral.sh/uv/) and Node.js 20.9+; uv manages Python.

```bash
git clone https://github.com/polyackiy/camoufox-profile-manager.git
cd camoufox-profile-manager
uv sync --extra desktop
uv run python scripts/build_webui.py
uv run python -m camoufox_pm.desktop
```

On Linux, source desktop mode also needs a supported pywebview GUI backend and
its system libraries. The packaged `.deb` bundles the Qt backend. For a web tab
instead, use `uv sync`, build the UI, and run `uv run camoufox-pm`.
[CLI options](cli.md) and [profile settings](profile-settings.md) cover advanced use.
