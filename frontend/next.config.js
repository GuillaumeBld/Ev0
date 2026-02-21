/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  experimental: {
    serverActions: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.BACKEND_URL || 'http://backend:8000'}/api/v1/:path*`,
      },
      {
        source: '/health',
        destination: `${process.env.BACKEND_URL || 'http://backend:8000'}/health`,
      },
    ]
  },
}

module.exports = nextConfig
