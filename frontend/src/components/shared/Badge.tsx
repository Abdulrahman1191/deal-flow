interface BadgeProps {
  label: string;
  variant?: "yes" | "maybe" | "reject" | "neutral" | "purple";
}

const variantClass: Record<string, string> = {
  yes: "bg-success/10 text-success border border-success/30",
  maybe: "bg-warning/10 text-warning border border-warning/30",
  reject: "bg-error/10 text-error border border-error/30",
  neutral: "bg-muted text-muted-foreground border border-border",
  purple: "bg-primary/10 text-primary border border-primary/30",
};

export default function Badge({ label, variant = "neutral" }: BadgeProps) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${variantClass[variant]}`}>
      {label}
    </span>
  );
}
