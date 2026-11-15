'use client';

import { Mark } from './Icons';

/**
 * The loading screen: the live globe, with the logo over it.
 *
 * There is no artwork here and no progress bar. The map itself is the
 * loading screen — it sits behind this overlay from the first frame, so the
 * moment the overlay lifts and the globe turns, the app is simply already
 * there. A static image could never line up with the WebGL globe across
 * every screen size; this cannot fail to.
 */
export default function Splash({ hidden }: { hidden: boolean }) {
  return (
    <div className={`splash${hidden ? ' hidden' : ''}`} aria-hidden={hidden}>
      <div className="splash-veil" />
      <div className="splash-logo">
        <Mark size={30} />
        <span className="splash-name">Soil Help</span>
      </div>
    </div>
  );
}
