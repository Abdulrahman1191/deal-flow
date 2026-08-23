interface Props {
  checked: boolean;
  onChange: () => void;
  readOnly?: boolean;
  label: string;
}

/** Bulk-select checkbox rendered on board cards (issue #141). Stops the
 * click from bubbling into the card's own expand/collapse handlers. */
export default function SelectCheckbox({ checked, onChange, readOnly = false, label }: Props) {
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={onChange}
      onClick={(e) => e.stopPropagation()}
      disabled={readOnly}
      title={readOnly ? "Read-only while viewing another user's board" : label}
      aria-label={label}
      data-testid="lead-select-checkbox"
      className="h-3.5 w-3.5 mt-0.5 rounded border-border accent-primary shrink-0 disabled:cursor-not-allowed"
    />
  );
}
