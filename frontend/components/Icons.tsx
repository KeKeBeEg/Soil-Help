/** Inline stroke icons. Kept local so the app pulls in no icon library. */
import type { SVGProps } from 'react';

type P = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: P) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const ChevronLeft = (p: P) => <Svg {...p}><path d="M15 18l-6-6 6-6" /></Svg>;
export const ChevronRight = (p: P) => <Svg {...p}><path d="M9 18l6-6-6-6" /></Svg>;
export const ChevronDown = (p: P) => <Svg {...p}><path d="M6 9l6 6 6-6" /></Svg>;
export const Plus = (p: P) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>;
export const Minus = (p: P) => <Svg {...p}><path d="M5 12h14" /></Svg>;
export const X = (p: P) => <Svg {...p}><path d="M18 6L6 18M6 6l12 12" /></Svg>;

export const Check = (p: P) => (
  <Svg strokeWidth={3} {...p}><path d="M20 6L9 17l-5-5" /></Svg>
);

export const Search = (p: P) => (
  <Svg {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></Svg>
);

export const Profile = (p: P) => (
  <Svg {...p}><path d="M2 17l5-7 4 5 3-4 3 3 5-8" /></Svg>
);

export const Polygon = (p: P) => (
  <Svg {...p}>
    <path d="M12 3l8 5-3 10H7L4 8z" />
    <circle cx="12" cy="3" r="1.4" fill="currentColor" />
    <circle cx="20" cy="8" r="1.4" fill="currentColor" />
    <circle cx="4" cy="8" r="1.4" fill="currentColor" />
  </Svg>
);

export const Locate = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="7" />
    <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
  </Svg>
);

/** The needle rotates with the map bearing, so it is split from the ring. */
export const CompassRing = (p: P) => (
  <Svg {...p}><circle cx="12" cy="12" r="9.2" strokeWidth={1.6} /></Svg>
);

export const CompassNeedle = (p: P) => (
  <Svg {...p}>
    <path d="M12 4.5l3 8.5h-6z" fill="#e5484d" stroke="#e5484d" strokeWidth={1.2} />
    <path d="M12 19.5l-3-6.5h6z" fill="currentColor" stroke="currentColor" strokeWidth={1.2} />
  </Svg>
);

export const Globe = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a15 15 0 010 18 15 15 0 010-18" />
  </Svg>
);

export const Sun = (p: P) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Svg>
);

export const Download = (p: P) => (
  <Svg {...p}><path d="M12 3v12M7 11l5 5 5-5M4 21h16" /></Svg>
);

export const Undo = (p: P) => (
  <Svg {...p}><path d="M9 14L4 9l5-5" /><path d="M4 9h11a5 5 0 010 10h-4" /></Svg>
);

// ------------------------------------------------- Google-Earth-style chrome

export const Redo = (p: P) => (
  <Svg {...p}><path d="M15 14l5-5-5-5" /><path d="M20 9H9a5 5 0 000 10h4" /></Svg>
);

export const Pin = (p: P) => (
  <Svg {...p}>
    <path d="M12 21s7-6.3 7-11.5A7 7 0 005 9.5C5 14.7 12 21 12 21z" />
    <circle cx="12" cy="9.5" r="2.4" />
  </Svg>
);

export const Layers = (p: P) => (
  <Svg {...p}>
    <path d="M12 3l9 5-9 5-9-5 9-5z" />
    <path d="M3 13l9 5 9-5" />
  </Svg>
);

export const Eye = (p: P) => (
  <Svg {...p}>
    <path d="M2 12s3.8-6.5 10-6.5S22 12 22 12s-3.8 6.5-10 6.5S2 12 2 12z" />
    <circle cx="12" cy="12" r="2.8" />
  </Svg>
);

export const EyeOff = (p: P) => (
  <Svg {...p}>
    <path d="M10.6 6.1A9.9 9.9 0 0112 6c6.2 0 10 6 10 6a18 18 0 01-3.3 3.9M6.4 6.5A17.6 17.6 0 002 12s3.8 6.5 10 6.5a10 10 0 004-.8" />
    <path d="M3 3l18 18" />
  </Svg>
);

export const Trash = (p: P) => (
  <Svg {...p}><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13" /></Svg>
);

export const Info = (p: P) => (
  <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 7.6v.4" /></Svg>
);

export const Sidebar = (p: P) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9.5 4v16" />
  </Svg>
);

/** Brand mark: a thin globe with a horizon line. Monochrome, no tile. */
export const Mark = ({ size = 20 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={1.5}
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="9" />
    <ellipse cx="12" cy="12" rx="4" ry="9" />
    <path d="M3.3 9h17.4M3.3 15h17.4" />
  </svg>
);
