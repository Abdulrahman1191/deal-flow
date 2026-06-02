interface Props {
  positive_signals: string[] | null;
  red_flags: string[] | null;
  data_gaps: string[] | null;
}

export default function ReasoningBox({ positive_signals, red_flags, data_gaps }: Props) {
  return (
    <div className="space-y-2 text-xs">
      {positive_signals?.map((s, i) => (
        <div key={i} className="flex gap-2 text-green-400">
          <span className="mt-0.5 shrink-0">✓</span>
          <span>{s}</span>
        </div>
      ))}
      {red_flags?.map((f, i) => (
        <div key={i} className="flex gap-2 text-red-400">
          <span className="mt-0.5 shrink-0">✗</span>
          <span>{f}</span>
        </div>
      ))}
      {data_gaps?.map((g, i) => (
        <div key={i} className="flex gap-2 text-yellow-400">
          <span className="mt-0.5 shrink-0">⚠</span>
          <span>{g}</span>
        </div>
      ))}
    </div>
  );
}
