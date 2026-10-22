import type { Metadata, Viewport } from "next";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = { title: "帳務損益", description: "博竑・鄧師傅集團 帳務損益系統" };
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-Hant">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="stylesheet"
              href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;600;700&display=swap" />
      </head>
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
