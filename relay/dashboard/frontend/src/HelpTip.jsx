import { useEffect, useId, useRef, useState } from "react";

import { GLOSSARY } from "./glossary";

// A small "?" that opens a plain-language explanation of the term next to
// it. Click or keyboard to open; click outside or Escape to close.
export default function HelpTip({ term }) {
  const entry = GLOSSARY[term];
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState(null);
  const wrapperRef = useRef(null);
  const popoverId = useId();

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const onPointerDown = (event) => {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    // The popover is fixed to the viewport, so it would drift on scroll.
    const onScroll = () => setOpen(false);

    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  if (!entry) {
    return null;
  }

  return (
    <span className="help-tip" ref={wrapperRef}>
      <button
        type="button"
        className="help-tip-button"
        aria-label={`What is ${entry.title}?`}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={(event) => {
          event.stopPropagation();
          // Fixed to the viewport, so a scrolling table cannot clip it.
          const rect = event.currentTarget.getBoundingClientRect();
          const width = Math.min(320, window.innerWidth - 24);
          setPosition({
            top: rect.bottom + 8,
            left: Math.max(12, Math.min(rect.left - 12, window.innerWidth - width - 12)),
            width,
          });
          setOpen((value) => !value);
        }}
      >
        ?
      </button>
      {open && (
        <span
          className="help-tip-popover"
          role="dialog"
          id={popoverId}
          style={position ?? undefined}
        >
          <strong>{entry.title}</strong>
          <span>{entry.body}</span>
        </span>
      )}
    </span>
  );
}
