import { useQuery } from "@tanstack/react-query";
import { fetchBriefings } from "../../api/briefings";
import { format } from "date-fns";

interface Props {
  onSelect: (date: string) => void;
  selectedDate?: string;
}

export default function BriefingArchive({ onSelect, selectedDate }: Props) {
  const { data } = useQuery({ queryKey: ["briefings"], queryFn: () => fetchBriefings() });

  return (
    <div className="space-y-1">
      <p className="text-xs text-gray-600 mb-2 uppercase tracking-wider">Archive</p>
      {data?.items.map((b) => (
        <button
          key={b.id}
          onClick={() => onSelect(b.date)}
          className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
            selectedDate === b.date
              ? "bg-gray-800 text-white"
              : "text-gray-400 hover:bg-gray-800/50 hover:text-gray-200"
          }`}
        >
          {format(new Date(b.date), "dd/MM/yyyy")}
        </button>
      ))}
    </div>
  );
}
