"use client";

import { useMemo, useRef, useState } from "react";

/**
 * ราคา (พื้นที่) + วอลุ่ม (แท่ง) — แยกเป็นสองแผงที่ใช้แกนเวลาเดียวกัน
 *
 * ตั้งใจไม่วางสองชุดข้อมูลบนแกน y เดียวกัน เพราะการจับคู่สเกลราคา/วอลุ่มเป็นเรื่อง
 * ที่คนทำกราฟกำหนดเอง ทำให้กราฟ "สร้าง" ความสัมพันธ์ที่ไม่มีอยู่จริงในข้อมูล
 */

export type Bar = {
  date?: string | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close: number;
  volume?: number | null;
};

type ChartMode = "area" | "candle";

type Props = {
  bars: Bar[];
  height?: number;
  volumeHeight?: number;
  className?: string;
  /** โหมดที่หน้าเพจควบคุม — ปุ่มสลับอยู่นอกกราฟ (ดูหัวการ์ด) */
  mode?: ChartMode;
};

/** ใช้ตรวจจากฝั่งหน้าเพจว่าเปิดโหมดแท่งเทียนได้ไหม ก่อนจะโชว์ปุ่ม */
export function hasOhlcData(bars: Bar[]): boolean {
  const points = bars.filter((bar) => bar.close > 0);
  return (
    points.length > 1 &&
    points.every((bar) => bar.open != null && bar.high != null && bar.low != null)
  );
}

const PAD_X = 4;
const PAD_Y = 8;

function formatVolume(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K`;
  return String(value);
}

function formatDate(value?: string | null): string {
  if (!value) return "";
  const [month, day] = value.split("/");
  return month && day ? `${day}/${month}` : value;
}

export function PriceVolumeChart({
  bars,
  height = 190,
  volumeHeight = 54,
  className,
  mode = "area"
}: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  const points = useMemo(() => bars.filter((bar) => bar.close > 0), [bars]);
  const hasVolume = points.some((bar) => (bar.volume ?? 0) > 0);
  // แท่งเทียนต้องมี OHLC ครบ — provider สำรองบางตัวส่งมาแค่ราคาปิด
  const hasOhlc = points.every(
    (bar) => bar.open != null && bar.high != null && bar.low != null
  );
  const showCandles = mode === "candle" && hasOhlc;

  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const width = 600;
    // โหมดแท่งเทียนต้องกินช่วง high-low ทั้งหมด ไม่ใช่แค่ราคาปิด
    const values = showCandles
      ? points.flatMap((bar) => [bar.high ?? bar.close, bar.low ?? bar.close])
      : points.map((bar) => bar.close);
    const closes = points.map((bar) => bar.close);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = (width - PAD_X * 2) / (points.length - 1);
    const usableY = height - PAD_Y * 2;

    const toY = (value: number) => PAD_Y + usableY - ((value - min) / span) * usableY;
    const coords = points.map((bar, index) => {
      const x = PAD_X + index * stepX;
      return { x, y: toY(bar.close), bar };
    });

    const line = coords
      .map(({ x, y }, index) => (index === 0 ? `M${x},${y}` : `L${x},${y}`))
      .join(" ");
    const area = `${line} L${coords[coords.length - 1].x},${height} L${coords[0].x},${height} Z`;

    const maxVolume = Math.max(...points.map((bar) => bar.volume ?? 0), 1);
    const barWidth = Math.max(1.5, stepX * 0.62);

    return { width, coords, line, area, min, max, stepX, maxVolume, barWidth, toY };
  }, [points, height, showCandles]);

  if (!geometry) {
    return (
      <div
        className={`flex items-center justify-center text-xs text-ink-subtle ${className ?? ""}`}
        style={{ height }}
      >
        ไม่มีข้อมูลราคาย้อนหลัง
      </div>
    );
  }

  const { width, coords, line, area, min, max, maxVolume, barWidth, toY } = geometry;
  const first = coords[0].bar.close;
  const last = coords[coords.length - 1].bar.close;
  const up = last >= first;
  const stroke = up ? "rgb(var(--gain))" : "rgb(var(--loss))";
  const fill = up ? "rgb(var(--gain) / 0.12)" : "rgb(var(--loss) / 0.12)";

  const active = hover !== null ? coords[hover] : null;

  function handleMove(event: React.MouseEvent<HTMLDivElement>) {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const ratio = (event.clientX - rect.left) / rect.width;
    const index = Math.round(ratio * (coords.length - 1));
    setHover(Math.max(0, Math.min(coords.length - 1, index)));
  }

  const changePercent = first > 0 ? ((last - first) / first) * 100 : 0;

  return (
    <div
      ref={wrapRef}
      className={`relative ${className ?? ""}`}
      onMouseMove={handleMove}
      onMouseLeave={() => setHover(null)}
    >
      {/* ทิศทางบอกด้วยลูกศร+ตัวเลข ไม่ใช่สีอย่างเดียว — เขียวกับแดงที่ใช้อยู่
          ต่างกันแค่ ΔE 1.1 ในสายตาแบบ deuteranopia คือแยกไม่ออกเลย */}
      <div className="mb-1 flex items-baseline gap-1.5">
        <span
          className={`font-mono text-xs font-semibold tabular-nums ${
            up ? "text-gain" : "text-loss"
          }`}
        >
          {up ? "▲" : "▼"} {changePercent >= 0 ? "+" : ""}
          {changePercent.toFixed(1)}%
        </span>
        <span className="text-[10px] text-ink-subtle">ในช่วง {points.length} วันทำการ</span>
      </div>
      {/* แผงราคา */}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height }}
        role="img"
        aria-label={`กราฟราคา ${points.length} วันทำการ ตั้งแต่ ${first.toFixed(2)} ถึง ${last.toFixed(2)} ดอลลาร์`}
      >
        {/* เส้นกริดแนวนอนแบบจาง ๆ ให้พอกะระดับได้ */}
        {[0.25, 0.5, 0.75].map((ratio) => (
          <line
            key={ratio}
            x1={0}
            x2={width}
            y1={PAD_Y + (height - PAD_Y * 2) * ratio}
            y2={PAD_Y + (height - PAD_Y * 2) * ratio}
            stroke="rgb(var(--bg-line))"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {showCandles ? (
          coords.map(({ x, bar }, index) => {
            const open = bar.open ?? bar.close;
            const high = bar.high ?? bar.close;
            const low = bar.low ?? bar.close;
            const rising = bar.close >= open;
            const color = rising ? "rgb(var(--gain))" : "rgb(var(--loss))";
            const bodyTop = toY(Math.max(open, bar.close));
            const bodyBottom = toY(Math.min(open, bar.close));
            return (
              <g key={index} opacity={hover === null || hover === index ? 1 : 0.55}>
                {/* ไส้เทียน: ช่วงสูงสุด-ต่ำสุดของวัน */}
                <line
                  x1={x}
                  x2={x}
                  y1={toY(high)}
                  y2={toY(low)}
                  stroke={color}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                {/* ตัวเทียน: ช่วงราคาเปิด-ปิด (วันที่เท่ากันพอดีต้องเหลือเส้นบาง ๆ ให้เห็น) */}
                <rect
                  x={x - barWidth / 2}
                  y={bodyTop}
                  width={barWidth}
                  height={Math.max(bodyBottom - bodyTop, 1)}
                  fill={rising ? "none" : color}
                  stroke={color}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              </g>
            );
          })
        ) : (
          <>
            <path d={area} fill={fill} />
            <path
              d={line}
              fill="none"
              stroke={stroke}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          </>
        )}
        {active ? (
          <>
            <line
              x1={active.x}
              x2={active.x}
              y1={0}
              y2={height}
              stroke="rgb(var(--ink-subtle))"
              strokeWidth={1}
              strokeDasharray="3 3"
              vectorEffect="non-scaling-stroke"
            />
            {!showCandles ? (
              <>
                <circle cx={active.x} cy={active.y} r={4} fill={stroke} />
                <circle
                  cx={active.x}
                  cy={active.y}
                  r={4}
                  fill="none"
                  stroke="rgb(var(--bg-surface))"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
              </>
            ) : null}
          </>
        ) : null}
      </svg>

      {/* แผงวอลุ่ม — แกนเวลาเดียวกับแผงราคาด้านบน */}
      {hasVolume ? (
        <svg
          viewBox={`0 0 ${width} ${volumeHeight}`}
          preserveAspectRatio="none"
          className="mt-1 block w-full"
          style={{ height: volumeHeight }}
          role="img"
          aria-label={`กราฟวอลุ่มรายวัน สูงสุด ${formatVolume(maxVolume)} หุ้น`}
        >
          {coords.map(({ x, bar }, index) => {
            const value = bar.volume ?? 0;
            const barHeight = (value / maxVolume) * (volumeHeight - 4);
            const isActive = hover === index;
            return (
              <rect
                key={index}
                x={x - barWidth / 2}
                y={volumeHeight - barHeight}
                width={barWidth}
                height={Math.max(barHeight, 0.5)}
                rx={1}
                fill={isActive ? stroke : "rgb(var(--ink-subtle) / 0.45)"}
              />
            );
          })}
        </svg>
      ) : null}

      {/* ป้ายค่าสูง/ต่ำ — บอกช่วงราคาโดยไม่ต้องมีแกน y เต็ม */}
      <div className="pointer-events-none absolute right-0 top-0 text-[10px] tabular-nums text-ink-subtle">
        ${max.toFixed(2)}
      </div>
      <div
        className="pointer-events-none absolute right-0 text-[10px] tabular-nums text-ink-subtle"
        style={{ top: height - 14 }}
      >
        ${min.toFixed(2)}
      </div>

      {/* Tooltip */}
      {active ? (
        <div
          className="pointer-events-none absolute top-0 z-10 min-w-[128px] rounded-xl border border-bg-line bg-bg-surface px-2.5 py-1.5 text-[11px] shadow-lift"
          style={{
            left: `min(max(0px, ${(active.x / width) * 100}% - 64px), calc(100% - 128px))`
          }}
        >
          <div className="text-ink-subtle">{formatDate(active.bar.date)}</div>
          <div className="font-mono font-semibold text-ink">
            ${active.bar.close.toFixed(2)}
          </div>
          {showCandles && active.bar.open != null ? (
            <div className="font-mono text-[10px] leading-4 text-ink-muted">
              <div>เปิด ${active.bar.open.toFixed(2)}</div>
              <div>
                สูง ${active.bar.high?.toFixed(2)} · ต่ำ ${active.bar.low?.toFixed(2)}
              </div>
            </div>
          ) : null}
          {active.bar.volume ? (
            <div className="text-ink-muted">วอลุ่ม {formatVolume(active.bar.volume)}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
