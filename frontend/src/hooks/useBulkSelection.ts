import { useCallback, useEffect, useState } from "react";

/** Checkbox-multi-select over a list of ids that changes as the board
 * refetches — prunes any selected id that's dropped out of `ids` (e.g. a
 * lead archived elsewhere) so the displayed count stays accurate. */
export function useBulkSelection(ids: string[]) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    setSelected((prev) => {
      const idSet = new Set(ids);
      let changed = false;
      const next = new Set<string>();
      prev.forEach((id) => {
        if (idSet.has(id)) next.add(id);
        else changed = true;
      });
      return changed ? next : prev;
    });
  }, [ids]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => setSelected(new Set(ids)), [ids]);
  const clear = useCallback(() => setSelected(new Set()), []);

  const allSelected = ids.length > 0 && ids.every((id) => selected.has(id));

  return { selected, toggle, selectAll, clear, allSelected };
}
