import { useEffect, useState } from "react";

/** True below the `md` breakpoint (768 px): tables turn into cards. */
export function useIsMobile(query = "(max-width: 767px)") {
  const [mobile, setMobile] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = (e) => setMobile(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return mobile;
}
