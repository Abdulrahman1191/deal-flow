interface BadgeProps {
  label: string;
  variant?: "yes" | "maybe" | "reject" | "neutral" | "purple";
}

const variantClass: Record<string, string> = {
  yes: "bg-green-900/60 text-green-300 border border-green-700",
  maybe: "bg-yellow-900/60 text-yellow-300 border border-yellow-700",
  reject: "bg-red-900/60 text-red-300 border border-red-700",
  neutral: "bg-gray-800 text-gray-300 border border-gray-700",
  purple: "bg-purple-900/60 text-purple-300 border border-purple-700",
};

export default function Badge({ label, variant = "neutral" }: BadgeProps) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${variantClass[variant]}`}>
      {label}
    </span>
  );
}
