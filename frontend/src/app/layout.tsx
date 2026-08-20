import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Signal Trace · Customer Signal Intelligence",
  description: "Goal부터 공개 Fact와 Analysis Note까지 이어지는 고객 신호 분석 데모",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
