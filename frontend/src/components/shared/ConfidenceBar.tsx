interface Props {
  score: number;
  bucket: string;
}

const colorClass: Record<string, string> = {
  YES: "bg-success",
  MAYBE: "bg-warning",
  REJECT: "bg-error",
};

export default function ConfidenceBar({ score, bucket }: Props) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${colorClass[bucket] ?? "bg-muted-foreground"}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs text-muted-foreground w-8 text-right">{score}</span>
    </div>
  );
}
