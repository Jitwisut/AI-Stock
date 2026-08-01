import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { NavBar } from "../components/NavBar";

export const metadata: Metadata = {
  title: "AI Momentum Stock Radar",
  description: "เรดาร์สแกนหุ้น momentum ตลาดสหรัฐ สรุปเป็นภาษาไทย"
};

/**
 * ตั้งธีมก่อนเบราว์เซอร์วาดหน้าแรก — ถ้ารอ React mount ผู้ใช้ธีมสว่างจะเห็น
 * หน้าจอมืดแวบหนึ่งทุกครั้งที่โหลด (flash of wrong theme)
 */
const THEME_INIT = `
(function() {
  try {
    var stored = localStorage.getItem('theme');
    var prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    document.documentElement.dataset.theme = stored || (prefersLight ? 'light' : 'dark');
  } catch (e) {
    document.documentElement.dataset.theme = 'dark';
  }
})();
`;

export default function RootLayout({
  children
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="th" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body className="pb-16 sm:pb-0">
        <NavBar />
        {children}
      </body>
    </html>
  );
}
