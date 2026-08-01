export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * ยิง API พร้อม retry — เซิร์ฟเวอร์บนโฮสต์ฟรีจะ "หลับ" เมื่อไม่มีคนใช้
 * คำขอแรกหลังตื่นมักได้ 502/503 หรือ network error ทำให้หน้าเว็บว่างเปล่า
 * จนกว่าผู้ใช้จะกดรีเฟรชเอง — retry สองสามครั้งช่วยให้รอบแรกก็ขึ้น
 */
export async function fetchJson<T>(
  path: string,
  options: { retries?: number; timeoutMs?: number } = {}
): Promise<T> {
  const { retries = 2, timeoutMs = 30_000 } = options;
  let lastError: Error = new Error("เรียก API ไม่สำเร็จ");

  for (let attempt = 0; attempt <= retries; attempt++) {
    // กันคำขอค้างไม่รู้จบเมื่อเซิร์ฟเวอร์ไม่ตอบ
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${API_BASE_URL}${path}`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (!response.ok) {
        // 5xx = เซิร์ฟเวอร์ยังไม่พร้อม ลองใหม่ได้; 4xx = ผิดที่คำขอ ไม่ต้องลองซ้ำ
        if (response.status >= 500 && attempt < retries) {
          lastError = new Error(`เซิร์ฟเวอร์ตอบ ${response.status}`);
          await delay(1500 * (attempt + 1));
          continue;
        }
        throw new Error(
          response.status === 503
            ? "ข้อมูลราคาถูกจำกัดชั่วคราว ลองใหม่อีกครั้งใน 1-2 นาที"
            : `เรียก API ไม่สำเร็จ (${response.status})`
        );
      }
      return (await response.json()) as T;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("เรียก API ไม่สำเร็จ");
      const aborted = lastError.name === "AbortError";
      if (attempt >= retries) {
        throw aborted ? new Error("เซิร์ฟเวอร์ไม่ตอบสนอง ลองใหม่อีกครั้ง") : lastError;
      }
      await delay(1500 * (attempt + 1));
    } finally {
      clearTimeout(timer);
    }
  }

  throw lastError;
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
