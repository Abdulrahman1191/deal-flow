import client from "./client";

export type TemplateLanguage = "en" | "ar";
export type TemplateKey = "CONFLICT" | "MARKET_SIZE" | "TRACTION" | "MANDATE";

export interface EmailTemplate {
  id: string;
  language: TemplateLanguage;
  template_key: TemplateKey;
  subject: string;
  body: string;
  reason_text: string;
  updated_by: string | null;
  updated_at: string;
}

export interface EmailTemplateVersion {
  id: string;
  subject: string;
  body: string;
  reason_text: string;
  saved_by: string | null;
  saved_at: string;
}

export interface EmailTemplateUpdate {
  subject: string;
  body: string;
  reason_text: string;
}

export const fetchEmailTemplates = () =>
  client.get<EmailTemplate[]>("/email-templates").then((r) => r.data);

export const updateEmailTemplate = (
  language: TemplateLanguage,
  templateKey: TemplateKey,
  body: EmailTemplateUpdate,
) =>
  client
    .put<EmailTemplate>(`/email-templates/${language}/${templateKey}`, body)
    .then((r) => r.data);

export const fetchEmailTemplateVersions = (language: TemplateLanguage, templateKey: TemplateKey) =>
  client
    .get<EmailTemplateVersion[]>(`/email-templates/${language}/${templateKey}/versions`)
    .then((r) => r.data);

export const revertEmailTemplate = (
  language: TemplateLanguage,
  templateKey: TemplateKey,
  versionId: string,
) =>
  client
    .post<EmailTemplate>(`/email-templates/${language}/${templateKey}/revert/${versionId}`)
    .then((r) => r.data);
