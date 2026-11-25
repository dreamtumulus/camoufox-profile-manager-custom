"use client";
import { useEffect, useState } from "react";
import { Modal } from "./modal";
import { useI18n } from "./i18n-provider";
import {
  isStaleWrite,
  OS_LABELS,
  presetsAPI,
  type BrowserSettings,
  type DevicePreset,
} from "@/lib/api";
import { templatesAPI, type ProfileTemplate } from "@/lib/templates-api";
import { extensionsAPI, type ExtensionMetadata } from "@/lib/extensions-api";
import { detectTemplateOs, uniqueTemplateName } from "@/lib/template-defaults";

export function TemplateEditor({
  template,
  existingNames = [],
  onClose,
  onSaved,
  onReload,
}: {
  template: ProfileTemplate | null;
  existingNames?: string[];
  onClose: () => void;
  onSaved: (value: ProfileTemplate) => void;
  onReload: () => void;
}) {
  const { t } = useI18n();
  // A new template starts on this computer's system with a name built from it.
  const [detectedOs] = useState(() =>
    detectTemplateOs(
      typeof navigator === "undefined" ? null : navigator.userAgent,
    ),
  );
  const suggestedName = (os: string | undefined) =>
    uniqueTemplateName(
      t("{os} template", { os: OS_LABELS[os ?? ""] ?? os ?? "" }),
      existingNames,
    );
  const [name, setName] = useState(
    template?.name ?? suggestedName(detectedOs),
  );
  const [notes, setNotes] = useState(template?.notes ?? "");
  const [tags, setTags] = useState(template?.tags.join(", ") ?? "");
  const [settings, setSettings] = useState<BrowserSettings>(
    template?.browser_settings ?? {
      os: detectedOs,
      screen: "1920x1080",
      languages: ["en-US", "en"],
      webrtc_mode: "replace",
      browser_ui: "desktop",
    },
  );
  const [advanced, setAdvanced] = useState(JSON.stringify(settings, null, 2));
  const [fingerprintSource, setFingerprintSource] = useState<"fpgen" | "preset">(
    template?.fingerprint_source ?? "fpgen",
  );
  const [presetId, setPresetId] = useState(template?.fingerprint_preset_id ?? "");
  const [presets, setPresets] = useState<DevicePreset[]>([]);

  const [library, setLibrary] = useState<ExtensionMetadata[]>([]);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [replaceExtensions, setReplaceExtensions] = useState(!template);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  useEffect(() => {
    extensionsAPI
      .list()
      .then((items) => {
        setLibrary(items);
        setSelected(
          new Set(
            items
              .filter((item) =>
                template?.extensions.some(
                  (extension) => extension.sha256 === item.sha256,
                ),
              )
              .map((item) => item.id!),
          ),
        );
      })
      .catch((err) => setLibraryError(String(err)));
  }, [template]);
  useEffect(() => {
    presetsAPI.list().then(setPresets).catch(() => setPresets([]));
  }, []);
  function change(patch: Partial<BrowserSettings>) {
    try {
      const base = JSON.parse(advanced);
      if (!base || typeof base !== "object" || Array.isArray(base))
        throw new Error(t("Browser settings must be a JSON object."));
      const next = { ...base, ...patch };
      setSettings(next);
      setAdvanced(JSON.stringify(next, null, 2));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  async function save() {
    if (!name.trim() || busy || stale) return;
    setBusy(true);
    setError(null);
    try {
      const parsed = JSON.parse(advanced);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
        throw new Error(t("Browser settings must be a JSON object."));
      const draft = {
        name: name.trim(),
        notes: notes || null,
        tags: tags
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        browser_settings: parsed,
        fingerprint_source: fingerprintSource,
        fingerprint_preset_id: fingerprintSource === "preset" ? presetId || null : null,
        ...(replaceExtensions ? { extension_ids: [...selected] } : {}),
      };
      const saved = template
        ? await templatesAPI.update(template, draft)
        : await templatesAPI.create(draft);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStale(isStaleWrite(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      title={t(template ? "Edit template" : "New template")}
      width={660}
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <>
          <button className="btn btn-default" disabled={busy} onClick={onClose}>
            {t("Cancel")}
          </button>
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={
              busy || stale || !name.trim() || (!template && !!libraryError)
            }
          >
            {busy ? t("Saving…") : t("Save template")}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className="block">
          {t("Template name")}
          <input
            data-modal-autofocus
            className="field mt-1"
            maxLength={255}
            value={name}
            onChange={(event) => setName(event.target.value)}
            onFocus={(event) => {
              // Typing replaces the suggested name instead of appending to it.
              if (!template && name === suggestedName(settings.os))
                event.target.select();
            }}
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            {t("Operating system")}
            <select
              className="field mt-1"
              value={settings.os}
              onChange={(event) => {
                // The suggested name follows the system until the user renames it.
                if (!template && name === suggestedName(settings.os))
                  setName(suggestedName(event.target.value));
                setPresetId("");
                change({ os: event.target.value });
              }}
            >
              {["windows", "macos", "linux"].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("Screen resolution")}
            <input
              className="field mt-1"
              value={settings.screen}
              onChange={(event) => change({ screen: event.target.value })}
            />
          </label>
        </div>
        <label className="block">
          {t("Real device fingerprint preset")}
          <select
            className="field mt-1 font-mono"
            value={fingerprintSource === "fpgen" ? "" : presetId || "auto"}
            onChange={(event) => {
              const value = event.target.value;
              setFingerprintSource(value === "" ? "fpgen" : "preset");
              setPresetId(value === "" || value === "auto" ? "" : value);
            }}
          >
            <option value="">{t("fpgen automatic generation")}</option>
            <option value="auto">{t("Real devices · automatically choose unused devices")}</option>
            {presetId && !presets.some((preset) => preset.id === presetId) && (
              <option value={presetId}>{presetId} · {t("Current selection")}</option>
            )}
            {presets.filter((preset) => preset.os === settings.os).map((preset) => (
              <option key={preset.id} value={preset.id}>
                {[preset.id, preset.screen, t("{count} cores", { count: preset.hardware_concurrency }), preset.gpu]
                  .filter(Boolean)
                  .join(" · ")}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-ink-dim">
            {fingerprintSource === "fpgen"
              ? t("Every profile receives a new fpgen sample; samples are checked so no two windows share one.")
              : t("The selected device is used first when available. Every later profile receives a different unused preset for this OS; nothing is copied between windows.")}
          </span>
        </label>
        <label className="block">
          {t("Languages")}
          <input
            className="field mt-1"
            value={(settings.languages ?? []).join(", ")}
            onChange={(event) =>
              change({
                languages: event.target.value
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label>
            {t("Timezone")}
            <input
              className="field mt-1"
              placeholder={t("Follow proxy location")}
              value={settings.timezone ?? ""}
              onChange={(event) =>
                change({ timezone: event.target.value || null })
              }
            />
          </label>
          <label>
            {t("WebRTC")}
            <select
              className="field mt-1"
              value={settings.webrtc_mode ?? "replace"}
              onChange={(event) => change({ webrtc_mode: event.target.value })}
            >
              <option value="replace">{t("Replace")}</option>
              <option value="none">{t("Disable")}</option>
              <option value="real">{t("Real")}</option>
              <option value="forward">{t("Forward")}</option>
            </select>
          </label>
        </div>
        <label className="block">
          {t("Tags")}
          <input
            className="field mt-1"
            value={tags}
            onChange={(event) => setTags(event.target.value)}
          />
          <span className="mt-1 block text-ink-dim">
            {t("Separate tags with commas.")}
          </span>
        </label>
        <label className="block">
          {t("Notes")}
          <textarea
            className="field mt-1 min-h-[70px]"
            maxLength={1000}
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
        <details>
          <summary className="cursor-pointer font-medium">
            {t("All browser settings")}
          </summary>
          <p className="my-2 text-ink-dim">
            {t(
              "Edit browser settings as JSON. Cookies, proxies and pinned fingerprint data are not accepted; the generation policy above is applied independently to every profile.",
            )}
          </p>
          <textarea
            className="field min-h-[180px] font-mono text-[12px]"
            aria-label={t("Browser settings JSON")}
            value={advanced}
            onChange={(event) => setAdvanced(event.target.value)}
          />
        </details>
        <fieldset className="border-t border-line pt-4">
          <legend className="font-medium">{t("Extensions")}</legend>
          {template && (
            <>
              <p className="mb-2 text-ink-dim">
                {template.extensions
                  .map((item) => `${item.name} ${item.version}`)
                  .join(", ") || t("No extensions")}
              </p>
              <label className="flex gap-2">
                <input
                  type="checkbox"
                  checked={replaceExtensions}
                  onChange={(event) =>
                    setReplaceExtensions(event.target.checked)
                  }
                />
                {t("Replace saved extension snapshots")}
              </label>
            </>
          )}
          {replaceExtensions &&
            library.map((item) => (
              <label key={item.id} className="mt-2 flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={selected.has(item.id!)}
                  onChange={() =>
                    setSelected((old) => {
                      const next = new Set(old);
                      if (next.has(item.id!)) next.delete(item.id!);
                      else next.add(item.id!);
                      return next;
                    })
                  }
                />
                <span>
                  {item.name}{" "}
                  <span className="font-mono text-ink-dim">{item.version}</span>
                  <span className="block break-all text-[11px] text-ink-dim">
                    {item.permissions.join(", ") ||
                      t("No declared permissions")}
                  </span>
                </span>
              </label>
            ))}
          {libraryError && (
            <p className="text-danger" role="alert">
              {t("Could not load extension library")}
            </p>
          )}
        </fieldset>
        <p className="text-ink-dim">
          {t(
            "New profiles receive fresh browser data and different device fingerprints. Duplicate samples and duplicate real-device presets are refused.",
          )}
        </p>
        {error && (
          <div role="alert">
            <p className="text-danger">{t(error)}</p>
            {stale && (
              <button className="btn btn-default mt-2" onClick={onReload}>
                {t("Reload saved template")}
              </button>
            )}
          </div>
        )}
      </div>
    </Modal>
  );
}
