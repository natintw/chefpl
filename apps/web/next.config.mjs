/**
 * 瀏覽器只呼叫同網域的 /api/*；Next.js 在伺服器端轉給帳務損益系統 API。
 * Railway 上 LEDGER_API_URL 設為 http://ledger.railway.internal:8080（私有網路），
 * 所以後端不必對外開放、也不需要 CORS。
 * 注意：rewrites 在 build 時就寫進 .next，改 LEDGER_API_URL 之後要重新部署（Railway 的變數在 build 階段也拿得到）。
 * /integration/* 不轉發：那是系統對系統的端點，只給現金流系統走私有網路。
 */
const api = process.env.LEDGER_API_URL || "http://127.0.0.1:8080";

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  // 轉發到後端的等待上限（預設約 30 秒）。大檔匯入在跨區連線時可能超過，放寬到 2 分鐘
  experimental: { proxyTimeout: 120_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
