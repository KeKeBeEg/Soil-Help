/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // MapLibre ships its own worker; keep it out of the server bundle.
  transpilePackages: ['maplibre-gl'],
};

export default nextConfig;
