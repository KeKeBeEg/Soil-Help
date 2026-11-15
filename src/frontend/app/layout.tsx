import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Soil Help — Field Viewer',
  description:
    'A 3D satellite and terrain viewer for farm fields: draw a field boundary, read an elevation profile, and inspect the slope of the land.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  themeColor: '#0d1117',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="tr">
      <head>
        {/* The wordmark on the loading screen is set in Inter. Without a
            preload the fallback face paints first and the word visibly
            changes width mid-animation when the font arrives. */}
        <link
          rel="preload"
          href="/fonts/inter-latin-wght-normal.woff2"
          as="font"
          type="font/woff2"
          crossOrigin="anonymous"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
