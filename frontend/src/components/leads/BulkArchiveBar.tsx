interface Props {
  count: number;
  total: number;
  allSelected: boolean;
  onSelectAll: () => void;
  onClear: () => void;
  onArchiveSelected: () => void;
  archiving: boolean;
  /** Admin "view as" QA mode (issue #52) — disables selection + the bulk action. */
  readOnly?: boolean;
}

export default function BulkArchiveBar({
  count,
  total,
  allSelected,
  onSelectAll,
  onClear,
  onArchiveSelected,
  archiving,
  readOnly = false,
}: Props) {
  if (total === 0) return null;

  return (
    <div className="flex items-center gap-3 flex-wrap bg-card border border-border rounded-xl px-4 py-2.5">
      <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer select-none">
        <input
          type="checkbox"
          checked={allSelected}
          onChange={() => (allSelected ? onClear() : onSelectAll())}
          disabled={readOnly}
          title={readOnly ? "Read-only while viewing another user's board" : undefined}
          className="h-3.5 w-3.5 rounded border-border accent-primary disabled:cursor-not-allowed"
        />
        {allSelected ? "Clear selection" : `Select all (${total})`}
      </label>
      {count > 0 && (
        <>
          <span className="text-xs text-muted-foreground">{count} selected</span>
          <button
            onClick={onArchiveSelected}
            disabled={archiving || readOnly}
            title={readOnly ? "Read-only while viewing another user's board" : undefined}
            data-testid="bulk-archive-btn"
            className="ml-auto px-3 py-1.5 text-xs font-medium rounded-lg bg-error/10 text-error hover:bg-error/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {archiving ? "Archiving…" : `Archive selected (${count})`}
          </button>
        </>
      )}
    </div>
  );
}
