"use client";

/**
 * วงแหวน meter สำหรับ "ค่าหนึ่งค่าเทียบเพดาน" (0-100)
 * รางเป็นสีเดียวกับหลอดแต่จางกว่า เพื่อให้อ่านสถานะได้ทั้งวง
 */

type GaugeRingProps = {
  value: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  /** ตัวเลขใหญ่กลางวง — ปิดได้เมื่อใช้เป็นวงเล็กในลิสต์ */
  showValue?: boolean;
  /** หน่วยต่อท้ายตัวเลข เช่น "%" หรือ "/100" (คะแนน ไม่ใช่ความน่าจะเป็น) */
  unit?: string;
};

/**
 * ระดับความแรงของโอกาส — 3 ขั้นตามหลัก meter (เข้ม → เตือน → เงียบ)
 * รางเป็นสีเดียวกับหลอดแต่จาง เพื่อให้อ่านสถานะได้ทั้งวง
 *
 * เคยมีขั้นสีฟ้าคั่นระหว่างเขียวกับเหลือง แต่ในธีมสว่าง ฟ้า (#0E7490) กับ
 * เขียว (#15803D) ห่างกันแค่ ΔE 14.2 ซึ่งคนสายตาปกติก็แยกไม่ค่อยออก จึงตัดทิ้ง
 * ที่เหลือคู่ที่ใกล้สุดอยู่ในเกณฑ์ที่ยอมรับได้เมื่อมีตัวเลขกำกับ (ซึ่งมีทุกวง)
 */
function tone(value: number): { stroke: string; track: string } {
  if (value >= 70) return { stroke: "rgb(var(--gain))", track: "rgb(var(--gain) / 0.16)" };
  if (value >= 45) return { stroke: "rgb(var(--amber))", track: "rgb(var(--amber) / 0.18)" };
  return { stroke: "rgb(var(--ink-subtle))", track: "rgb(var(--ink-subtle) / 0.18)" };
}

export function GaugeRing({
  value,
  size = 132,
  strokeWidth = 10,
  label,
  showValue = true,
  unit = "%"
}: GaugeRingProps) {
  const safe = Math.max(0, Math.min(100, value));
  const { stroke, track } = tone(safe);

  // เว้นช่องล่าง 90° ให้เป็นวงแหวนเปิด อ่านง่ายกว่าวงกลมเต็ม
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const arcPortion = 0.75;
  const arcLength = circumference * arcPortion;
  const filled = arcLength * (safe / 100);

  return (
    <div
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${label ?? "ค่า"} ${Math.round(safe)}${unit === "%" ? " เปอร์เซ็นต์" : ` ${unit}`}`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        {/* หมุนให้ช่องว่างอยู่ด้านล่าง */}
        <g transform={`rotate(135 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={track}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={`${arcLength} ${circumference}`}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={stroke}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={`${filled} ${circumference}`}
            style={{ transition: "stroke-dasharray 400ms ease-out" }}
          />
        </g>
      </svg>

      {showValue ? (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          {/* ตัวเลขใช้สี ink ไม่ใช่สีของหลอด — สีทำหน้าที่บอกระดับอยู่แล้ว */}
          <span
            className="font-semibold leading-none text-ink"
            style={{ fontSize: size * 0.28 }}
          >
            {Math.round(safe)}
            <span className="text-ink-muted" style={{ fontSize: size * 0.14 }}>
              {unit}
            </span>
          </span>
          {label ? (
            <span
              className="mt-1 max-w-[86%] text-center leading-tight text-ink-subtle"
              style={{ fontSize: Math.max(9, size * 0.09) }}
            >
              {label}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
