import { NextResponse, type NextRequest } from "next/server";

// Conveniência de navegação, não segurança: quem não tem cookie de sessão vai direto para o login.
// Quem decide se a sessão vale é sempre a API (GET /auth/me), que a página consulta ao carregar.
const SESSION_COOKIE = "regista_session";
const PUBLIC_PREFIXES = ["/login", "/invite"];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isPublic = PUBLIC_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  if (!isPublic && !request.cookies.has(SESSION_COOKIE)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Fora: rewrite da API, arquivos estáticos e o ícone.
  matcher: ["/((?!api|_next/static|_next/image|icon.svg|favicon.ico).*)"],
};
