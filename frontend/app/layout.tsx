import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Chat2Order",
  description: "메신저 주문 자동 정리기",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-white text-gray-900">{children}</body>
    </html>
  );
}
