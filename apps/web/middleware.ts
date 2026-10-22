/**
 * 暫時的密碼門：Google 登入完成前，設了 SITE_PASSWORD 就要求瀏覽器輸入帳密（HTTP Basic）。
 * 頁面與 /api/* 轉發都會經過這裡，所以拿到網址也看不到資料。
 * 帳號固定用 SITE_USER（預設 chefteng），密碼用 SITE_PASSWORD。換成 Google 登入後拿掉這兩個變數即可。
 */
import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const pass = process.env.SITE_PASSWORD;
  if (!pass) return NextResponse.next();
  const user = process.env.SITE_USER || "chefteng";
  const header = req.headers.get("authorization") || "";
  if (header.startsWith("Basic ")) {
    try {
      const [u, ...rest] = atob(header.slice(6)).split(":");
      if (u === user && rest.join(":") === pass) return NextResponse.next();
    } catch { /* 格式錯就當沒登入 */ }
  }
  return new NextResponse("需要登入", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="ledger", charset="UTF-8"' } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
