import { TrendingUp, TrendingDown, Minus } from "lucide-react";

type ScoreBadgeProps = {
  score: number;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
};

export function ScoreBadge({ score, size = "md", showLabel = false }: ScoreBadgeProps) {
  const tone =
    score >= 85
      ? { bg: "bg-gain/15", text: "text-gain", border: "border-gain/30", label: "แข็งแกร่ง" }
      : score >= 70
      ? { bg: "bg-amber/15", text: "text-amber", border: "border-amber/30", label: "น่าสนใจ" }
      : { bg: "bg-bg-raised", text: "text-ink-muted", border: "border-bg-line", label: "เฝ้าดู" };

  const sizes = {
    sm: "h-7 min-w-12 px-2 text-xs",
    md: "h-8 min-w-14 px-2.5 text-sm",
    lg: "h-10 min-w-16 px-3 text-base"
  } as const;

  return (
    <span
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg border font-mono font-semibold tabular-nums ${tone.bg} ${tone.text} ${tone.border} ${sizes[size]}`}
    >
      {Math.round(score)}
      {showLabel ? <span className="font-sans text-[10px] opacity-80">{tone.label}</span> : null}
    </span>
  );
}

type DeltaProps = {
  value: number;
  suffix?: string;
  className?: string;
};

export function Delta({ value, suffix = "%", className = "" }: DeltaProps) {
  const isUp = value > 0;
  const isDown = value < 0;
  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Minus;
  const tone = isUp ? "text-gain" : isDown ? "text-loss" : "text-ink-muted";

  return (
    <span
      className={`inline-flex items-center gap-1 font-mono font-semibold tabular-nums ${tone} ${className}`}
    >
      <Icon className="h-3.5 w-3.5" />
      {isUp ? "+" : ""}
      {value.toFixed(1)}
      {suffix}
    </span>
  );
}

export function DataSourceBadge({ source }: { source?: string | null }) {
  const normalized = (source || "unknown").toLowerCase();
  const isMock = normalized === "mock";
  const isUnknown = normalized === "unknown" || normalized === "unavailable";
  const label = isMock
    ? "Mock"
    : isUnknown
    ? "Unavailable"
    : `Live: ${sourceLabel(normalized)}`;
  const className = isMock
    ? "border-loss/30 bg-loss/10 text-loss"
    : isUnknown
    ? "border-amber/30 bg-amber/10 text-amber"
    : "border-gain/30 bg-gain/10 text-gain";

  return (
    <span className={`chip ${className}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}

function sourceLabel(source: string) {
  return source
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
