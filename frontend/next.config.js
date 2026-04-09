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
        source: '/api/config/:path*',
        destination: `${process.env.BACKEND_URL || 'http://backend:8000'}/api/config/:path*`,
      },
      {
        source: '/health',
        destination: `${process.env.BACKEND_URL || 'http://backend:8000'}/health`,
      },
      {
        source: '/ready',
        destination: `${process.env.BACKEND_URL || 'http://backend:8000'}/ready`,
      },
    ]
  },
}

module.exports = nextConfig
