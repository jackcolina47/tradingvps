import { useEffect, useState } from "react";

/** True below the `md` breakpoint: wide tables switch to stacked cards. */
export function useIsMobile(max = 767) {
  const q = `(max-width: ${max}px)`;
  const [mobile, setMobile] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setMobile(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [q]);
  return mobile;
}
