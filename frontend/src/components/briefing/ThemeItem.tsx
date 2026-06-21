import type { Theme } from "../../types/briefing";
import Badge from "../shared/Badge";

export default function ThemeItem({ theme }: { theme: Theme }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground font-mono">#{theme.rank}</span>
        <h4 className="text-sm font-semibold text-foreground">{theme.title}</h4>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">{theme.description}</p>
      <div className="flex flex-wrap gap-1">
        {theme.tags.map((tag) => (
          <Badge key={tag} label={tag} variant="neutral" />
        ))}
      </div>
      {theme.sources.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {theme.sources.map((src, i) => (
            <a
              key={i}
              href={src}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-primary hover:underline truncate max-w-xs"
            >
              {src}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
