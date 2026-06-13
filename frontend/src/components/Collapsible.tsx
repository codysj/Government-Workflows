import { useId, useState, type ReactNode } from "react";
import { Icon } from "./Icon";

/** Accessible expander: button with aria-expanded/aria-controls, chevron aria-hidden. */
export function Collapsible({
  header,
  children,
  defaultOpen = false,
  onFirstOpen,
  headingLevel = "h2",
  className,
}: {
  header: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  onFirstOpen?: () => void;
  headingLevel?: "h2" | "h3" | "none";
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [opened, setOpened] = useState(defaultOpen);
  const contentId = useId();

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !opened) {
      setOpened(true);
      onFirstOpen?.();
    }
  };

  const button = (
    <button
      type="button"
      className="collapsible-toggle"
      aria-expanded={open}
      aria-controls={contentId}
      onClick={toggle}
    >
      <Icon name={open ? "chevron-down" : "chevron-right"} size={16} />
      <span className="collapsible-header">{header}</span>
    </button>
  );

  return (
    <div className={`collapsible${className ? ` ${className}` : ""}`}>
      {headingLevel === "none" ? (
        button
      ) : headingLevel === "h2" ? (
        <h2 className="collapsible-heading">{button}</h2>
      ) : (
        <h3 className="collapsible-heading">{button}</h3>
      )}
      <div id={contentId} hidden={!open} className="collapsible-content">
        {children}
      </div>
    </div>
  );
}
