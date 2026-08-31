"use client";

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const OVERLAY_ID = "catcher-overlay-root";

/**
 * 드로어와 모달을 화면 전환 래퍼 밖으로 빼낸다.
 *
 * 진입 애니메이션이 걸린 래퍼는 끝난 뒤에도 transform 이 남고,
 * transform 이 있는 조상은 `position: fixed` 의 기준점이 된다.
 * 그대로 두면 오버레이가 뷰포트가 아니라 페이지 전체를 기준으로 붙어
 * 스크롤을 내린 상태에서 열면 화면 밖에 그려진다.
 *
 * 토큰이 `.app` 안에만 정의돼 있으므로 body 가 아니라
 * `.app` 안쪽의 전용 컨테이너로 보낸다.
 */
export function Overlay({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);

  useEffect(() => {
    setHost(document.getElementById(OVERLAY_ID));
  }, []);

  if (!host) return null;
  return createPortal(children, host);
}
