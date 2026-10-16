import { useState, useEffect } from "react";

/** True when the viewport is narrower than Tailwind's `md` breakpoint (768px).
 *  Returns false during SSR; safe to call from `'use client'` components. */
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  return isMobile;
}
