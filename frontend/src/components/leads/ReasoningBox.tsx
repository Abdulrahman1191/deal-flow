import { useMemo, useState } from "react";
import { extractSignalLabel } from "../../lib/extractSignalLabel";

interface Props {
  positive_signals: string[] | null;
  red_flags: string[] | null;
  data_gaps: string[] | null;
}

const VISIBLE_COUNT = 3;

const toneDot: Record<Tone, string> = {
  success: "bg-success",
  error: "bg-error",
  warning: "bg-warning",
};

type Tone = "success" | "error" | "warning";

export default function ReasoningBox({ positive_signals, red_flags, data_gaps }: Props) {
  const [showMore, setShowMore] = useState(false);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const signals = positive_signals ?? [];
  const flags = red_flags ?? [];
  const gaps = data_gaps ?? [];

  const extraSignals = Math.max(0, signals.length - VISIBLE_COUNT);
  const extraFlags = Math.max(0, flags.length - VISIBLE_COUNT);
  const gapsCount = gaps.length;

  const visibleSignalCount = showMore ? signals.length : Math.min(signals.length, VISIBLE_COUNT);
  const visibleFlagCount = showMore ? flags.length : Math.min(flags.length, VISIBLE_COUNT);

  const toggleRow = (key: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const footerParts = [
    gapsCount > 0 ? `${gapsCount} data gap${gapsCount === 1 ? "" : "s"}` : null,
    extraSignals > 0 ? `${extraSignals} more signal${extraSignals === 1 ? "" : "s"}` : null,
    extraFlags > 0 ? `${extraFlags} more flag${extraFlags === 1 ? "" : "s"}` : null,
  ].filter((part): part is string => part !== null);

  return (
    <div className="space-y-2 text-xs">
      {signals.slice(0, visibleSignalCount).map((text, i) => (
        <SignalRow
          key={`signal-${i}`}
          text={text}
          tone="success"
          expanded={expandedRows.has(`signal-${i}`)}
          onToggle={() => toggleRow(`signal-${i}`)}
        />
      ))}
      {flags.slice(0, visibleFlagCount).map((text, i) => (
        <SignalRow
          key={`flag-${i}`}
          text={text}
          tone="error"
          expanded={expandedRows.has(`flag-${i}`)}
          onToggle={() => toggleRow(`flag-${i}`)}
        />
      ))}
      {showMore &&
        gaps.map((text, i) => (
          <SignalRow
            key={`gap-${i}`}
            text={text}
            tone="warning"
            expanded={expandedRows.has(`gap-${i}`)}
            onToggle={() => toggleRow(`gap-${i}`)}
          />
        ))}
      {footerParts.length > 0 && (
        <button
          type="button"
          onClick={() => setShowMore((v) => !v)}
          className="text-[11px] text-muted-foreground hover:text-foreground hover:underline"
          data-testid="reasoning-more-toggle"
        >
          {showMore ? "Show less ▲" : `${footerParts.join(" · ")} ▼`}
        </button>
      )}
    </div>
  );
}

function SignalRow({
  text,
  tone,
  expanded,
  onToggle,
}: {
  text: string;
  tone: Tone;
  expanded: boolean;
  onToggle: () => void;
}) {
  const { label, detail } = useMemo(() => extractSignalLabel(text), [text]);
  return (
    <div data-testid="signal-row">
      <button type="button" onClick={onToggle} className="flex items-start gap-2 w-full text-left">
        <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${toneDot[tone]}`} />
        <span className="text-foreground flex-1" dir="auto">
          {label}
        </span>
      </button>
      {expanded && (
        <p className="pl-3.5 mt-1 text-muted-foreground leading-relaxed" dir="auto">
          {detail}
        </p>
      )}
    </div>
  );
}
