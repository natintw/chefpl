/**
 * 1. 暫時的密碼門：Google 登入完成前，設了 SITE_PASSWORD 就要求瀏覽器輸入帳密（HTTP Basic）。
 *    頁面與 /api/* 轉發都會經過這裡，所以拿到網址也看不到資料。
 *    帳號用 SITE_USER（預設 chefteng）。換成 Google 登入後拿掉這兩個變數即可。
 * 2. 代理金鑰：後端有公開網址時（Render），設 LEDGER_PROXY_KEY，轉給後端的 /api/* 會帶上 X-Proxy-Key，
 *    後端 FRONTEND_PROXY_KEY 設同一個值，就只有這個前端進得去。金鑰只在伺服器端，瀏覽器看不到。
 */
import { NextRequest, NextResponse } from "next/server";

function passwordOk(req: NextRequest): boolean {
  const pass = process.env.SITE_PASSWORD;
  if (!pass) return true;
  const user = process.env.SITE_USER || "chefteng";
  const header = req.headers.get("authorization") || "";
  if (!header.startsWith("Basic ")) return false;
  try {
    const [u, ...rest] = atob(header.slice(6)).split(":");
    return u === user && rest.join(":") === pass;
  } catch {
    return false;
  }
}

export function middleware(req: NextRequest) {
  if (!passwordOk(req)) {
    return new NextResponse("需要登入", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="ledger", charset="UTF-8"' } });
  }
  const key = process.env.LEDGER_PROXY_KEY;
  if (key && req.nextUrl.pathname.startsWith("/api/")) {
    const headers = new Headers(req.headers);
    headers.set("x-proxy-key", key);
    headers.delete("authorization"); // 密碼門的帳密不必再傳給後端
    return NextResponse.next({ request: { headers } });
  }
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
