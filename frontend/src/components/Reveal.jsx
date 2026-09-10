import { useEffect, useRef, useState } from "react";

/**
 * Scroll-in reveal. Deliberately tiny and CSS-driven rather than pulling in an
 * animation library, and it renders the final state immediately when the
 * viewer has asked for reduced motion or IntersectionObserver is unavailable -
 * the content must never depend on the animation to become visible.
 */
export default function Reveal({ delay = 0, as: Tag = "div", className = "", children, ...props }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || typeof IntersectionObserver === "undefined") {
      setShown(true);
      return undefined;
    }
    const el = ref.current;
    if (!el) return undefined;

    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.05 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <Tag
      ref={ref}
      style={{ transitionDelay: shown ? `${delay}ms` : "0ms" }}
      className={
        "transition-[opacity,transform] duration-700 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none " +
        (shown ? "translate-y-0 opacity-100" : "translate-y-4 opacity-0") +
        " " + className
      }
      {...props}
    >
      {children}
    </Tag>
  );
}
