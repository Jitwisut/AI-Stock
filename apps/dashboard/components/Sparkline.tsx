"use client";

import { useId, useMemo } from "react";

type SparklineProps = {
  /** แนวโน้มของชุดข้อมูล — ถ้าไม่ส่ง points มาจะสร้าง mock ให้ */
  points?: number[];
  /** 0–1 บอกทิศทางสี (override อัตโนมัติ) */
  trend?: "up" | "down" | "flat";
  width?: number;
  height?: number;
  strokeWidth?: number;
  className?: string;
  /** แสดงจุด dot ปลายสุด */
  showDot?: boolean;
  /** แสดงพื้นที่ใต้เส้น (gradient fill) */
  filled?: boolean;
};

/** สร้าง mock sparkline แบบ pseudo-random จาก symbol (deterministic) */
function generateMockPoints(seed: string, count = 24): number[] {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  const pts: number[] = [];
  let v = 50 + (h % 30);
  for (let i = 0; i < count; i++) {
    const r = ((h = (h * 1103515245 + 12345) >>> 0) % 1000) / 1000;
    v += (r - 0.45) * 6;
    v = Math.max(10, Math.min(95, v));
    pts.push(v);
  }
  return pts;
}

export function Sparkline({
  points,
  trend,
  width = 120,
  height = 36,
  strokeWidth = 1.5,
  className,
  showDot = true,
  filled = true
}: SparklineProps) {
  const id = useId().replace(/:/g, "");
  const data = useMemo(
    () => points && points.length > 1 ? points : generateMockPoints(id, 24),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [points?.join(",")]
  );

  const computedTrend = useMemo<"up" | "down" | "flat">(() => {
    if (trend) return trend;
    const first = data[0];
    const last = data[data.length - 1];
    const diff = ((last - first) / first) * 100;
    if (diff > 1.5) return "up";
    if (diff < -1.5) return "down";
    return "flat";
  }, [data, trend]);

  const stroke =
    computedTrend === "up"
      ? "#22C55E"
      : computedTrend === "down"
      ? "#EF4444"
      : "#94A3B8";

  // พื้นใต้เส้นเป็นสีทึบโปร่งบาง ๆ ไม่ใช่ไล่สี ให้เข้ากับสไตล์ทั้งเว็บ
  const areaFill =
    computedTrend === "up"
      ? "rgba(34,197,94,0.14)"
      : computedTrend === "down"
      ? "rgba(239,68,68,0.14)"
      : "rgba(148,163,184,0.12)";

  const { path, area, lastX, lastY } = useMemo(() => {
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const stepX = width / (data.length - 1);
    const padY = 3;
    const usableH = height - padY * 2;
    const coords = data.map((v, i) => {
      const x = i * stepX;
      const y = padY + usableH - ((v - min) / range) * usableH;
      return [x, y] as const;
    });
    const path = coords
      .map(([x, y], i) => (i === 0 ? `M${x},${y}` : `L${x},${y}`))
      .join(" ");
    const area =
      `M${coords[0][0]},${height} ` +
      coords.map(([x, y]) => `L${x},${y}`).join(" ") +
      ` L${coords[coords.length - 1][0]},${height} Z`;
    return {
      path,
      area,
      lastX: coords[coords.length - 1][0],
      lastY: coords[coords.length - 1][1]
    };
  }, [data, width, height]);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={`sparkline trend ${computedTrend}`}
    >
      {filled ? <path d={area} fill={areaFill} /> : null}
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {showDot ? (
        <g>
          <circle
            cx={lastX}
            cy={lastY}
            r={4}
            fill={stroke}
            fillOpacity={0.18}
          />
          <circle cx={lastX} cy={lastY} r={1.8} fill={stroke} />
        </g>
      ) : null}
    </svg>
  );
}
