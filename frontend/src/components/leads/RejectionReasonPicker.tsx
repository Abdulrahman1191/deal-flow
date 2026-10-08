// Canonical claude_agent.UNQUAL_REASON_OPTIONS labels (issue #223), reused
// here (issue #232) to pick which of the four prebuilt rejection templates
// renders -- see backend/app/services/rejection_templates.py for the
// label -> template mapping and precedence. Kept as a plain list in sync
// with the backend by hand, same pattern as ReasonModal's TAGS_BY_BUCKET --
// there's no options endpoint for this fixed, rarely-changing vocabulary.
export const UNQUAL_REASON_OPTIONS = [
  "Founder(s)",
  "Out of our stage",
  "Out of our region",
  "Lack of traction",
  "Dedication and focus",
  "Ownership structure",
  "Market size",
  "Business Model",
  "Regulations and Legislation",
  "Technology and IP",
  "Exit potential",
  "Conflict of interest",
  "Other",
] as const;

// Mirrors claude_agent.INTERNAL_ONLY_REASONS -- judgements about the people,
// never selects a template or reaches the founder's inbox. Still
// selectable: it's still recorded on the card and still drives Copper's
// Unqualification Reasons field, same as every other reason here.
const INTERNAL_ONLY_REASONS = new Set<string>(["Founder(s)", "Dedication and focus", "Ownership structure"]);

const TEMPLATE_LABELS: Record<string, string> = {
  CONFLICT: "Conflict of interest",
  MARKET_SIZE: "Market size / exit potential",
  TRACTION: "Traction / business model",
  MANDATE: "Outside our mandate",
};

// Mirrors MAX_REJECTION_REASONS in backend/app/schemas/assessment.py.
const MAX_REASONS = 3;

interface Props {
  selected: Set<string>;
  onToggle: (label: string) => void;
  language: "en" | "ar";
  onLanguageChange: (language: "en" | "ar") => void;
  templateKey?: string;
  disabled?: boolean;
}

export default function RejectionReasonPicker({
  selected,
  onToggle,
  language,
  onLanguageChange,
  templateKey,
  disabled,
}: Props) {
  return (
    <div className="space-y-2" data-testid="rejection-reason-picker">
      <div className="flex items-center justify-between">
        <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Reason for passing (up to {MAX_REASONS})
        </label>
        <div className="flex items-center gap-1 text-[10px]" role="group" aria-label="Email language">
          {(["en", "ar"] as const).map((code) => (
            <button
              key={code}
              type="button"
              disabled={disabled}
              onClick={() => onLanguageChange(code)}
              data-testid={`language-toggle-${code}`}
              className={`px-2 py-0.5 rounded-full border uppercase transition-colors disabled:opacity-50 ${
                language === code
                  ? "bg-primary/20 text-primary border-primary"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {code}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {UNQUAL_REASON_OPTIONS.map((label) => {
          const isOn = selected.has(label);
          const atCap = !isOn && selected.size >= MAX_REASONS;
          return (
            <button
              key={label}
              type="button"
              disabled={disabled || atCap}
              onClick={() => onToggle(label)}
              title={
                INTERNAL_ONLY_REASONS.has(label)
                  ? "Recorded internally only — never appears in the email"
                  : undefined
              }
              className={`text-xs px-3 py-1.5 rounded-full transition-colors border disabled:opacity-40 ${
                isOn
                  ? "bg-error/20 text-error border-error"
                  : "bg-muted/50 text-muted-foreground border-border hover:text-foreground"
              }`}
            >
              {label}
              {INTERNAL_ONLY_REASONS.has(label) && <span className="ml-1 opacity-60">· internal</span>}
            </button>
          );
        })}
      </div>
      {templateKey && (
        <p className="text-[10px] text-muted-foreground">
          Template: <span className="text-foreground font-medium">{TEMPLATE_LABELS[templateKey] ?? templateKey}</span>
        </p>
      )}
    </div>
  );
}
