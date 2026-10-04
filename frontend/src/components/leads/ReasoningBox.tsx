import type { Signal } from "../../types/lead";

interface Props {
  positive_signals: Signal[] | null;
  red_flags: Signal[] | null;
  data_gaps: string[] | null;
}

// A signal is either a plain string (today's shape) or a model-labelled
// object {label, text} (issue #200) -- never render the raw item directly,
// since a bare object as a React child throws.
function signalParts(item: Signal): { label: string | null; text: string } {
  if (typeof item === "string") return { label: null, text: item };
  const label = item.label?.trim() || null;
  return { label, text: item.text ?? "" };
}

export default function ReasoningBox({ positive_signals, red_flags, data_gaps }: Props) {
  return (
    <div className="space-y-2 text-xs">
      {positive_signals?.map((s, i) => {
        const { label, text } = signalParts(s);
        return (
          <div key={i} className="flex gap-2 text-success">
            <span className="mt-0.5 shrink-0">✓</span>
            <span>{label ? <><span className="font-medium">{label}:</span> {text}</> : text}</span>
          </div>
        );
      })}
      {red_flags?.map((f, i) => {
        const { label, text } = signalParts(f);
        return (
          <div key={i} className="flex gap-2 text-error">
            <span className="mt-0.5 shrink-0">✗</span>
            <span>{label ? <><span className="font-medium">{label}:</span> {text}</> : text}</span>
          </div>
        );
      })}
      {data_gaps?.map((g, i) => (
        <div key={i} className="flex gap-2 text-warning">
          <span className="mt-0.5 shrink-0">⚠</span>
          <span>{g}</span>
        </div>
      ))}
    </div>
  );
}
