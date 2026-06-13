import type { ReactElement } from "react";

// Small inline icon set (lucide-style outlines, hand-drawn paths).
// Icons are always decorative: aria-hidden, paired with visible text labels.

export type IconName =
  | "home"
  | "play"
  | "history"
  | "shield"
  | "shield-check"
  | "shield-alert"
  | "shield-x"
  | "inbox"
  | "alert-triangle"
  | "alert-circle"
  | "check"
  | "check-circle"
  | "x-circle"
  | "octagon-x"
  | "octagon-alert"
  | "info"
  | "sparkles"
  | "plug-zap"
  | "file-question"
  | "chevron-down"
  | "chevron-right"
  | "download"
  | "copy"
  | "upload"
  | "file-text"
  | "table"
  | "braces"
  | "sheet"
  | "archive"
  | "file";

const octagon = (
  <polygon points="7.8 2 16.2 2 22 7.8 22 16.2 16.2 22 7.8 22 2 16.2 2 7.8" />
);

const shieldOutline = (
  <path d="M12 3l8 3v6c0 4.6-3.2 7.9-8 9-4.8-1.1-8-4.4-8-9V6l8-3z" />
);

const fileOutline = (
  <>
    <path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8l-5-5z" />
    <path d="M14 3v5h5" />
  </>
);

const ICONS: Record<IconName, ReactElement> = {
  home: (
    <>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </>
  ),
  play: <polygon points="6 4 20 12 6 20 6 4" />,
  history: (
    <>
      <path d="M3.5 8A9 9 0 1 1 3 12" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </>
  ),
  shield: shieldOutline,
  "shield-check": (
    <>
      {shieldOutline}
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>
  ),
  "shield-alert": (
    <>
      {shieldOutline}
      <line x1="12" y1="8" x2="12" y2="13" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </>
  ),
  "shield-x": (
    <>
      {shieldOutline}
      <path d="m9.5 9.5 5 5M14.5 9.5l-5 5" />
    </>
  ),
  inbox: (
    <>
      <polyline points="22 13 16 13 14 16 10 16 8 13 2 13" />
      <path d="M5.5 4.5h13L22 13v6a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-6l3.5-8.5z" />
    </>
  ),
  "alert-triangle": (
    <>
      <path d="M12 3 2 20h20L12 3z" />
      <line x1="12" y1="10" x2="12" y2="14" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </>
  ),
  "alert-circle": (
    <>
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="8" x2="12" y2="13" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </>
  ),
  check: <path d="m5 13 4 4L19 7" />,
  "check-circle": (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.5 2.5 4.5-5.5" />
    </>
  ),
  "x-circle": (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6M15 9l-6 6" />
    </>
  ),
  "octagon-x": (
    <>
      {octagon}
      <path d="m9.5 9.5 5 5M14.5 9.5l-5 5" />
    </>
  ),
  "octagon-alert": (
    <>
      {octagon}
      <line x1="12" y1="8" x2="12" y2="13" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="11" x2="12" y2="16" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z" />
      <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z" />
    </>
  ),
  "plug-zap": <path d="M13 2 7 13h4.5L11 22l6-11h-4.5L13 2z" />,
  "file-question": (
    <>
      {fileOutline}
      <path d="M10 12.5a2 2 0 1 1 3 1.6c-.6.4-1 .9-1 1.4" />
      <line x1="12" y1="18" x2="12.01" y2="18" />
    </>
  ),
  "chevron-down": <polyline points="6 9 12 15 18 9" />,
  "chevron-right": <polyline points="9 6 15 12 9 18" />,
  download: (
    <>
      <path d="M12 3v12" />
      <path d="m7 11 5 5 5-5" />
      <path d="M4 21h16" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <rect x="4" y="4" width="11" height="11" rx="2" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4" />
      <path d="m7 9 5-5 5 5" />
      <path d="M4 21h16" />
    </>
  ),
  "file-text": (
    <>
      {fileOutline}
      <path d="M9 12h6M9 16h6" />
    </>
  ),
  table: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M3 10h18M9 4v16M15 4v16" />
    </>
  ),
  braces: (
    <>
      <path d="M9 4c-2 0-2.5 1-2.5 3 0 2 0 3-2 5 2 2 2 3 2 5 0 2 .5 3 2.5 3" />
      <path d="M15 4c2 0 2.5 1 2.5 3 0 2 0 3 2 5-2 2-2 3-2 5 0 2-.5 3-2.5 3" />
    </>
  ),
  sheet: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="1.5" />
      <path d="M3 9h18M9 9v11" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8" />
      <path d="M10 12h4" />
    </>
  ),
  file: fileOutline,
};

export function Icon({
  name,
  size = 16,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {ICONS[name]}
    </svg>
  );
}
