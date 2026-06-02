interface Props {
  score: number;
  bucket: string;
}

const colorClass: Record<string, string> = {
  YES: "bg-green-500",
  MAYBE: "bg-yellow-500",
  REJECT: "bg-red-500",
};

export default function ConfidenceBar({ score, bucket }: Props) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${colorClass[bucket] ?? "bg-gray-500"}`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs text-gray-400 w-8 text-right">{score}</span>
    </div>
  );
}
