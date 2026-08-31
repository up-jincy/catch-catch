import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "고객 시그널 캐처 · Customer Signal Catcher",
  description: "흩어진 고객 행동을 하나의 시그널로 연결하는 Customer Intelligence Agent",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
