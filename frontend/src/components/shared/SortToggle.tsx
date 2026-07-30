export type SortOrder = "newest" | "oldest";

interface Props {
  value: SortOrder;
  onChange: (value: SortOrder) => void;
}

export default function SortToggle({ value, onChange }: Props) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as SortOrder)}
      aria-label="Sort by date"
      className="bg-card border border-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-ring transition-colors"
    >
      <option value="newest">Newest first</option>
      <option value="oldest">Oldest first</option>
    </select>
  );
}
