import type { Briefing } from "../../types/briefing";
import ThemeItem from "./ThemeItem";

export default function BriefingPanel({ briefing }: { briefing: Briefing }) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-base font-semibold text-white mb-3">Top 5 Themes</h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {briefing.top_themes.map((t) => (
            <ThemeItem key={t.rank} theme={t} />
          ))}
        </div>
      </div>
      <div>
        <h2 className="text-base font-semibold text-white mb-3">Deep Dives</h2>
        <div className="space-y-4">
          {briefing.deep_dives.map((dd, i) => (
            <div key={i} className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3">
              <h4 className="text-sm font-semibold text-white">{dd.title}</h4>
              <p className="text-xs text-gray-400 leading-relaxed whitespace-pre-line">{dd.body}</p>
              {dd.sources.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {dd.sources.map((src, j) => (
                    <a
                      key={j}
                      href={src}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-brand hover:underline truncate max-w-xs"
                    >
                      {src}
                    </a>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
