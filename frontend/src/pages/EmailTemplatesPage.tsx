import { useMemo, useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchEmailTemplates,
  fetchEmailTemplateVersions,
  revertEmailTemplate,
  updateEmailTemplate,
  type EmailTemplate,
  type EmailTemplateVersion,
  type TemplateKey,
  type TemplateLanguage,
} from "../api/emailTemplates";
import { fetchLeads } from "../api/leads";
import type { Lead } from "../types/lead";
import { useMe } from "../lib/auth";
import useAppStore from "../store/useAppStore";
import Modal from "../components/shared/Modal";
import { useToast } from "../components/shared/Toast";

const TEMPLATE_KEYS: TemplateKey[] = ["CONFLICT", "MARKET_SIZE", "TRACTION", "MANDATE"];

const KEY_LABELS: Record<TemplateKey, string> = {
  CONFLICT: "Conflict of interest",
  MARKET_SIZE: "Market size / exit potential",
  TRACTION: "Lack of traction",
  MANDATE: "Outside mandate / other",
};

const LANGUAGES: { value: TemplateLanguage; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ar", label: "Arabic" },
];

const READ_ONLY_TITLE = "Read-only while viewing another user's board";

function templatesQueryKey() {
  return ["email-templates"] as const;
}

function versionsQueryKey(templateId: string) {
  return ["email-template-versions", templateId] as const;
}

/** First token of the lead's first listed founder name, or null -- mirrors
 * backend/app/routers/assessments.py::_lead_first_name so the preview's
 * greeting matches what actually gets sent. */
function leadFirstName(lead: Lead): string | null {
  for (const full of lead.founder_names ?? []) {
    const stripped = (full ?? "").trim();
    if (stripped) return stripped.split(/\s+/)[0];
  }
  return null;
}

/** Mirrors backend/app/services/rejection_templates.py::_greeting_line. */
function greetingLine(firstName: string | null, language: TemplateLanguage): string {
  const name = (firstName ?? "").trim();
  if (language === "ar") return name ? `مرحباً ${name}،` : "مرحباً،";
  return name ? `Hi ${name},` : "Hi there,";
}

function fillPlaceholders(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? values[key] : match));
}

function errorDetail(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err) && typeof err.response?.data?.detail === "string") {
    return err.response.data.detail;
  }
  return fallback;
}

function HistoryModal({
  template,
  onClose,
  onReverted,
  readOnly,
}: {
  template: EmailTemplate;
  onClose: () => void;
  onReverted: (reverted: EmailTemplate) => void;
  readOnly: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();

  const { data: versions = [], isLoading } = useQuery<EmailTemplateVersion[]>({
    queryKey: versionsQueryKey(template.id),
    queryFn: () => fetchEmailTemplateVersions(template.language, template.template_key),
  });

  const revertMutation = useMutation({
    mutationFn: (versionId: string) => revertEmailTemplate(template.language, template.template_key, versionId),
    onSuccess: (reverted) => {
      qc.invalidateQueries({ queryKey: templatesQueryKey() });
      qc.invalidateQueries({ queryKey: versionsQueryKey(template.id) });
      toast("Reverted — the subject, body and reason were restored together.");
      onReverted(reverted);
    },
    onError: (err: unknown) => toast(errorDetail(err, "Couldn't revert — please try again.")),
  });

  return (
    <Modal title={`History — ${KEY_LABELS[template.template_key]} (${template.language.toUpperCase()})`} onClose={onClose}>
      <div className="space-y-4">
        <div className="border border-border rounded-lg p-3">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">Current</p>
          <p className="text-sm text-foreground">
            {template.updated_by ? `Saved by ${template.updated_by}` : "Seeded original"} ·{" "}
            {new Date(template.updated_at).toLocaleString("en-GB")}
          </p>
        </div>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading history…</p>
        ) : versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No prior versions yet — this is the only save on record.</p>
        ) : (
          <ul className="space-y-2">
            {versions.map((v) => (
              <li
                key={v.id}
                className="flex items-start justify-between gap-3 border border-border rounded-lg p-3"
              >
                <div className="text-sm">
                  <p className="text-foreground">{v.saved_by ? `Saved by ${v.saved_by}` : "Seeded original"}</p>
                  <p className="text-xs text-muted-foreground">{new Date(v.saved_at).toLocaleString("en-GB")}</p>
                </div>
                <button
                  onClick={() => revertMutation.mutate(v.id)}
                  disabled={revertMutation.isPending || readOnly}
                  title={readOnly ? READ_ONLY_TITLE : undefined}
                  className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-lg bg-muted hover:bg-border text-foreground transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {revertMutation.isPending ? "Reverting…" : "Revert to this"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}

function TemplateBlock({
  template,
  previewLead,
  partnerName,
  readOnly,
}: {
  template: EmailTemplate;
  previewLead: Lead | null;
  partnerName: string;
  readOnly: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const isAr = template.language === "ar";

  const [subject, setSubject] = useState(template.subject);
  const [body, setBody] = useState(template.body);
  const [reasonText, setReasonText] = useState(template.reason_text);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const isDirty = subject !== template.subject || body !== template.body || reasonText !== template.reason_text;

  const saveMutation = useMutation({
    mutationFn: () => updateEmailTemplate(template.language, template.template_key, { subject, body, reason_text: reasonText }),
    onSuccess: () => {
      setSaveError(null);
      qc.invalidateQueries({ queryKey: templatesQueryKey() });
      qc.invalidateQueries({ queryKey: versionsQueryKey(template.id) });
      toast(`Saved ${KEY_LABELS[template.template_key]} (${template.language.toUpperCase()}).`);
    },
    onError: (err: unknown) => setSaveError(errorDetail(err, "Save failed — please try again.")),
  });

  const revert = (reverted: EmailTemplate) => {
    setSubject(reverted.subject);
    setBody(reverted.body);
    setReasonText(reverted.reason_text);
    setSaveError(null);
    setShowHistory(false);
  };

  const preview = useMemo(() => {
    if (!previewLead) return null;
    const values = {
      greeting_line: greetingLine(leadFirstName(previewLead), template.language),
      company: previewLead.company_name,
      reason: reasonText,
      partner_name: partnerName,
    };
    return {
      subject: fillPlaceholders(subject, values),
      body: fillPlaceholders(body, values),
    };
  }, [previewLead, subject, body, reasonText, partnerName, template.language]);

  const dir = isAr ? "rtl" : "ltr";

  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-foreground">
          {KEY_LABELS[template.template_key]} — {template.language.toUpperCase()}
        </h3>
        <button
          onClick={() => setShowHistory(true)}
          className="text-xs font-medium text-info hover:underline"
        >
          History
        </button>
      </div>

      <p className="text-xs text-muted-foreground">
        Placeholders for the body: <code>{"{greeting_line}"}</code> <code>{"{company}"}</code>{" "}
        <code>{"{reason}"}</code> <code>{"{partner_name}"}</code>. <code>{"{greeting_line}"}</code> expands to a
        greeting like "Hi Sara," or "Hi there," (Arabic equivalent for AR blocks) depending on whether the lead has
        a first name on file.
      </p>

      <label className="block">
        <span className="text-xs font-medium text-muted-foreground">Subject</span>
        <input
          dir={dir}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={readOnly}
          title={readOnly ? READ_ONLY_TITLE : undefined}
          className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring disabled:opacity-60"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium text-muted-foreground">Body</span>
        <textarea
          dir={dir}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          disabled={readOnly}
          title={readOnly ? READ_ONLY_TITLE : undefined}
          rows={8}
          className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring font-mono disabled:opacity-60"
        />
      </label>

      <label className="block">
        <span className="text-xs font-medium text-muted-foreground">Reason text (fills {"{reason}"})</span>
        <textarea
          dir={dir}
          value={reasonText}
          onChange={(e) => setReasonText(e.target.value)}
          disabled={readOnly}
          title={readOnly ? READ_ONLY_TITLE : undefined}
          rows={3}
          className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring disabled:opacity-60"
        />
      </label>

      {saveError && (
        <p className="text-xs text-error bg-error/10 rounded-lg px-3 py-2">{saveError}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          onClick={() => saveMutation.mutate()}
          disabled={!isDirty || saveMutation.isPending || readOnly}
          title={readOnly ? READ_ONLY_TITLE : undefined}
          className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saveMutation.isPending ? "Saving…" : "Save"}
        </button>
        {isDirty && !saveMutation.isPending && (
          <span className="text-xs text-muted-foreground">Unsaved changes</span>
        )}
      </div>

      <div className="border border-dashed border-border rounded-lg p-3">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Preview</p>
        {!preview ? (
          <p className="text-sm text-muted-foreground">Pick a lead above to preview this template.</p>
        ) : (
          <div dir={dir} className="space-y-1">
            <p className="text-sm font-semibold text-foreground">{preview.subject}</p>
            <p className="text-sm text-foreground whitespace-pre-wrap">{preview.body}</p>
          </div>
        )}
      </div>

      {showHistory && (
        <HistoryModal
          template={template}
          onClose={() => setShowHistory(false)}
          onReverted={revert}
          readOnly={readOnly}
        />
      )}
    </div>
  );
}

export default function EmailTemplatesPage() {
  const readOnly = !!useAppStore((s) => s.viewAs);
  const me = useMe();

  const { data: templates = [], isLoading, isError, error } = useQuery<EmailTemplate[]>({
    queryKey: templatesQueryKey(),
    queryFn: fetchEmailTemplates,
  });

  const { data: leadsPage } = useQuery({
    queryKey: ["email-template-preview-leads"],
    queryFn: () => fetchLeads({ bucket: "REJECT", page_size: 100, sort: "newest" }),
    enabled: !isError,
  });
  const previewLeads = leadsPage?.items ?? [];

  const [selectedLeadId, setSelectedLeadId] = useState("");
  const previewLead = previewLeads.find((l) => l.id === selectedLeadId) ?? null;

  const partnerName = me.data?.name || me.data?.email || "Partner";

  if (isLoading) return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Loading templates…</p>;
  if (isError) {
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 403) {
      return <p className="p-4 sm:p-6 text-sm text-muted-foreground">Email templates is admin-only.</p>;
    }
    return <p className="p-4 sm:p-6 text-sm text-error">Failed to load email templates.</p>;
  }

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Email templates</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Edit the rejection copy sent for each reason, in English and Arabic. Each block saves on its own —
          saving one doesn't touch the others.
        </p>
        {readOnly && (
          <p className="text-xs text-warning mt-1">{READ_ONLY_TITLE}.</p>
        )}
      </div>

      <div className="bg-card border border-border rounded-xl p-4">
        <label className="block max-w-md">
          <span className="text-xs font-medium text-muted-foreground">Preview against a lead</span>
          <select
            value={selectedLeadId}
            onChange={(e) => setSelectedLeadId(e.target.value)}
            className="mt-1 w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring"
          >
            <option value="">Select a REJECT lead…</option>
            {previewLeads.map((l) => (
              <option key={l.id} value={l.id}>
                {l.company_name}
              </option>
            ))}
          </select>
          <span className="block mt-1 text-xs text-muted-foreground">
            Drives every preview below — unsaved edits included, so it's a real preview rather than a read-back.
          </span>
        </label>
      </div>

      {TEMPLATE_KEYS.map((key) => {
        const rows = LANGUAGES.map((l) => templates.find((t) => t.template_key === key && t.language === l.value));
        return (
          <section key={key} className="space-y-3">
            <h2 className="text-sm font-semibold text-foreground">{KEY_LABELS[key]}</h2>
            <div className="grid sm:grid-cols-2 gap-4">
              {rows.map((row, i) =>
                row ? (
                  <TemplateBlock
                    key={`${row.id}-${row.updated_at}`}
                    template={row}
                    previewLead={previewLead}
                    partnerName={partnerName}
                    readOnly={readOnly}
                  />
                ) : (
                  <p key={LANGUAGES[i].value} className="text-sm text-muted-foreground">
                    No {LANGUAGES[i].label} row seeded for {KEY_LABELS[key]}.
                  </p>
                ),
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
