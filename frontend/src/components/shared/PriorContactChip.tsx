function priorContactTooltip(
  count: number | null | undefined,
  lastAt: string | null | undefined,
): string | undefined {
  if (!count && !lastAt) return undefined;
  const parts: string[] = [];
  if (count) parts.push(`${count} prior email${count === 1 ? "" : "s"}`);
  if (lastAt) {
    parts.push(
      `last on ${new Date(lastAt).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })}`,
    );
  }
  return parts.join(" · ");
}

interface Props {
  priorContact: boolean | null | undefined;
  priorContactCount?: number | null;
  priorContactLastAt?: string | null;
}

export default function PriorContactChip({
  priorContact,
  priorContactCount,
  priorContactLastAt,
}: Props) {
  if (priorContact === true) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-medium text-info bg-info/10 border border-info/30 rounded px-1.5 py-0.5 mt-0.5 w-fit"
        title={priorContactTooltip(priorContactCount, priorContactLastAt)}
        data-testid="prior-contact-badge"
      >
        ✉ Prior contact
      </span>
    );
  }
  if (priorContact === false) {
    return (
      <span
        className="inline-block text-[10px] text-muted-foreground/70 mt-0.5"
        data-testid="prior-contact-badge"
      >
        No prior contact
      </span>
    );
  }
  return null;
}
