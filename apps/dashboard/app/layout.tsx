import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { NavBar } from "../components/NavBar";

export const metadata: Metadata = {
  title: "AI Momentum Stock Radar",
  description: "เรดาร์สแกนหุ้น momentum ตลาดสหรัฐ สรุปเป็นภาษาไทย"
};

export default function RootLayout({
  children
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html lang="th">
      <body className="pb-16 sm:pb-0">
        <NavBar />
        {children}
      </body>
    </html>
  );
}
